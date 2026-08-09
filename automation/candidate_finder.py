"""Candidate finder — keep the discovery queue topped up without a human.

candidates.json was a hand-written starter list, so discovery could only ever
process firms someone had already thought of; once the factory drains it, the
nightly run has nothing new to do. This module refills the queue on every
discover pass: ask Claude for VC firms we don't have, VERIFY each one against
its live site, and append the survivors.

Nothing proposed is trusted. A model can invent a plausible firm or attach the
wrong domain to a real one, so every proposal must clear `verify()` — the
homepage has to actually serve HTML, the page has to mention the firm, and it
has to read like a VC site — before it enters the queue. A proposal is a LEAD,
not data; the repo's never-fabricate rule is upheld by the fact that no
proposed text ever reaches a dataset. Only the firm name and a verified URL
survive, and the companies themselves still come from the firm's own site via
the normal extractor / factory path.

State: data/discovered_candidates.json, a JSON list committed through the same
Contents API as the datasets (GitHubStore.read_json only round-trips lists), so
ephemeral Railway containers read back what earlier runs found:

    [{"firm_name": "Example Ventures", "homepage": "example.vc",
      "portfolio_url": "https://example.vc/portfolio",   # or null
      "status": "queued" | "rejected", "reason": "...",
      "found_at": "2026-07-27T07:00:00+00:00"}]

Rejected entries are KEPT on purpose: they are the memory that stops the model
re-proposing the same dead domain — and paying to re-verify it — every night.
Same bargain as gen_state's retirement, one layer earlier in the funnel.

It lives in data/ rather than in automation/ for a deployment reason: Railway's
Watch Paths cover automation/**, so committing the queue there would redeploy
the discovery service and kill the very run that wrote it (the failure mode
that forced pipeline._flush_factory_commits). data/ is outside those paths, so
this commit is safe to make mid-run.

Env: ANTHROPIC_API_KEY (required — no key, no finding), FIND_MAX_PER_RUN
(default 1 firm added per run), FIND_MAX_OPEN (default 25 unprocessed
candidates before the finder pauses), FIND_PROPOSALS (default 12 asked per
call, since most get rejected), FIND_MODEL (default claude-sonnet-4-5).

Run standalone:  python3 automation/candidate_finder.py --dry-run
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import requests

import budget
import extract
import identity

FILE = "discovered_candidates.json"
_DATA = Path(__file__).resolve().parent.parent / "data"
_CANDIDATES = Path(__file__).resolve().parent / "candidates.json"
_API = "https://api.anthropic.com/v1/messages"

# Firms already checked by hand and confirmed to publish NO portfolio on their
# own site (CLAUDE.md). Nothing here is scrapeable under the no-third-party
# rule, so they are excluded in the prompt AND auto-rejected if proposed anyway.
_NO_PORTFOLIO = [
    "Benchmark", "Thrive Capital", "DST Global", "Tiger Global",
    "Altimeter Capital", "Sutter Hill Ventures", "Greenoaks",
]

# Words that carry no identifying signal when matching a firm name to its site.
_GENERIC_TOKENS = {
    "ventures", "venture", "capital", "partners", "partner", "fund", "funds",
    "group", "management", "holdings", "investments", "the", "and",
}
_VC_MARKERS = ("portfolio", "invest", "founders", "companies", "fund", "venture")

# A second, browser-shaped User-Agent used ONLY for the finder's liveness check,
# and only after the polite `vc-comps-pipeline/1.0` request has already failed.
#
# Added when Atomico, Balderton and Point Nine — all real firms with working
# sites — came back "homepage unreachable" in one run. UNPROVEN: a later probe
# of 500.co, Sapphire and GGV found the polite agent working everywhere, so that
# night was more likely a transient outage than bot protection. Kept because the
# downside is one extra request on a path that had already failed, and some
# sites genuinely do 403 unrecognised agents. Delete it if it never earns its
# keep — the load-bearing fix for that night was NOT recording "unreachable" as
# a permanent rejection.
#
# Scraping still uses the polite agent everywhere; this decides only whether a
# firm exists, never what its data is.
_BROWSER_UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                             "AppleWebKit/537.36 (KHTML, like Gecko) "
                             "Chrome/126.0.0.0 Safari/537.36",
               "Accept": "text/html,application/xhtml+xml"}


def _browser_fetch(url: str) -> str | None:
    try:
        r = requests.get(url, headers=_BROWSER_UA, timeout=20)
    except requests.RequestException:
        return None
    if r.status_code == 200 and "html" in r.headers.get("content-type", ""):
        return r.text
    return None

_PROMPT = """Name {n} venture capital firms that publish their portfolio companies \
on their OWN website.

Rules:
- Real, currently-active VC firms only.
- `homepage` must be the firm's real primary domain. If you are not confident of \
the exact domain, OMIT the firm entirely — a wrong domain is worse than a short list.
- Skip firms whose portfolio appears only on Crunchbase/LinkedIn/PitchBook, and \
firms that publish no portfolio at all.
- Prefer firms with 30+ portfolio companies listed on their own site.
- Vary the list: different geographies, stages (pre-seed through growth) and \
sector focuses, not only well-known Silicon Valley names.

Already covered — do NOT name any of these:
{exclude}

Return ONLY a JSON array, no prose:
[{{"firm_name": "Example Ventures", "homepage": "example.vc"}}]
"""


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


# ------------------------------------------------------------------ queue state
def load(store=None) -> list[dict]:
    """The queue as a list. Prefers the repo copy (Railway containers are
    ephemeral and may be running an older checkout); falls back to the local
    file; tolerates absence and garbage — a broken queue must degrade to 'no
    memory', never kill the run."""
    raw = None
    if store is not None:
        try:
            raw = store.read_json(FILE)
        except Exception:  # noqa: BLE001
            raw = None
    if raw is None:
        try:
            raw = json.loads((_DATA / FILE).read_text())
        except Exception:  # noqa: BLE001
            raw = []
    return [e for e in (raw if isinstance(raw, list) else [])
            if isinstance(e, dict) and e.get("firm_name")]


def save(entries: list[dict], store=None, commit: bool = True) -> None:
    """Write locally always — roster.py reads the local file moments later, and
    on Railway that copy is the only one this container will see. Commit too
    when a store is available: that is what makes the queue outlive the run."""
    payload = sorted(entries, key=lambda e: (e.get("status", ""),
                                             e.get("firm_name", "")))
    try:
        (_DATA / FILE).write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    except OSError:
        pass
    if store is not None and commit:
        n_open = sum(1 for e in payload if e.get("status") == "queued")
        store.commit_json(FILE, payload,
                          f"Discovery queue: {len(payload)} known, {n_open} open")


def queued(entries: list[dict]) -> list[dict]:
    return [e for e in entries if e.get("status") != "rejected"]


# ------------------------------------------------------------------ name hygiene
def _norm_domain(homepage: str) -> str:
    """'https://www.Example.VC/portfolio' -> 'example.vc'. Empty if unusable."""
    h = (homepage or "").strip().lower()
    if not h:
        return ""
    netloc = urlsplit(h if "//" in h else "//" + h).netloc.split(":")[0]
    if netloc.startswith("www."):
        netloc = netloc[4:]
    return netloc if re.fullmatch(r"[a-z0-9][a-z0-9.-]*\.[a-z]{2,}", netloc) else ""


def _tokens(firm_name: str) -> list[str]:
    """Identifying words in a firm name — 'Foundation Capital' -> ['foundation']."""
    words = re.sub(r"[^a-z0-9 ]", " ", firm_name.lower()).split()
    keep = [w for w in words if w not in _GENERIC_TOKENS and len(w) > 2]
    return keep or words


def _visible_text(html: str) -> str:
    return " ".join(re.sub(r"<[^>]+>", " ", html).split())


def _name_present(firm_name: str, domain: str, html: str) -> bool:
    """Does this site actually belong to this firm? The gate that catches a
    model pairing a plausible name with a domain that isn't the firm's.

    Evidence must come from the PAGE, not the domain. Accepting a domain match
    would make the check circular — a hallucinated firm is usually paired with a
    domain spun out of its own name, so 'northwind.vc' would vouch for
    'Northwind Ventures' and prove nothing. Real firms put their name in the
    title, logo alt text or footer, so page evidence costs them nothing.

    One narrow exception: a fully client-rendered site can ship a near-empty
    shell. Those are the firms the scraper factory exists to rescue, so when
    there is essentially no text to search, fall back to the domain."""
    hay = re.sub(r"[^a-z0-9]", "", html.lower())
    flat = re.sub(r"[^a-z0-9]", "", firm_name.lower())
    toks = _tokens(firm_name)
    if flat and flat in hay:
        return True                       # full name somewhere on the page
    if toks and all(t in hay for t in toks):
        return True                       # every distinctive word present
    if len(_visible_text(html)) < 400:    # JS shell — nothing to match against
        dom = re.sub(r"[^a-z0-9]", "", domain)
        return bool(flat and flat in dom) or bool(toks and all(t in dom for t in toks))
    return False


def _seed_names() -> list[str]:
    """Firm names from the hand-written candidates.json."""
    try:
        return [c["firm_name"] for c in
                json.loads(_CANDIDATES.read_text()).get("candidates", [])
                if c.get("firm_name")]
    except Exception:  # noqa: BLE001
        return []


def _pending(names: list[str], known_files: list[str],
             gstate: dict, sstate: dict) -> list[str]:
    """Of these candidate names, the ones genuinely still waiting on work.

    A firm the factory has retired, or one the scrape memory has written off, is
    FINISHED — unsuccessfully, but finished. Counting those as backlog is a slow
    leak: they never gain a dataset, so they never leave the count, and at one
    new firm a night the total crosses FIND_MAX_OPEN in about a fortnight and
    the finder switches itself off permanently. Measured before this filter: the
    9 stuck JS-heavy candidates put the count at 9 on day one and the finder
    stopped on night 16."""
    import gen_state
    import scrape_state

    out = []
    for n in names:
        if identity.is_known(n, known_files):
            continue                          # has a dataset — done, and worked
        slug = identity.slugify(n)
        if not gen_state.eligible(gstate, slug)[0]:
            continue                          # factory gave up on it
        if not scrape_state.due(sstate, slug)[0]:
            continue                          # scrape memory wrote it off
        out.append(n)
    return out


def _excludes(known_files: list[str], entries: list[dict]) -> list[str]:
    """Every firm the model must not name: repo datasets, the hand-written
    candidates, everything already proposed (accepted OR rejected), and the
    hand-verified no-portfolio list."""
    import names as _names

    # "is this file a dataset?" is identity's rule, not a suffix test done here —
    # a local endswith() check missed companies.json (Lightspeed) and let the
    # model propose a firm the repo already has.
    out: list[str] = []
    for f in sorted(known_files):
        slug = identity.slug_from_file(f)
        if not slug:
            continue
        out.append(_names.display_name(f))
        # Also the names the firm is otherwise known by. firm_names.json holds
        # ONE display name — "a16z" — so without this the model is never told
        # about "Andreessen Horowitz" and duly proposes it.
        out.extend(identity.alt_names_for(slug))
    out += _seed_names()
    out += [e["firm_name"] for e in entries]
    out += _NO_PORTFOLIO
    seen, uniq = set(), []
    for n in out:
        k = identity.slugify(n)
        if k and k not in seen:
            seen.add(k)
            uniq.append(n)
    return uniq


# ------------------------------------------------------------------- proposing
def propose(exclude: list[str], n: int) -> list[dict]:
    """One Claude call -> [{firm_name, homepage}]. Returns [] on any failure:
    the finder is a nice-to-have, so it must never break the nightly run."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        print("[find] ANTHROPIC_API_KEY not set — skipping")
        return []
    prompt = _PROMPT.format(n=n, exclude="\n".join(f"- {e}" for e in exclude))
    model = os.environ.get("FIND_MODEL", "claude-sonnet-4-5")
    try:
        r = requests.post(
            _API,
            headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                     "content-type": "application/json"},
            json={"model": model, "max_tokens": 2000,
                  "messages": [{"role": "user", "content": prompt}]},
            timeout=120,
        )
        r.raise_for_status()
        body = r.json()
    except Exception as exc:  # noqa: BLE001
        print(f"[find] proposal call failed: {exc}")
        return []
    u = body.get("usage", {})
    try:
        cost = budget.bill("finder", model, u)
    except Exception:  # noqa: BLE001
        cost = None
    print(f"[find] tokens in={u.get('input_tokens', '?')} "
          f"out={u.get('output_tokens', '?')}"
          + (f"  ${cost:.4f}" if cost is not None else ""))
    text = "".join(b.get("text", "") for b in body.get("content", []))
    m = re.search(r"\[.*\]", text, re.S)
    if not m:
        print("[find] no JSON array in reply")
        return []
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError as exc:
        print(f"[find] unparseable JSON: {exc}")
        return []
    return [d for d in data if isinstance(d, dict) and d.get("firm_name")]


# ------------------------------------------------------------------ verifying
def verify(firm_name: str, homepage: str) -> tuple[bool, str, str | None]:
    """Check a proposal against the live web. -> (ok, reason, portfolio_url).

    Every gate is HARD now. Reachability + identity separate a real firm from a
    hallucinated one. Portfolio-page resolution WAS a soft gate (queue as null,
    let the factory rescue it) — but terminal no-url retirement (2026-07-27)
    made that path a same-night death: the factory needs the same URL this
    resolver couldn't find, so a null-portfolio candidate is a guaranteed
    tombstone (observed: LocalGlobe, queued and dead within one run). Rejecting
    it here instead lets the loop move on to the night's next proposal, so a
    run queues a firm with a real path to a dataset, or nothing. Accepted cost,
    same shape as the one-strike scrape rule: a real firm whose portfolio only
    exists client-rendered is written off — it is unusable to this pipeline
    either way. Re-arm by deleting its rejected entry."""
    domain = _norm_domain(homepage)
    if not domain:
        return False, f"unusable homepage {homepage!r}", None
    html = extract.fetch(f"https://{domain}") or _browser_fetch(f"https://{domain}")
    if not html:
        return False, "homepage unreachable", None
    if not _name_present(firm_name, domain, html):
        return False, "homepage doesn't mention the firm (wrong or parked domain)", None
    if not any(w in html.lower() for w in _VC_MARKERS):
        return False, "site doesn't read like a VC firm", None
    try:
        portfolio = extract.resolve_portfolio_url(domain)
    except Exception:  # noqa: BLE001
        portfolio = None
    if not portfolio:
        return False, ("no portfolio page resolved — client-rendered or "
                       "none published"), None
    return True, "verified", portfolio


# ------------------------------------------------------------------- the finder
def find(store=None, known_files: list[str] | None = None,
         limit: int | None = None, dry_run: bool = False,
         gstate: dict | None = None, sstate: dict | None = None,
         may_propose: bool = True) -> list[dict]:
    """Top up the queue. Returns the entries added this run (possibly []).

    Always writes the local queue file even when nothing is added, because
    roster.py reads that file immediately afterwards and a Railway container's
    checkout can predate the last run's commit.

    `may_propose=False` does the sync and skips the API call. The caller must
    still call this rather than skipping it outright when the budget is gone:
    otherwise the local queue keeps whatever the last DEPLOY shipped, and every
    firm discovered since then silently drops out of the roster."""
    # Ask identity what counts as a dataset rather than pattern-matching names:
    # glob("*_companies.json") structurally cannot match companies.json, so
    # Lightspeed was invisible here even after identity learned its alias, and a
    # standalone --dry-run proposed it again. The pipeline passes known_files in
    # from GitHub, so only this default was affected.
    known_files = known_files if known_files is not None else [
        p.name for p in _DATA.glob("*.json") if identity.slug_from_file(p.name)]
    limit = limit if limit is not None else int(
        os.environ.get("FIND_MAX_PER_RUN", "1"))
    max_open = int(os.environ.get("FIND_MAX_OPEN", "25"))

    entries = load(store)
    if not dry_run:
        save(entries, None)                      # sync local before roster reads
    if not may_propose:
        print(f"[find] queue synced ({len(queued(entries))} known-good); "
              f"not proposing this run")
        return []

    # queue pressure: candidates from BOTH lists that are still waiting on work.
    # The two memories are passed in by the pipeline (it loads them anyway) so
    # this doesn't re-read them from GitHub; standalone runs load their own.
    if gstate is None:
        import gen_state
        gstate = gen_state.load(store)
    if sstate is None:
        import scrape_state
        sstate = scrape_state.load(store)
    seed = _seed_names()
    open_now = _pending([e["firm_name"] for e in queued(entries)] + seed,
                        known_files, gstate, sstate)
    if len(open_now) >= max_open:
        print(f"[find] {len(open_now)} candidates still unprocessed "
              f"(>= FIND_MAX_OPEN={max_open}) — not adding more")
        return []
    limit = min(limit, max_open - len(open_now))
    if limit < 1:                      # e.g. FIND_MAX_PER_RUN=0
        print("[find] limit is 0 — not proposing (the call costs money either way)")
        return []

    exclude = _excludes(known_files, entries)
    proposals = propose(exclude, int(os.environ.get("FIND_PROPOSALS", "12")))
    if not proposals:
        return []

    known_domains = {_norm_domain(e.get("homepage", "")) for e in entries}
    seen_slugs = {identity.slugify(n) for n in
                  [e["firm_name"] for e in entries] + seed}
    before = len(entries)
    added: list[dict] = []
    for p in proposals:
        if len(added) >= limit:
            break
        name = (p.get("firm_name") or "").strip()
        home = (p.get("homepage") or "").strip()
        slug = identity.slugify(name)
        if not slug or slug in seen_slugs:
            continue                              # proposed before, or on the seed list
        if identity.is_known(name, known_files):
            continue                              # already a dataset
        if any(identity.slugify(n) == slug for n in _NO_PORTFOLIO):
            entries.append({"firm_name": name, "homepage": home,
                            "portfolio_url": None, "status": "rejected",
                            "reason": "hand-verified: publishes no portfolio",
                            "found_at": _now()})
            seen_slugs.add(slug)       # one response can name a firm twice
            print(f"[find] {name}: REJECTED (known no-portfolio firm)")
            continue
        dom = _norm_domain(home)
        if dom and dom in known_domains:
            continue

        ok, reason, portfolio = verify(name, home)

        # "unreachable" is the one failure that says nothing about the FIRM —
        # it's a timeout, an outage, or bot protection we didn't get past. A
        # rejection here is permanent memory, so recording it would blacklist a
        # real firm on one bad night. Skip it silently instead: it costs one
        # request to re-check whenever the model happens to propose it again.
        if not ok and reason == "homepage unreachable":
            seen_slugs.add(slug)       # don't re-fetch it twice in one response;
            print(f"[find] {name} ({home}): unreachable — not recorded, "
                  f"may be proposed again")   # but nothing is written, so a
            continue                          # later RUN may still propose it

        entry = {"firm_name": name, "homepage": dom or home,
                 "portfolio_url": portfolio,
                 "status": "queued" if ok else "rejected",
                 "reason": reason, "found_at": _now()}
        entries.append(entry)
        known_domains.add(dom)
        seen_slugs.add(slug)
        if ok:
            added.append(entry)
            # the hard gate means a queued entry always has a portfolio url
            print(f"[find] {name} ({dom}): QUEUED -> {portfolio}")
        else:
            print(f"[find] {name} ({home}): rejected — {reason}")

    if dry_run:
        print(f"[find] dry-run: {len(added)} would be queued, nothing saved")
        return added
    if len(entries) == before:
        # Nothing queued and nothing rejected — every proposal was a duplicate,
        # already-known, or unreachable. Committing here would push a file
        # identical to the one in the repo, so the discovery service would leave
        # a "Discovery queue" commit on nights it learned nothing.
        print("[find] nothing new to record")
        return added
    save(entries, store)
    print(f"[find] added {len(added)} candidate(s); queue now "
          f"{len(queued(entries))} known-good / {len(entries)} seen")
    return added


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Top up the discovery queue.")
    ap.add_argument("--dry-run", action="store_true",
                    help="propose + verify, print results, write nothing")
    ap.add_argument("--limit", type=int, default=None,
                    help="max candidates to add (default FIND_MAX_PER_RUN)")
    a = ap.parse_args()
    find(store=None, limit=a.limit, dry_run=a.dry_run)
