"""Scraper factory — turn firms without a bespoke scraper into firms with one.

Orchestrates gen -> guard -> commit for the pipeline's discover mode:

  for each target firm (no scripts/<slug>_scraper.py):
      1. generate module code via the Claude API        [scraper_gen]
      2. static AST guard                                [scraper_guard.static_check]
      3. run scrape() in a token-free sandbox            [scraper_guard.run_sandboxed]
      4. validate the records are real + rich            [scraper_guard.validate_output]
      5. only then: write scripts/<slug>_scraper.py (with the trusted runnable
         footer), commit scraper + refreshed dataset to GitHub.

Steps 1-4 run as a BURST: up to GEN_MAX_ATTEMPTS tries in the same run (default
4), on an escalating model — cheap for the early tries, strong for the last two,
see scraper_gen.model_for — each later try prompted with the burst's earlier
failures (labelled with the model that produced them) so it varies its approach
(different on-page data source, fixed validation gap). A firm therefore leaves
the queue the night it is first tried — bespoke on success, retired (gen_state
exhaustion) on failure — so no rolling backlog forms. API-transport errors are
the exception: they abort the burst uncounted and the firm retries next run.

A committed scraper makes the firm 'bespoke' from the next deploy on (Railway
rebuilds on the push), so the monthly refresh re-scrapes it like the original 47.
Attempts are capped per run (GEN_MAX_PER_RUN, default 3) to bound API cost, and
remembered across runs in data/gen_attempts.json (see gen_state): after
GEN_MAX_ATTEMPTS counted failures — or a manual "skip": true — a firm is
excluded from targets() instead of being retried forever.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import extract
import scraper_gen
import scraper_guard
import tags

_REPO = Path(__file__).resolve().parent.parent
_SCRIPTS = _REPO / "scripts"
_DATA = _REPO / "data"


def targets(roster_firms: list, state: dict | None = None) -> list:
    """Firms eligible for generation, in priority order:

    1. repo firms with a dataset but NO bespoke scraper — the thin generic ones
       (Amplify, Homebrew, …). Proven scrapeable, so generation is likeliest to
       succeed, and their incomplete JSON gets REPLACED by the rich dataset.
    2. this run's generic failures (needs-scraper candidates).

    Capped by the caller, so the backlog drains a few firms per nightly run.

    `state` is the gen_state attempt memory (data/gen_attempts.json): firms
    that exhausted their attempt budget or carry a manual "skip": true — the
    legitimately-thin, leave-it-alone flag — are filtered OUT here, before the
    caller's [:GEN_MAX_PER_RUN] slice, so they can't pin the nightly slots."""
    import gen_state
    import names as _names

    if state is None:
        state = gen_state.load()

    out, seen = [], set()

    # 1. thin datasets already in the repo (portfolio URL read from the dataset).
    # The narrow glob is DELIBERATE here, unlike elsewhere: it excludes
    # companies.json, and Lightspeed's 425 hand-built records must never be
    # replaced by generated output. Don't "fix" this to use identity.
    for p in sorted(_DATA.glob("*_companies.json")):
        slug = p.name[: -len("_companies.json")]
        if (_SCRIPTS / f"{slug}_scraper.py").exists():
            continue
        f = _Target(
            slug=slug, data_file=p.name, kind="generic",
            firm_name=_names.display_name(p.name),
            homepage=None, portfolio_url=_names.source_url(p.name),
        )
        out.append(f)
        seen.add(slug)

    # 2. candidates processed this run that still have no dataset
    for f in roster_firms:
        if (f.kind == "generic" and f.slug not in seen
                and not (_SCRIPTS / f"{f.slug}_scraper.py").exists()):
            out.append(f)

    # attempt-memory filter (both kinds), preserving priority order
    kept = []
    for f in out:
        ok, why = gen_state.eligible(state, f.slug)
        if ok:
            kept.append(f)
        else:
            print(f"[factory] skipping {f.slug}: {why}")
    return kept


class _Target:
    """Lightweight stand-in for roster.Firm for repo-derived targets."""
    def __init__(self, **kw):
        self.__dict__.update(kw)


def attempt(firm, store, tries: int = 1) -> dict:
    """Burst-try one firm: up to `tries` generations in THIS run, each later try
    shown the earlier failures (reason + last code) so it varies its approach
    instead of resampling the same one. The site context is fetched once and
    reused across the burst. Returns {slug, ok, records|None, reason, failures,
    terminal} where `failures` lists every failed try, oldest first, for the
    caller to record in gen_state. An API-transport error aborts the burst (its
    "generation error: …" entry is uncounted there, so it retries next run).
    `terminal` True means no retry can ever change the outcome — the caller
    should retire the firm tonight rather than let it burn attempts.
    Commits scraper+data on success ONLY when `store` is passed — the pipeline
    passes store=None and defers commits to end-of-run (a mid-run push triggers
    a Railway redeploy that kills the running container; see
    pipeline._flush_factory_commits)."""
    slug, data_file = firm.slug, firm.data_file
    res = {"slug": slug, "ok": False, "records": None, "reason": "",
           "failures": [], "terminal": False}

    def _fail(reason: str, code: str | None = None, abort: bool = False,
              model: str | None = None):
        res["failures"].append(reason)
        res["reason"] = reason
        if not abort:
            # `model` is carried so the next try's prompt can say who wrote the
            # code it is being shown — passed explicitly rather than captured
            # from the loop, so a later reordering can't silently mislabel it.
            prior.append({"reason": reason, "code": code, "model": model})

    url = firm.portfolio_url or (
        extract.resolve_portfolio_url(firm.homepage) if firm.homepage else None)
    if not url:
        # TERMINAL (user decision 2026-07-27): with no portfolio page there is
        # nothing to hand the generator, so a "retry" is just the same URL
        # resolution against the same JS shell — it cannot succeed where this
        # one failed. Retire tonight instead of burning 4 counted no-ops
        # across ~2 weeks while Airtable keeps saying "needs-scraper".
        res["failures"].append("no portfolio url")
        res["reason"] = "no portfolio url"
        res["terminal"] = True
        return res

    context = scraper_gen.build_context(firm.firm_name or slug, slug, url)
    if not context:
        res["failures"].append("portfolio page unreachable")
        res["reason"] = "portfolio page unreachable"
        return res

    # baseline = what the generic extractor got, if anything (read once)
    baseline = 0
    if (_DATA / data_file).exists():
        try:
            baseline = len(json.loads((_DATA / data_file).read_text()))
        except Exception:  # noqa: BLE001
            baseline = 0

    prior: list = []                       # [{"reason", "code"}] for feedback
    code = None
    tries = max(1, tries)
    for _t in range(tries):
        # 1. generate (feedback from this burst's earlier tries, if any).
        # Model escalates: cheap early, strong for the last two, so a firm is
        # never retired without the strong model having had a real go at it.
        model = scraper_gen.model_for(_t, tries)
        try:
            code = scraper_gen.generate(firm.firm_name or slug, slug, url,
                                        context=context, failures=prior,
                                        model=model)
        except Exception as exc:  # noqa: BLE001 — API/transport: abort burst
            _fail(f"generation error: {exc}", abort=True)
            return res
        if not code:
            _fail("no code generated", model=model)
            continue

        # 2. static guard
        problems = scraper_guard.static_check(code)
        if problems:
            _fail("static guard: " + "; ".join(problems[:4]), code, model=model)
            continue

        # 3. sandboxed run (no tokens in env)
        cand = _DATA.parent / "automation" / f".candidate_{slug}.py"
        cand.write_text(code + "\n")
        try:
            records, err = scraper_guard.run_sandboxed(str(cand))
        finally:
            try:
                cand.unlink()
            except OSError:
                pass
        if records is None:
            _fail(f"sandbox: {err}", code, model=model)
            continue

        # 4. validate output
        fails = scraper_guard.validate_output(records, baseline)
        if fails:
            _fail("validation: " + "; ".join(fails), code, model=model)
            continue
        break                              # all gates passed
    else:
        return res                         # burst exhausted without success

    # 5. persist: scraper file + dataset, committed to GitHub
    final_code = scraper_gen.with_footer(code, data_file)
    (_SCRIPTS / f"{slug}_scraper.py").write_text(final_code)
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    for r in records:
        r.setdefault("source_url", url)
        r.setdefault("scraped_at", now)
    # Tag before persist — the tags are the product (the dashboard agent
    # builds comps from everywhere_tags; see tags.py). Generated scrapers
    # never tag, so this fill is what makes a graduate useful downstream.
    tags.fill_empty(records)
    for r in records:
        r.setdefault("everywhere_tags", [])     # explicit [] for stragglers
    (_DATA / data_file).write_text(
        json.dumps(records, indent=2, ensure_ascii=False) + "\n")

    if store is not None:
        store.commit_json(data_file, records,
                          f"Auto-generated rich dataset: {slug} ({len(records)} companies)")
        _commit_text(store, f"scripts/{slug}_scraper.py", final_code,
                     f"Auto-generated scraper: {slug} (guard+validation passed)")

    res.update(ok=True, records=records,
               reason=(f"OK ({len(records)} records, "
                       f"try {len(res['failures']) + 1}/{max(1, tries)})"))
    return res


def _commit_text(store, path: str, content: str, message: str) -> None:
    """Commit a non-data file via the same Contents API the store uses."""
    import base64
    import requests as rq
    url = f"https://api.github.com/repos/{store.repo}/contents/{path}"
    payload = {"message": message, "branch": store.branch,
               "content": base64.b64encode(content.encode()).decode()}
    r = store._s.get(url, params={"ref": store.branch}, timeout=30)
    if r.status_code == 200:
        payload["sha"] = r.json().get("sha")
    rq.put(url, headers=store._s.headers, json=payload, timeout=30).raise_for_status()
