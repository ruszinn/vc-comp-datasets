"""One-time (and re-runnable) full populate of Private Comps.

The nightly pipeline only writes firms that CHANGED, so it won't fill the 17 tag
columns for all 48 firms on its own. This reads the CURRENT data files (no
re-scrape), builds a row per firm — record count + the 17 tag counts — and upserts
them all straight into Airtable (matched on Data file). Safe to re-run any time to
force a full re-sync.

Run:
  cd "VC comps"
  set -a; source automation/.env; set +a
  python3 automation/populate_registry.py --dry-run   # counts only, no write
  python3 automation/populate_registry.py             # upsert all firms
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import airtable_writer
import tags

_DATA = Path(__file__).resolve().parent.parent / "data"
_SUFFIX = "_companies.json"


def _raw_url(data_file: str) -> str:
    repo = os.environ.get("GITHUB_REPO", "michaeleverywhere/vc-comp")
    branch = os.environ.get("GITHUB_BRANCH", "main")
    ddir = os.environ.get("GITHUB_DATA_DIR", "data")
    return f"https://raw.githubusercontent.com/{repo}/{branch}/{ddir}/{data_file}"


def _rows() -> list[dict]:
    rows = []
    for p in sorted(_DATA.glob(f"*{_SUFFIX}")) + list(_DATA.glob("companies.json")):
        try:
            recs = json.loads(p.read_text())
        except Exception:  # noqa: BLE001
            continue
        if not isinstance(recs, list):
            continue
        slug = p.name[: -len(_SUFFIX)] if p.name.endswith(_SUFFIX) else "lightspeed"
        source_url = next((r.get("source_url") for r in recs if r.get("source_url")), None)
        row = {
            "firm_slug": slug, "data_file": p.name, "firm_name": slug,
            "record_count": len(recs), "prev_count": len(recs), "delta": 0,
            "health": "same", "status": "active", "is_new": False,
            "output_url": _raw_url(p.name), "source_url": source_url,
        }
        row.update(tags.count_tags(recs))   # 17 flat tag-count keys
        rows.append(row)
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    rows = _rows()
    total = sum(r["record_count"] for r in rows)
    print(f"{len(rows)} firms, {total} companies total")
    for r in rows[:3]:
        hot = ", ".join(f"{t}={r[t]}" for t in tags.TAGS if r[t])
        print(f"  {r['data_file']}: {r['record_count']} cos  [{hot}]")

    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    airtable_writer.upsert_firms(rows, now, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
