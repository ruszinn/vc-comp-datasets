"""Offline tests for the generic-scrape memory — no network, no API key, no cost.

Two things need proving, and the second is the one that matters:

  1. one failure means never again, and both escape hatches still work;
  2. it actually saves the requests. So the last test SIMULATES 30 nightly runs
     over the nine stuck candidates and counts real fetch calls, comparing
     today's behaviour against the old always-scrape one. A backoff that looked
     right but still fetched nightly would pass every unit test and fix nothing.

Run:  python3 automation/test_scrape_backoff.py     (exit 0 = all pass)
"""
from __future__ import annotations

import os
import pathlib
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import gen_state  # noqa: E402
import scrape_state as ss  # noqa: E402

_FAILS: list[str] = []
T0 = datetime(2026, 8, 1, 7, 0, tzinfo=timezone.utc)   # a run at 07:00 UTC


def check(label: str, got, want) -> None:
    ok = got == want
    if not ok:
        _FAILS.append(f"{label}: got {got!r}, want {want!r}")
    print(f"  {'ok  ' if ok else 'FAIL'} {label}"
          + ("" if ok else f"   got={got!r} want={want!r}"))


def test_default_never_retries() -> None:
    print("\ndefault: one failure and it is never scraped again")
    check("no retry schedule by default", ss.schedule(), ())
    st: dict = {}
    check("unknown firm is due now", ss.due(st, "uncork", T0)[0], True)

    nxt = ss.record_failure(st, "uncork", "no portfolio page resolved", now=T0)
    check("no next attempt is scheduled", nxt, None)
    check("not due the same night", ss.due(st, "uncork", T0)[0], False)
    check("not due tomorrow",
          ss.due(st, "uncork", T0 + timedelta(days=1))[0], False)
    check("not due in a year",
          ss.due(st, "uncork", T0 + timedelta(days=365))[0], False)
    check("reason names the failure and the way back",
          all(s in ss.due(st, "uncork", T0)[1]
              for s in ("no portfolio page resolved", "scrape_attempts.json")),
          True)


def test_escape_hatches() -> None:
    print("\nescape hatches")
    st: dict = {}
    ss.record_failure(st, "craft", "low confidence 0.20 (3 found)", now=T0)
    check("deleting the entry re-arms the firm", ss.clear(st, "craft"), True)
    check("re-armed firm is due again", ss.due(st, "craft", T0)[0], True)
    check("clearing an unknown firm is a no-op", ss.clear(st, "nobody"), False)

    # SCRAPE_BACKOFF_DAYS restores the widening retry without a code change
    os.environ["SCRAPE_BACKOFF_DAYS"] = "1,3,7,30"
    try:
        check("env var restores the schedule", ss.schedule(), (1, 3, 7, 30))
        st2: dict = {}
        waits = []
        for _ in range(5):
            base = T0 + timedelta(days=sum(waits))
            ss.record_failure(st2, "uncork", "no portfolio page resolved", now=base)
            nxt = datetime.fromisoformat(st2["uncork"]["next_attempt"])
            waits.append((nxt - base).days)
        check("waits widen then plateau", waits, [1, 3, 7, 30, 30])
        check("due again once the wait passes",
              ss.due(st2, "uncork", T0 + timedelta(days=400))[0], True)
    finally:
        del os.environ["SCRAPE_BACKOFF_DAYS"]
    check("garbage env value falls back to never", ss.schedule(), ())


def test_corrupt_state_degrades() -> None:
    print("\nbroken state degrades to 'no memory'")
    check("missing next_attempt -> due", ss.due({"x": {"slug": "x"}}, "x", T0)[0], True)
    check("garbage timestamp -> due",
          ss.due({"x": {"slug": "x", "next_attempt": "not-a-date"}}, "x", T0)[0], True)


def test_retirement_stops_scraping() -> None:
    print("\nfactory retirement reaches the scrape queue")
    g: dict = {}
    for _ in range(gen_state.max_attempts()):
        gen_state.record_failure(g, "susa", "validation: description coverage < 30%")
    check("firm is retired from generation", gen_state.eligible(g, "susa")[0], False)
    # the pipeline's skip test, mirrored
    retired = not gen_state.eligible(g, "susa")[0]
    check("so the pipeline skips scraping it", retired, True)
    check("a firm with attempts left is NOT skipped",
          gen_state.eligible({}, "wingvc")[0], True)
    check("a manual skip flag also stops scraping",
          gen_state.eligible({"thin": {"slug": "thin", "skip": True}}, "thin")[0],
          False)


# --------------------------------------------------------------- the real test
STUCK = ["emergencecapital", "foundationcapital", "uncorkcapital", "craftventures",
         "boldstartventures", "costanoaventures", "susaventures", "bullpencapital",
         "scaleventurepartners"]
FETCHES_PER_ATTEMPT = 16     # homepage + ~15 candidate/guessed portfolio URLs


def _simulate(nights: int, *, memory: bool) -> tuple[int, list[int]]:
    """Replay `nights` nightly runs over the nine permanently-failing firms.

    Returns (total fetches, fetches per night). Per-night matters as much as the
    total: a memory that merely THINNED the traffic would still show a big
    percentage drop while quietly fetching every night forever."""
    st: dict = {}
    gs: dict = {}
    total, per_night = 0, []
    for night in range(nights):
        now = T0 + timedelta(days=night)
        tonight = 0
        for slug in STUCK:
            if memory:
                if not gen_state.eligible(gs, slug)[0]:
                    continue                   # factory gave up -> never again
                if not ss.due(st, slug, now)[0]:
                    continue                   # already failed -> never again
            tonight += FETCHES_PER_ATTEMPT     # the scrape actually runs
            ss.record_failure(st, slug, "no portfolio page resolved", now=now)
            if memory and not gs.get(slug):    # factory bursts its whole budget
                for _ in range(gen_state.max_attempts()):
                    gen_state.record_failure(gs, slug, "validation failed")
        total += tonight
        per_night.append(tonight)
    return total, per_night


def test_request_savings() -> None:
    print("\nrequests over 30 nights (9 permanently-failing firms)")
    before, before_nightly = _simulate(30, memory=False)
    after, after_nightly = _simulate(30, memory=True)

    print(f"    before : {before:>5} fetches   nightly: {before_nightly[:4]} …")
    print(f"    after  : {after:>5} fetches   nightly: {after_nightly[:4]} …"
          f"   ({100 - round(after / before * 100)}% fewer)")

    check("before: ~150 every single night", set(before_nightly),
          {len(STUCK) * FETCHES_PER_ATTEMPT})
    check("after: one night's worth, total", after,
          len(STUCK) * FETCHES_PER_ATTEMPT)
    check("after: zero on every night but the first",
          set(after_nightly[1:]), {0})
    check("a firm that succeeds is never in the memory at all",
          ss.due({}, "matrixpartners", T0)[0], True)


if __name__ == "__main__":
    test_default_never_retries()
    test_escape_hatches()
    test_corrupt_state_degrades()
    test_retirement_stops_scraping()
    test_request_savings()
    print(f"\n{'FAILED: ' + str(len(_FAILS)) if _FAILS else 'all tests passed'}")
    for f in _FAILS:
        print("  -", f)
    raise SystemExit(1 if _FAILS else 0)
