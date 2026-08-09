"""One-time bulk load of the existing ~12.5k companies into Airtable.

RUN THIS ON YOUR OWN MACHINE, ONCE — never on Railway. It is the only piece that
needs the Airtable Personal Access Token (PAT); keeping it here means that
credential lives on your laptop for one run and is never deployed. After this,
the nightly pipeline handles only deltas and Railway never sees the PAT.

It reads every data/<slug>_companies.json and creates one row per company in the
Portfolio Companies table, keyed by  <firm_slug>|<company_key>  (identity.row_key)
so the nightly upsert finds and updates the same rows later.

Setup:
  pip install requests
  export AIRTABLE_PAT=pat_xxx
  export AIRTABLE_BASE_ID=appXXXXXXXXXXXXXX
  export AIRTABLE_TABLE="Portfolio Companies"
  python3 automation/backfill_airtable.py --dry-run   # count only
  python3 automation/backfill_airtable.py             # load
  python3 automation/backfill_airtable.py --only accel,usv
"""
from __future__ import annotations

import argparse
import json
import os
import time
from datetime import date
from pathlib import Path

import requests

import identity

_DATA = Path(__file__).resolve().parent.parent / "data"
_SUFFIX = "_companies.json"
_API = "https://api.airtable.com/v0"


def _rows_for(slug: str, today: str) -> list[dict]:
    rows = []
    for rec in json.loads((_DATA / f"{slug}{_SUFFIX}").read_text()):
        name = identity.company_name(rec)
        if not name:
            continue
        rows.append({"fields": {
            "Key": identity.row_key(slug, rec),
            "Company": name,
            "Firm": slug,
            "Company URL": identity.company_url(rec),
            "Description": identity.company_desc(rec),
            "Change": "exited" if identity.is_exited(rec) else "active",
            "First seen": today,
            "Last seen": today,
            "Source file": f"{slug}{_SUFFIX}",
        }})
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", type=str, default="")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    slugs = sorted(p.name[: -len(_SUFFIX)] for p in _DATA.glob(f"*{_SUFFIX}"))
    if args.only:
        want = {s.strip() for s in args.only.split(",")}
        slugs = [s for s in slugs if s in want]

    today = date.today().isoformat()
    all_rows: list[dict] = []
    for slug in slugs:
        rows = _rows_for(slug, today)
        all_rows += rows
        print(f"{slug}: {len(rows)}")
    print(f"\nTOTAL: {len(all_rows)} rows across {len(slugs)} firms")

    if args.dry_run:
        print("[dry-run] nothing written")
        return 0

    base, table = os.environ["AIRTABLE_BASE_ID"], os.environ.get("AIRTABLE_TABLE", "Portfolio Companies")
    url = f"{_API}/{base}/{requests.utils.quote(table)}"
    headers = {"Authorization": f"Bearer {os.environ['AIRTABLE_PAT']}",
               "Content-Type": "application/json"}
    created = 0
    for i in range(0, len(all_rows), 10):        # Airtable: max 10 records/create
        r = requests.post(url, headers=headers,
                          json={"records": all_rows[i:i + 10], "typecast": True}, timeout=30)
        if r.status_code != 200:
            print(f"  ERROR at {i}: {r.status_code} {r.text[:300]}")
            r.raise_for_status()
        created += len(all_rows[i:i + 10])
        if created % 500 == 0:
            print(f"  … {created}/{len(all_rows)}")
        time.sleep(0.25)
    print(f"Done — created {created} rows.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
