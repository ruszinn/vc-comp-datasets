"""Attempt memory for the scraper factory — data/gen_attempts.json.

The factory is deliberately stateless everywhere else: targets() re-derives its
queue from file existence alone, so "never tried" and "tried nightly and failed"
look identical. Without a memory, a firm whose generated scrapers keep failing
validation — e.g. a site that publishes no descriptions, making the >=30%
description gate structurally unpassable — would be retried at full API cost
every night, forever, pinning one of the GEN_MAX_PER_RUN slots at the head of
the alphabetical queue.

State lives in the repo itself (data/gen_attempts.json, committed through the
same Contents API as the datasets) so ephemeral Railway containers read it back
exactly like they read datasets. Shape: a JSON list (GitHubStore.read_json only
round-trips lists), one entry per firm with open attempts:

    [{"slug": "wingvc", "attempts": 2, "skip": false,
      "last_attempt": "2026-07-27T07:00:00+00:00",
      "last_reason": "validation: description coverage < 30% (not rich)",
      "history": [{"at": "...", "reason": "...", "counted": true}]}]

Rules:
  * every failed attempt is recorded; it COUNTS toward exhaustion unless it was
    a pure API-transport failure ("generation error: ..."), which says nothing
    about the firm;
  * pacing (2026-07-27): the factory spends a firm's whole budget as a BURST in
    its first run (scraper_factory.attempt(tries=...)), so exhaustion — being
    "retired" — normally happens the same night the firm is first tried;
  * a firm stops being targeted once attempts >= GEN_MAX_ATTEMPTS (default 4)
    — retired — or when a human sets "skip": true, the explicit
    "legitimately thin, leave it alone" flag;
  * a failure the factory marks TERMINAL (currently only "no portfolio url":
    there is no page to generate against, so a retry is a no-op) sets
    "retired": true and exhausts the firm at once, whatever its attempt
    count — recorded as a flag, not as attempts = max, so the entry stays
    honest about how many tries actually ran (user decision 2026-07-27);
  * success deletes the entry — the committed scraper is the durable state;
  * to re-arm an exhausted firm, delete (or edit) its entry and commit.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

FILE = "gen_attempts.json"
_DATA = Path(__file__).resolve().parent.parent / "data"
_HISTORY_KEEP = 5
_UNCOUNTED_PREFIX = "generation error"   # API/transport — not the firm's fault


def max_attempts() -> int:
    """Tries per firm. 4, not 3, since model escalation arrived: the first two
    go to the cheap model, so a budget of 3 would have left the strong model a
    single attempt with no room to iterate on its own failure — a quality
    regression smuggled in as a cost saving. 4 restores the strong model's
    second try; at the observed success mix it costs nothing, because most
    firms finish on try 1 and never reach it."""
    return int(os.environ.get("GEN_MAX_ATTEMPTS", "4"))


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


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


def eligible(state: dict, slug: str) -> tuple[bool, str]:
    """(True, "") if the factory may attempt this firm, else (False, why)."""
    e = state.get(slug)
    if not e:
        return True, ""
    if e.get("skip"):
        return False, "manual skip flag in data/gen_attempts.json"
    if e.get("retired"):
        return False, (f"retired ({e.get('last_reason', '?')}) — "
                       "clear its entry to retry")
    n = int(e.get("attempts") or 0)
    if n >= max_attempts():
        return False, (f"exhausted ({n} failed attempts, last: "
                       f"{e.get('last_reason', '?')}) — clear its entry to retry")
    return True, ""


def record_failure(state: dict, slug: str, reason: str,
                   terminal: bool = False) -> None:
    e = state.setdefault(slug, {"slug": slug, "attempts": 0, "skip": False})
    counted = not reason.startswith(_UNCOUNTED_PREFIX)
    if counted:
        e["attempts"] = int(e.get("attempts") or 0) + 1
        if terminal:
            # No retry can change this outcome; retire at once. Guarded by
            # `counted` so a transport fluke can never retire a firm.
            e["retired"] = True
    e["last_attempt"] = _now()
    e["last_reason"] = reason
    e.setdefault("history", []).append(
        {"at": e["last_attempt"], "reason": reason, "counted": counted})
    e["history"] = e["history"][-_HISTORY_KEEP:]


def clear(state: dict, slug: str) -> bool:
    """Drop a firm's entry (on success). True if there was one."""
    return state.pop(slug, None) is not None


def save(state: dict, store=None) -> None:
    """Write locally always (local runs); commit to the repo when a store is
    available (Railway runs — the commit IS the persistence there)."""
    payload = sorted(state.values(), key=lambda e: e.get("slug", ""))
    try:
        (_DATA / FILE).write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    except OSError:
        pass
    if store is not None:
        store.commit_json(FILE, payload,
                          f"Factory: attempt log ({len(payload)} open)")
