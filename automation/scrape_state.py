"""Backoff memory for generic scraping — data/scrape_attempts.json.

The scrape stage was the one stage of the nightly run with no memory. A
candidate whose portfolio page the generic extractor can't read — nearly always
a client-rendered site, where the HTML arrives empty and the companies are drawn
in later by the browser — was re-tried in full every single night, forever. Each
retry costs a homepage fetch plus up to ~15 guessed portfolio URLs, so nine
stuck candidates burned ~150 requests a night to reproduce nine known failures,
and wrote nine `needs-scraper` rows to Airtable while they were at it.

Per user decision (2026-07-27): one failure and the firm is NOT scraped again.

    [{"slug": "uncorkcapital", "failures": 1,
      "last_attempt": "2026-07-27T07:00:00+00:00",
      "next_attempt": null,                       # null = never
      "last_reason": "no portfolio page resolved"}]

The rejected alternative was a widening retry (1 -> 3 -> 7 -> 30 days), which
keeps a door open for a site rebuilt in plain HTML later and forgives a firm
that merely happened to be down that night. It was dropped for being harder to
reason about: the user wants "it doesn't even try it again", and a trickle of
retries is exactly the behaviour that made the old system confusing. The cost
is real and worth stating — a site that was briefly unreachable is written off
on one bad night — so both escape hatches are kept cheap:

  * SCRAPE_BACKOFF_DAYS="1,3,7,30" restores the widening retry, no code change;
  * deleting a firm's entry from data/scrape_attempts.json re-arms it.

Note the env override is NOT retroactive: entries already written under the
never-retry default carry "next_attempt": null, which due() reads as permanent
regardless of the schedule in force. Setting the variable changes what happens
to firms that fail from then on; to re-arm the existing ones, delete them.

A successful scrape also clears the entry — the firm graduated, so the memory
is meaningless.

Deliberately SEPARATE from gen_state's data/gen_attempts.json even though the
shape is similar. They answer different questions — "can the extractor read this
page?" versus "can Claude write a scraper for this site?" — and they retire on
different terms. Merging them would mean one file whose fields only apply half
the time, and a factory retry would silently reset a scrape backoff.

State lives in the repo (committed through the same Contents API as the
datasets) so ephemeral Railway containers read it back. data/ is outside
Railway's Watch Paths, so committing it mid-run does not redeploy the service.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

FILE = "scrape_attempts.json"
_DATA = Path(__file__).resolve().parent.parent / "data"

# Empty = never retry: one failed extraction and the firm is done. Set
# SCRAPE_BACKOFF_DAYS to a list of days ("1,3,7,30") to wait those intervals
# after the 1st, 2nd, 3rd … failure instead, last value repeating.
_SCHEDULE: tuple[int, ...] = ()


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _parse(ts: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(ts) if ts else None
    except (TypeError, ValueError):
        return None


def schedule() -> tuple[int, ...]:
    """Retry intervals in days; empty means never retry (the default)."""
    raw = os.environ.get("SCRAPE_BACKOFF_DAYS")
    if not raw:
        return _SCHEDULE
    try:
        return tuple(int(p) for p in raw.split(",") if p.strip()) or _SCHEDULE
    except ValueError:
        return _SCHEDULE


def load(store=None) -> dict:
    """State as {slug: entry}. Prefers the repo copy (Railway containers are
    ephemeral); falls back to the local file; tolerates absence and garbage —
    a broken state file must degrade to 'no memory', never kill the run."""
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
    out: dict = {}
    for e in raw if isinstance(raw, list) else []:
        if isinstance(e, dict) and e.get("slug"):
            out[e["slug"]] = e
    return out


def due(state: dict, slug: str, now: datetime | None = None) -> tuple[bool, str]:
    """(True, "") if this firm should be scraped tonight, else (False, why).

    Unknown firms are always due — no memory means no reason to wait. A garbled
    entry is treated the same way: a corrupt state file must degrade to 'scrape
    it', never to 'silently stop scraping everything'."""
    e = state.get(slug)
    if not e:
        return True, ""
    if e.get("failures") and e.get("next_attempt") is None:
        return False, (f"already failed ({e.get('last_reason', '?')}) — not "
                       f"scraped again; delete its entry in data/{FILE} to retry")
    nxt = _parse(e.get("next_attempt"))
    if nxt is None:
        return True, ""
    now = now or _now()
    if now >= nxt:
        return True, ""
    hours = max(1, int((nxt - now).total_seconds() // 3600))
    return False, (f"waiting after {e.get('failures', '?')} failed attempt(s) "
                   f"({e.get('last_reason', '?')}) — next try in ~{hours}h")


def record_failure(state: dict, slug: str, reason: str,
                   now: datetime | None = None) -> str | None:
    """Log a failed scrape. Returns when it may be retried, or None for never."""
    now = now or _now()
    e = state.setdefault(slug, {"slug": slug, "failures": 0})
    e["failures"] = int(e.get("failures") or 0) + 1
    e["last_attempt"] = now.isoformat()
    e["last_reason"] = reason
    days = schedule()
    e["next_attempt"] = (
        (now + timedelta(days=days[min(e["failures"], len(days)) - 1])).isoformat()
        if days else None)
    return e["next_attempt"]


def clear(state: dict, slug: str) -> bool:
    """Drop a firm's entry (on a successful scrape). True if there was one."""
    return state.pop(slug, None) is not None


def save(state: dict, store=None) -> None:
    """Write locally always (local runs); commit when a store is available —
    on Railway the commit IS the persistence."""
    payload = sorted(state.values(), key=lambda e: e.get("slug", ""))
    try:
        (_DATA / FILE).write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    except OSError:
        pass
    if store is not None:
        store.commit_json(FILE, payload,
                          f"Scrape backoff: {len(payload)} firm(s) waiting")
