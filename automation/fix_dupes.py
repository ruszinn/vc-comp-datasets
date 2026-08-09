"""Repair the Private Comps duplicates left by the upsert.

For every Data file that has BOTH a named row and a blank-name row:
  - rewrite the named row's Data file to the exact filename (removing the hidden
    whitespace/mismatch that broke the match), then
  - delete the blank-name duplicate.
Blank rows with no named sibling (a genuinely new firm, e.g. Inflection) are kept
and just reported, so you can add a Name by hand.

  python3 automation/fix_dupes.py --dry-run   # show the plan
  python3 automation/fix_dupes.py             # apply
Then re-run:  python3 automation/populate_registry.py
"""
from __future__ import annotations

import argparse
import os

import requests

_API = "https://api.airtable.com/v0"


def _url() -> str:
    base = os.environ["AIRTABLE_BASE_ID"]
    table = os.environ.get("AIRTABLE_TABLE", "Private Comps")
    return f"{_API}/{base}/{requests.utils.quote(table)}"


def _headers() -> dict:
    return {"Authorization": f"Bearer {os.environ['AIRTABLE_PAT']}",
            "Content-Type": "application/json"}


def _all_rows() -> list[dict]:
    rows, offset = [], None
    while True:
        params = {"pageSize": 100, "fields[]": ["Name", "Data file"]}
        if offset:
            params["offset"] = offset
        r = requests.get(_url(), headers=_headers(), params=params, timeout=30)
        r.raise_for_status()
        d = r.json()
        rows += d["records"]
        offset = d.get("offset")
        if not offset:
            return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    by_file: dict[str, list[dict]] = {}
    for rec in _all_rows():
        df = (rec["fields"].get("Data file") or "").strip()
        if df:
            by_file.setdefault(df, []).append(rec)

    to_fix, to_delete, new_firms = [], [], []
    for df, recs in by_file.items():
        named = [r for r in recs if (r["fields"].get("Name") or "").strip()]
        blank = [r for r in recs if not (r["fields"].get("Name") or "").strip()]
        if named and blank:
            to_fix.append((named[0], df))         # rewrite key on the named row
            to_delete += [r["id"] for r in blank]  # drop the dupes
        elif blank and not named:
            new_firms.append(df)

    print(f"rewrite Data file on {len(to_fix)} named rows, "
          f"delete {len(to_delete)} duplicate rows")
    for rec, df in to_fix:
        print(f"  keep {rec['fields'].get('Name')!r:32} set Data file -> {df}")
    if new_firms:
        print(f"\nnew firms with no named row (kept — add a Name by hand):\n  "
              + ", ".join(sorted(new_firms)))

    if args.dry_run:
        print("\n[dry-run] nothing changed.")
        return 0

    # rewrite keys (PATCH, batches of 10)
    fixes = [{"id": r["id"], "fields": {"Data file": df}} for r, df in to_fix]
    for i in range(0, len(fixes), 10):
        r = requests.patch(_url(), headers=_headers(),
                          json={"records": fixes[i:i + 10]}, timeout=30)
        r.raise_for_status()
    # delete dupes (batches of 10)
    for i in range(0, len(to_delete), 10):
        r = requests.delete(_url(), headers=_headers(),
                           params=[("records[]", rid) for rid in to_delete[i:i + 10]],
                           timeout=30)
        r.raise_for_status()
    print(f"\nDone — fixed {len(fixes)} keys, deleted {len(to_delete)} rows.")
    print("Now re-run:  python3 automation/populate_registry.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
