"""Create the Private Comps fields via the Airtable API — one Terminal command
instead of clicking 20 fields by hand.

Creates: the 17 everywhere-tag Number columns, plus Scraper health (single
select), Delta count (number), and Last run (date+time). It also extends the
existing Status single-select with `needs-scraper` and `broke`. Re-runnable:
fields that already exist are skipped, not duplicated.

REQUIREMENTS
- Runs on your Mac (uses the Airtable token). The token needs the
  **schema.bases:write** scope (the backfill token only had data.records scopes),
  and the base added to its access. If you get a 403, that scope is missing —
  edit the token at airtable.com/create/tokens (or make a new one) and re-run.

RUN
  cd "VC comps"
  export AIRTABLE_PAT=pat_xxx
  export AIRTABLE_BASE_ID=appXXXXXXXXXXXXXX
  python3 automation/create_at_fields.py --dry-run   # show what it would do
  python3 automation/create_at_fields.py             # create the fields
"""
from __future__ import annotations

import argparse
import os
import sys

import requests

import tags

_API = "https://api.airtable.com/v0/meta/bases"
_TABLE = os.environ.get("AIRTABLE_TABLE", "Private Comps")

# Fields to create: (name, type, options)
_NUMBER = ("number", {"precision": 0})
FIELDS: list[tuple[str, str, dict]] = (
    [(t, *_NUMBER) for t in tags.TAGS]                       # 17 tag counts
    + [
        ("Scraper health", "singleSelect", {"choices": [
            {"name": c} for c in
            ["new", "grew", "same", "shrank", "count-drop", "broke"]]}),
        ("Delta count", *_NUMBER),                           # allows negatives
        ("Last run", "dateTime", {
            "dateFormat": {"name": "iso"},
            "timeFormat": {"name": "24hour"},
            "timeZone": "utc"}),
    ]
)
# Extend this existing single-select with any missing options.
STATUS_ADD = ["needs-scraper", "broke"]


def _headers() -> dict:
    return {"Authorization": f"Bearer {os.environ['AIRTABLE_PAT']}",
            "Content-Type": "application/json"}


def _get_table(base: str) -> dict:
    r = requests.get(f"{_API}/{base}/tables", headers=_headers(), timeout=30)
    r.raise_for_status()
    for t in r.json()["tables"]:
        if t["name"] == _TABLE:
            return t
    sys.exit(f"Table {_TABLE!r} not found in base {base}. "
             f"Tables: {[t['name'] for t in r.json()['tables']]}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    base = os.environ["AIRTABLE_BASE_ID"]
    table = _get_table(base)
    tid = table["id"]
    existing = {f["name"]: f for f in table["fields"]}
    print(f"Table {_TABLE} ({tid}) — {len(existing)} existing fields\n")

    # 1) create missing fields
    for name, ftype, options in FIELDS:
        if name in existing:
            print(f"  skip   {name} (exists)")
            continue
        if args.dry_run:
            print(f"  CREATE {name}  [{ftype}]")
            continue
        r = requests.post(f"{_API}/{base}/tables/{tid}/fields", headers=_headers(),
                          json={"name": name, "type": ftype, "options": options},
                          timeout=30)
        print(f"  {'OK    ' if r.ok else 'ERR   '} {name}  "
              f"{'' if r.ok else r.status_code}{'' if r.ok else ' ' + r.text[:200]}")

    # 2) extend Status options
    status = existing.get("Status")
    if status and status.get("type") == "singleSelect":
        have = {c["name"] for c in status["options"]["choices"]}
        add = [c for c in STATUS_ADD if c not in have]
        if not add:
            print("\n  Status: already has needs-scraper/broke")
        elif args.dry_run:
            print(f"\n  Status: would add {add}")
        else:
            choices = [{"id": c["id"], "name": c["name"]}
                       for c in status["options"]["choices"]] + \
                      [{"name": c} for c in add]
            r = requests.patch(
                f"{_API}/{base}/tables/{tid}/fields/{status['id']}",
                headers=_headers(), json={"options": {"choices": choices}}, timeout=30)
            print(f"\n  Status +{add}: {'OK' if r.ok else r.status_code + ' ' + r.text[:200]}")

    print("\nDone." if not args.dry_run else "\n[dry-run] nothing changed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
