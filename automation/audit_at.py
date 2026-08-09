"""Audit Private Comps after the upsert — find why rows duplicated.

Reads every row and flags three things:
  * automation duplicates — a row with a Data file but a BLANK Name (created by the
    upsert because it couldn't match an existing row);
  * originals missing the key — a NAMED row with no Data file (the reason its firm
    duplicated); these are the rows to fix;
  * any Data file value that appears on more than one row.

Read-only. Needs AIRTABLE_PAT (data.records:read) + AIRTABLE_BASE_ID (+ AIRTABLE_TABLE).

  python3 automation/audit_at.py
"""
from __future__ import annotations

import os

import requests

_API = "https://api.airtable.com/v0"


def _all_rows() -> list[dict]:
    base = os.environ["AIRTABLE_BASE_ID"]
    table = os.environ.get("AIRTABLE_TABLE", "Private Comps")
    url = f"{_API}/{base}/{requests.utils.quote(table)}"
    headers = {"Authorization": f"Bearer {os.environ['AIRTABLE_PAT']}"}
    rows, offset = [], None
    while True:
        params = {"pageSize": 100, "fields[]": ["Name", "Data file"]}
        if offset:
            params["offset"] = offset
        r = requests.get(url, headers=headers, params=params, timeout=30)
        r.raise_for_status()
        data = r.json()
        rows += data["records"]
        offset = data.get("offset")
        if not offset:
            return rows


def main() -> int:
    rows = _all_rows()
    named_no_file, file_no_name, by_file = [], [], {}
    for rec in rows:
        f = rec["fields"]
        name = (f.get("Name") or "").strip()
        df = (f.get("Data file") or "").strip()
        if name and not df:
            named_no_file.append(name)
        if df and not name:
            file_no_name.append(df)
        if df:
            by_file.setdefault(df, []).append(name or "(blank)")

    dups = {k: v for k, v in by_file.items() if len(v) > 1}

    print(f"total rows: {len(rows)}\n")
    print(f"NAMED rows missing Data file ({len(named_no_file)}) — these are why firms "
          f"duplicated:\n  " + (", ".join(sorted(named_no_file)) or "(none)"))
    print(f"\nBLANK-name rows with a Data file ({len(file_no_name)}) — the automation "
          f"duplicates to delete:\n  " + (", ".join(sorted(file_no_name)) or "(none)"))
    print(f"\nData files on >1 row ({len(dups)}):")
    for k in sorted(dups):
        print(f"  {k}: {dups[k]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
