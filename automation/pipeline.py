"""The nightly pipeline — one pass, each firm handled exactly once.

    find new candidates ─► roster ─► for each firm ─► get fresh companies
      (discover mode)                                (bespoke run | generic extract)
    ─► diff vs GitHub ─► commit ─► collect ─► Airtable upserts (registry)

There is no separate discovery phase and no second service: a NEW firm is simply
one whose previous dataset is empty, so it falls out of the same diff as "all
added" with a `new` registry row. Dedup happens once (in the roster); GitHub is
read once per firm and committed once. Nothing here imports another service —
the pipeline is self-contained.

In discover mode the run opens with candidate_finder, which appends verified new
firms to data/discovered_candidates.json so the roster is never empty of leads,
and closes with the scraper factory, which turns leads into bespoke scrapers.

Secrets held: GITHUB_TOKEN (commit), AIRTABLE_PAT (upsert), ANTHROPIC_API_KEY
(candidate finder + scraper factory, discovery service only).

Run:  python3 automation/pipeline.py                 # full nightly pass
      python3 automation/pipeline.py --dry-run        # scrape+diff, no commit/post
      python3 automation/pipeline.py --limit 5
      python3 automation/pipeline.py --only accel,felicis
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import airtable_writer
import budget
import diff
import extract
import gen_state
import roster
import scrape_state
import tags
from gh import GitHubStore

_REPO = Path(__file__).resolve().parent.parent
_DATA = _REPO / "data"
_CONF_THRESHOLD = float(os.environ.get("GENERIC_CONFIDENCE", "0.6"))


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _run_bespoke(firm: roster.Firm) -> tuple[Optional[list], Optional[str]]:
    """Run the firm's scraper, then read the file it wrote. (records, error)."""
    try:
        proc = subprocess.run(
            [sys.executable, firm.scraper_path], cwd=str(_REPO),
            capture_output=True, text=True,
            timeout=int(os.environ.get("SCRAPER_TIMEOUT", "600")),
        )
    except subprocess.TimeoutExpired:
        return None, "timeout"
    if proc.returncode != 0:
        return None, f"exit {proc.returncode}: {proc.stderr.strip()[-300:]}"
    try:
        return json.loads((_DATA / firm.data_file).read_text()), None
    except Exception as exc:  # noqa: BLE001
        return None, f"unreadable output: {exc}"


def _run_generic(firm: roster.Firm) -> tuple[Optional[list], Optional[str]]:
    """Resolve the portfolio URL if needed, then generic-extract. (records, error)."""
    url = firm.portfolio_url or (
        extract.resolve_portfolio_url(firm.homepage) if firm.homepage else None)
    if not url:
        return None, "no portfolio page resolved"
    firm.portfolio_url = url
    records, conf = extract.extract_companies(url)
    if not records or conf < _CONF_THRESHOLD:
        return None, f"low confidence {conf:.2f} ({len(records)} found)"
    return records, None


def _source_url(firm: roster.Firm, records: list[dict]) -> Optional[str]:
    if firm.portfolio_url:
        return firm.portfolio_url
    for r in records or []:
        if r.get("source_url"):
            return r["source_url"]
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["all", "discover", "refresh"], default="all",
                    help="discover = only add NEW firms (fast, routine); "
                         "refresh = only re-scrape existing firms (heavy, weekly); "
                         "all = both (default)")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--only", type=str, default="")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    store = None if args.dry_run else GitHubStore()
    known_files = (store.list_data_files() if store
                   else [p.name for p in _DATA.glob("*.json")])

    # Month-to-date API spend. Activated before anything can call Claude, so
    # every call lands in the ledger whether or not the caller knows about it.
    bstate = budget.load(store)
    budget.activate(bstate)
    calls_before = int(bstate.get("calls") or 0)
    print(f"[budget] ${bstate['spent']:.2f} of ${budget.budget():.2f} spent "
          f"in {bstate['month']}")

    # Scrape memory, both read ONCE here and reused everywhere below: the finder
    # needs them to tell a real backlog from firms that are simply finished, the
    # scrape loop needs them to decide what to skip, and the factory needs the
    # attempt log. sstate is the backoff clock; gstate is the factory's log.
    sstate = scrape_state.load(store)
    gstate = gen_state.load(store)
    sstate_dirty = False

    # --- top up the discovery queue BEFORE the roster is built, so a firm found
    # tonight is scraped tonight (and can reach the factory the same run) — the
    # same "leave the queue the night you enter it" pacing the factory uses.
    # Safe to commit mid-run: the queue lives in data/, which is outside
    # Railway's Watch Paths, so this push does not redeploy the container.
    # Called even when the budget is gone: find() still syncs the queue from the
    # repo to disk, and roster.build() reads that file a few lines below. Skip
    # the call entirely and the roster sees only what the last DEPLOY shipped,
    # quietly losing every firm discovered since.
    if args.mode in ("discover", "all") and not args.dry_run:
        affordable, why = budget.can_find(bstate)
        if not affordable:
            print(f"[find] {why}")
        import candidate_finder
        try:
            candidate_finder.find(store, known_files, gstate=gstate,
                                  sstate=sstate, may_propose=affordable)
        except Exception as exc:  # noqa: BLE001 — nice-to-have, never fatal
            print(f"[find] skipped: {exc}")

    firms = roster.build(known_files)
    if args.mode == "discover":       # only new firms (generic candidates)
        firms = [f for f in firms if f.kind == "generic"]
    elif args.mode == "refresh":      # only re-scrape firms we already have
        firms = [f for f in firms if f.kind == "bespoke"]
    if args.only:
        want = {s.strip() for s in args.only.split(",")}
        firms = [f for f in firms if f.slug in want]
    if args.limit:
        firms = firms[: args.limit]

    run_at = _now()
    registry: list[dict] = []
    companies: list[dict] = []
    tally = {"added": 0, "dropped": 0, "exited": 0, "errors": 0,
             "new_firms": 0, "committed": 0, "skipped": 0}

    for i, firm in enumerate(firms, 1):
        # Generic candidates only. A bespoke scraper failing is real breakage
        # and must never be silenced by a backoff. The check runs BEFORE any
        # network call — that is the entire point — and does NOT remove the firm
        # from `firms`, so the factory below can still target it.
        if firm.kind == "generic":
            retired = not gen_state.eligible(gstate, firm.slug)[0]
            ready, why = scrape_state.due(sstate, firm.slug)
            if retired or not ready:
                tally["skipped"] += 1
                print(f"[{i}/{len(firms)}] {firm.slug}: skipped — "
                      + ("factory gave up on this firm; not scraping it again"
                         if retired else why))
                continue
            if firm.data_file not in known_files:
                # FACTORY-ONLY for new firms (user decision 2026-07-28): a
                # brand-new firm is never generic-scraped — its first dataset
                # is its bespoke one. No fetches, no needs-scraper row, no
                # scrape-memory entry; it just waits in the factory queue
                # below. Cost, accepted knowingly: no day-one data if the
                # burst has a bad night, and the ≥50%-of-baseline validation
                # gate loses its reference (absolute thresholds remain).
                tally["skipped"] += 1
                print(f"[{i}/{len(firms)}] {firm.slug}: factory-only — "
                      f"awaiting bespoke scraper")
                continue

        print(f"[{i}/{len(firms)}] {firm.slug} ({firm.kind}) …", flush=True)

        # previous side of the diff (None -> brand-new firm -> [])
        old = (store.read_json(firm.data_file) if store
               else _local(firm)) or []

        # fresh side
        new, err = (_run_bespoke(firm) if firm.kind == "bespoke"
                    else _run_generic(firm))
        if new:
            # Tags are the product (the dashboard agent builds comps from
            # them). Order matters: inherit from the previous dataset first
            # (carry_forward — keeps one-off enrichment like the LLM backfill
            # alive across refreshes), then keyword-fill what's still empty.
            # Both touch only EMPTY tags, so a hand-written scraper's own
            # tagging always wins.
            tags.carry_forward(old, new)
            tags.fill_empty(new)

        health = diff.registry_health(firm.slug, firm.data_file, old, new, err)
        health["output_url"] = store.raw_url(firm.data_file) if store else None
        health["firm_name"] = firm.firm_name
        health["source_url"] = _source_url(firm, new or [])

        if err:
            tally["errors"] += 1
            health["status"] = "needs-scraper" if firm.kind == "generic" else "broke"
            registry.append(health)
            if firm.kind == "generic":
                nxt = scrape_state.record_failure(sstate, firm.slug, err)
                sstate_dirty = True
                print(f"    NEEDS-SCRAPER: {err} — "
                      + (f"next try {nxt[:10]}" if nxt
                         else "will not be scraped again"))
            else:
                print(f"    BROKE: {err}")
            continue

        d = diff.diff_firm(firm.slug, firm.data_file, old, new)
        companies += d["added"] + d["dropped"] + d["exited"]
        tally["added"] += len(d["added"]); tally["dropped"] += len(d["dropped"])
        tally["exited"] += len(d["exited"])
        if health["is_new"]:
            tally["new_firms"] += 1
        health["status"] = "active"
        if firm.kind == "generic" and scrape_state.clear(sstate, firm.slug):
            sstate_dirty = True          # it worked — the backoff is now moot
        health.update(tags.count_tags(new))   # 17 per-firm tag counts, flat keys

        changed = bool(d["added"] or d["dropped"] or d["exited"])
        if changed or health["health"] not in ("same",):
            registry.append(health)

        if store and health["safe_to_commit"] and (changed or health["is_new"]):
            sha = store.commit_json(
                firm.data_file, new,
                f"Nightly: {firm.slug} +{len(d['added'])}/-{len(d['dropped'])} "
                f"({health['record_count']} total)")
            health["commit_sha"] = sha
            tally["committed"] += 1
        elif not health["safe_to_commit"]:
            print(f"    held back (health={health['health']}) — not committing")

        tag = "NEW" if health["is_new"] else f"{health['prev_count']}→{health['record_count']}"
        print(f"    +{len(d['added'])} -{len(d['dropped'])} ~{len(d['exited'])}  [{tag}]")

    # Persist the backoff clock HERE, not at the end: the factory's flush pushes
    # to scripts/, which redeploys Railway and stops this container. Anything
    # written after that push may never land.
    if sstate_dirty and not args.dry_run:
        try:
            scrape_state.save(sstate, store)
            sstate_dirty = False     # banked; the factory may dirty it again
        except Exception as exc:  # noqa: BLE001 — never lose the run over this
            print(f"[backoff] save FAILED: {exc}")

    # --- scraper factory (discover mode only): give scraper-less firms a real,
    # bespoke scraper via Claude API generation + guard + sandbox + validation.
    # Bursts write LOCALLY (store=None); GitHub commits are deferred to
    # _flush_factory_commits at the very end of the run — a mid-run push
    # triggers a Railway redeploy that stops this very container (felicis,
    # 2026-07-27), so the push must land when there is nothing left to kill.
    gen_done: list = []
    at_deletes: list = []                  # Data files whose Airtable row must go
    repo_deletes: list = []                # datasets removed with their firm
    gstate_dirty = False
    if args.mode == "discover" and not args.dry_run \
            and os.environ.get("ANTHROPIC_API_KEY"):
        import scraper_factory
        cap = int(os.environ.get("GEN_MAX_PER_RUN", "3"))
        gen_targets = scraper_factory.targets(firms, gstate)[:cap]
        attempt_budget = gen_state.max_attempts()   # TRIES per firm, not dollars
        for firm in gen_targets:
            # Dollars, checked per firm: a burst that ate more than expected
            # must stop the NEXT firm, not just the run after this one.
            affordable, why = budget.can_generate(bstate)
            if not affordable:
                print(f"[factory] {firm.slug}: skipped — {why}")
                continue
            done = int((gstate.get(firm.slug) or {}).get("attempts") or 0)
            tries_left = max(1, attempt_budget - done)  # burst: all of it, tonight
            print(f"[factory] {firm.slug}: up to {tries_left} generation "
                  f"tries …", flush=True)
            r = scraper_factory.attempt(firm, None, tries=tries_left)
            print(f"[factory] {firm.slug}: {r['reason']}")
            for fail in r["failures"]:
                # A terminal result carries exactly one failure (attempt()
                # returns immediately), so the flag cannot over-apply.
                gen_state.record_failure(gstate, firm.slug, fail,
                                         terminal=r.get("terminal", False))
                gstate_dirty = True
            if not r["ok"] and not gen_state.eligible(gstate, firm.slug)[0]:
                print(f"[factory] {firm.slug}: retired — won't be re-tried "
                      f"(re-arm by editing data/gen_attempts.json)")
                registry = _retire_fully(firm, registry, at_deletes,
                                         repo_deletes)
            if r["ok"]:
                gstate_dirty |= gen_state.clear(gstate, firm.slug)
                # The firm now has a dataset, so it leaves the roster and the
                # scrape memory about it is dead weight. Clear it so the file
                # keeps meaning "firms we gave up scraping", not "firms we once
                # gave up on" — and so deleting the dataset later re-arms it.
                sstate_dirty |= scrape_state.clear(sstate, firm.slug)
                gen_done.append((firm, r["records"]))
                # replace the firm's registry row with one built from the rich dataset
                h = diff.registry_health(firm.slug, firm.data_file, [], r["records"])
                h.update({"status": "active", "firm_name": firm.firm_name,
                          "source_url": firm.portfolio_url,
                          "output_url": store.raw_url(firm.data_file) if store else None})
                h.update(tags.count_tags(r["records"]))
                registry = [x for x in registry if x.get("data_file") != firm.data_file]
                registry.append(h)
                tally["generated"] = tally.get("generated", 0) + 1

    # Spend ledger: saved BEFORE the factory flush, for the same reason the
    # backoff clock is — that flush redeploys Railway and kills this container.
    # Losing the ledger would silently reset the month's spend to zero.
    #
    # Only when something was actually billed. The weekly refresh service holds
    # no Anthropic key, so it would otherwise rewrite an unchanged ledger every
    # Monday — a pointless commit, and a chance to clobber the discovery
    # service's figure with a copy it read an hour earlier.
    if not args.dry_run and int(bstate.get("calls") or 0) != calls_before:
        try:
            budget.save(bstate, store)
        except Exception as exc:  # noqa: BLE001
            print(f"[budget] save FAILED: {exc}")

    # Second scrape-memory save, only if the factory cleared an entry above. The
    # first save (right after the scrape loop) deliberately banks the expensive
    # scrape results early; this catches the cheap change the factory makes.
    if sstate_dirty and not args.dry_run:
        try:
            scrape_state.save(sstate, store)
        except Exception as exc:  # noqa: BLE001
            print(f"[backoff] save FAILED: {exc}")

    summary = {**tally, "firms_scanned": len(firms),
               "firms_changed": len(registry), "run_at": run_at,
               "spend_month_to_date": round(bstate["spent"], 4),
               "budget": budget.budget()}
    print("\n== summary ==\n" + json.dumps(summary, indent=2))

    # Write firm rows straight to Airtable (no Zapier). Company deltas are computed
    # for change-detection but not stored — Portfolio Companies is out of scope.
    if args.dry_run:
        print(f"[dry-run] not writing to Airtable "
              f"({len(registry)} firms would upsert)")
    else:
        airtable_writer.upsert_firms(registry, run_at)
        if at_deletes:
            airtable_writer.delete_firms(at_deletes)
        for df in repo_deletes:
            # bespoke-or-nothing: the retiring firm's dataset leaves the repo
            # too (data/ is outside Watch Paths, so this never redeploys; git
            # history keeps the file). Failure just leaves a stray dataset —
            # eligible() already blocks the firm everywhere, so log and move on.
            try:
                if store and store.delete_json(
                        df, f"Retire {df}: factory exhausted, bespoke-or-nothing"):
                    print(f"[retire] deleted {df} from the repo")
            except Exception as exc:  # noqa: BLE001
                print(f"[retire] repo delete FAILED for {df}: {exc}")
        if args.mode == "discover":
            # Reconcile (see airtable_writer.delete_strays): keep = every
            # dataset the repo knows, plus anything that graduated tonight,
            # plus any firm that may still produce one (bespoke, or a
            # candidate the memories haven't closed the book on). Every other
            # row with a Data file is a tombstone. This is what removes rows
            # stranded before delete-on-retire existed — e.g. the pre-retired
            # JS-heavy nine — without a hand-run cleanup script.
            keep = set(known_files)
            keep |= {f.data_file for f, _ in gen_done}
            keep |= {f.data_file for f in firms
                     if gen_state.eligible(gstate, f.slug)[0]}
            airtable_writer.delete_strays(keep)

    if gen_done or gstate_dirty:                # factory pushes: LAST, on purpose
        _flush_factory_commits(store, gen_done, gstate, gstate_dirty)
    return 0


def _flush_factory_commits(store, gen_done: list, gstate, gstate_dirty: bool) -> None:
    """Deferred factory persistence — the run's FINAL action, after Airtable.

    The GitHub pushes here trigger a Railway redeploy of this very service; done
    mid-run, that redeploy stops the running container (observed live: felicis's
    success commit killed foundrygroup's burst, 2026-07-27). Deferring means the
    redeploy lands on a container with nothing left to do. Scraper is committed
    BEFORE dataset so a kill between the two self-heals: the firm is already
    bespoke, and the next refresh rewrites its dataset. Per-firm failures are
    printed, not raised — local artifacts survive and the next run heals."""
    import scraper_factory
    if store is not None:
        for firm, records in gen_done:
            try:
                code = (_REPO / "scripts" / f"{firm.slug}_scraper.py").read_text()
                scraper_factory._commit_text(
                    store, f"scripts/{firm.slug}_scraper.py", code,
                    f"Auto-generated scraper: {firm.slug} (guard+validation passed)")
                store.commit_json(
                    firm.data_file, records,
                    f"Auto-generated rich dataset: {firm.slug} ({len(records)} companies)")
                print(f"[factory] {firm.slug}: scraper + dataset committed")
            except Exception as exc:  # noqa: BLE001 — keep local artifacts
                print(f"[factory] {firm.slug}: deferred commit FAILED: {exc}")
    if gstate_dirty:
        try:
            gen_state.save(gstate, store)
        except Exception as exc:  # noqa: BLE001
            print(f"[factory] attempt-log save FAILED: {exc}")


def _retire_fully(firm, registry: list, at_deletes: list,
                  repo_deletes: list) -> list:
    """Bespoke or nothing (user decisions 2026-07-27/28): a firm the factory
    gives up on leaves no trace outside repo memory. Its in-run registry row
    is dropped and its Airtable row queued for deletion (`at_deletes` →
    airtable_writer.delete_firms); if it had a thin generic dataset (the
    wingvc/signalfire class), that dataset is deleted too — locally
    best-effort, and from the repo via `repo_deletes` → store.delete_json.
    Git history keeps every deleted file, so this is always reversible. This
    replaced the earlier keep-the-thin-dataset fallback the same day the user
    ruled "if the EV tags are missing, I do not need the data". Lightspeed's
    hand-built companies.json can never get here: factory targets()
    deliberately excludes it. Repo-side memory is untouched —
    gen_attempts/scrape_attempts entries are what stop anything being
    re-proposed or re-scraped."""
    registry = [h for h in registry
                if h.get("data_file") != firm.data_file]
    at_deletes.append(firm.data_file)
    repo_deletes.append(firm.data_file)
    p = _DATA / firm.data_file
    if p.exists():
        try:
            p.unlink()
        except OSError:
            pass          # ephemeral container; the repo deletion is the real one
    return registry


def _local(firm: roster.Firm) -> Optional[list]:
    try:
        return json.loads((_DATA / firm.data_file).read_text())
    except Exception:  # noqa: BLE001
        return None


if __name__ == "__main__":
    raise SystemExit(main())
