"""Diff — compare a firm's previous company list to its fresh one, and rate the
scrape's health. Uses identity.py for every key/name/exit decision, so none of
that logic is duplicated here.

A brand-new firm arrives with `old = []`: every company is "added" and the
registry row is marked `new`. That's the whole trick that lets one pass cover
both discovery and refresh with no separate code path.
"""
from __future__ import annotations

from typing import Optional

import identity


def _index(records: list[dict]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for r in records:
        k = identity.company_key(r)
        if k and k not in out:
            out[k] = r
    return out


def _slim(record: dict, slug: str, data_file: str, change: str) -> dict:
    """Compact, Zapier-ready projection. Includes the Airtable upsert key so the
    Zap needs no Formatter step."""
    return {
        "key": identity.row_key(slug, record),
        "firm_slug": slug,
        "firm_data_file": data_file,
        "change": change,                      # added | dropped | exited
        "company_name": identity.company_name(record),
        "company_url": identity.company_url(record),
        "description": identity.company_desc(record),
    }


def diff_firm(slug: str, data_file: str,
              old_records: list[dict], new_records: list[dict]) -> dict:
    old, new = _index(old_records), _index(new_records)
    added = [_slim(new[k], slug, data_file, "added") for k in new if k not in old]
    dropped = [_slim(old[k], slug, data_file, "dropped") for k in old if k not in new]
    exited = [
        _slim(new[k], slug, data_file, "exited")
        for k in new
        if k in old and not identity.is_exited(old[k]) and identity.is_exited(new[k])
    ]
    return {"added": added, "dropped": dropped, "exited": exited}


def registry_health(slug: str, data_file: str,
                    old_records: list[dict],
                    new_records: Optional[list[dict]],
                    error: Optional[str] = None) -> dict:
    """Per-firm registry row. `safe_to_commit` is False for broke/count-drop so a
    bad scrape never overwrites good data. `is_new` True when we had no prior file."""
    prev = len(old_records)
    is_new = prev == 0

    if error is not None or new_records is None:
        return _row(slug, data_file, prev, prev, 0, "broke", False, is_new, error or "no output")

    count = len(new_records)
    delta = count - prev
    if prev and count == 0:
        health, safe = "broke", False
    elif prev >= 25 and delta < 0 and abs(delta) > 0.20 * prev:
        health, safe = "count-drop", False
    elif is_new:
        health, safe = "new", True
    elif delta > 0:
        health, safe = "grew", True
    elif delta < 0:
        health, safe = "shrank", True
    else:
        health, safe = "same", True
    return _row(slug, data_file, count, prev, delta, health, safe, is_new, "")


def _row(slug, data_file, count, prev, delta, health, safe, is_new, detail) -> dict:
    return {
        "firm_slug": slug, "data_file": data_file,
        "record_count": count, "prev_count": prev, "delta": delta,
        "health": health, "safe_to_commit": safe, "is_new": is_new, "detail": detail,
    }
