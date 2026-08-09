"""Direct Airtable writer — upsert firm rows into Private Comps.

Replaces the Zapier hop: the pipeline (and populate_registry) call this to write
straight to Airtable's API, matched on `Data file` so rows update in place instead
of duplicating. It writes ONLY the columns the automation owns — Record count,
Delta count, Scraper health, Status, Last run, and the 17 tag counts — and never
Name / Source URL / Notes, so your hand-entered firm data is preserved.

Broken-scrape safety: an error firm's row carries no tag counts, so those columns
are simply not sent (left as they were) rather than zeroed out.

Env (set on whatever host runs the pipeline — e.g. Railway service variables):
  AIRTABLE_PAT       token with data.records:write on the base
  AIRTABLE_BASE_ID   appXXXXXXXXXXXXXX
  AIRTABLE_TABLE     defaults to "Private Comps"
"""
from __future__ import annotations

import os
import time

import requests

import names
import tags

_API = "https://api.airtable.com/v0"


def _fields(row: dict, run_at: str) -> dict:
    f = {
        "Data file": row.get("data_file"),
        "Record count": row.get("record_count"),
        "Delta count": row.get("delta"),
        "Scraper health": row.get("health"),
        "Status": row.get("status"),
        "Last run": run_at,
    }
    for t in tags.TAGS:               # only send tag counts the row actually computed
        if t in row:
            f[t] = row[t]
    return {k: v for k, v in f.items() if v is not None}


def upsert_firms(rows: list[dict], run_at: str, dry_run: bool = False) -> int:
    """Upsert each firm into Private Comps, matched on Data file. Batches of 10
    (Airtable's per-request limit). Returns count written."""
    table = os.environ.get("AIRTABLE_TABLE", "Private Comps")
    if dry_run:
        print(f"[airtable] dry-run: would upsert {len(rows)} firms into {table!r}")
        return 0

    base = os.environ["AIRTABLE_BASE_ID"]
    url = f"{_API}/{base}/{requests.utils.quote(table)}"
    headers = {"Authorization": f"Bearer {os.environ['AIRTABLE_PAT']}",
               "Content-Type": "application/json"}

    n = 0
    for i in range(0, len(rows), 10):
        records = [{"fields": _fields(r, run_at)} for r in rows[i:i + 10]]
        body = {"performUpsert": {"fieldsToMergeOn": ["Data file"]},
                "records": records, "typecast": True}
        r = requests.patch(url, headers=headers, json=body, timeout=30)
        if not r.ok:
            print(f"[airtable] ERROR {r.status_code}: {r.text[:400]}")
            r.raise_for_status()
        n += len(records)
        time.sleep(0.2)

    filled = _fill_blank_meta(url, headers, rows)
    msg = f"[airtable] upserted {n} firms into {table!r}"
    if filled:
        msg += f"; auto-filled metadata on {filled} row(s)"
    print(msg)
    return n


# Registry-metadata columns the automation auto-fills when BLANK, and how each is
# derived. Existing (non-blank) values are never overwritten — with ONE narrow
# exception, `_stale_note` below.
_META_FIELDS = ("Name", "Source URL", "Notes", "Source type", "Scraper module")


def _stale_note(cur: str) -> bool:
    """The one non-blank value the auto-fill DOES overwrite: a github.com blob
    link in Notes, written by the old notes_url while a private repo was
    assumed. Blob links serve HTML, so the dashboard agent can't read them; the
    repo is public and the raw form is the point. Matching on the exact blob
    prefix means hand-written notes are never touched."""
    repo = os.environ.get("GITHUB_REPO", "michaeleverywhere/vc-comp")
    return cur.startswith(f"https://github.com/{repo}/blob/")


def _derive(field: str, df: str, row: dict) -> str | None:
    if field == "Name":
        return names.display_name(df)
    if field == "Source URL":
        # current run's value if present, else read it from the dataset (back-fill)
        return row.get("source_url") or names.source_url(df)
    if field == "Notes":
        return names.notes_url(df)
    if field == "Source type":
        return names.source_type(df)
    if field == "Scraper module":
        return names.scraper_module(df)
    return None


_MIN_KEEP = 10   # a keep-set smaller than this means "the dataset glob broke",
                 # not "the repo shrank to nine firms" — refuse to mass-delete


def delete_firms(data_files: list[str], dry_run: bool = False) -> int:
    """Delete the rows for these Data files (used when a firm retires with NO
    dataset — Airtable is "firms with data", not a graveyard of attempts; user
    decision 2026-07-27). Matching on Data file, same key the upsert merges on.
    Tolerates rows that don't exist (a re-run or a pre-cleaned row is fine)."""
    want = {d for d in data_files if d}
    if not want:
        return 0
    return _delete_where(lambda df: df in want, dry_run, label="no-data")


def delete_strays(keep_data_files, dry_run: bool = False) -> int:
    """Reconcile: delete every row whose Data file is set but NOT in
    `keep_data_files` (= repo datasets + candidates with pending work). The
    delete-on-retire path handles the common case the same night; this pass is
    the backstop that clears rows stranded by earlier designs, skipped runs, or
    a delete that failed. Refuses a suspiciously small keep-set — a bug in the
    caller's glob would otherwise empty the whole table."""
    keep = {d for d in keep_data_files if d}
    if len(keep) < _MIN_KEEP:
        print(f"[airtable] reconcile SKIPPED: keep-set suspiciously small "
              f"({len(keep)} < {_MIN_KEEP}) — refusing to mass-delete")
        return 0
    return _delete_where(lambda df: df not in keep, dry_run, label="stray")


def _delete_where(pred, dry_run: bool, label: str) -> int:
    """List rows, delete those whose non-blank Data file satisfies `pred`.
    Blank Data files are never touched (could be a hand-made row). Never raises
    into the pipeline: losing a delete costs one stale row, which the next
    reconcile pass removes — not worth failing a run over."""
    table = os.environ.get("AIRTABLE_TABLE", "Private Comps")
    if dry_run:
        print(f"[airtable] dry-run: not deleting {label} rows from {table!r}")
        return 0
    base = os.environ["AIRTABLE_BASE_ID"]
    url = f"{_API}/{base}/{requests.utils.quote(table)}"
    headers = {"Authorization": f"Bearer {os.environ['AIRTABLE_PAT']}"}
    try:
        ids, offset = [], None
        while True:
            params = {"pageSize": 100, "fields[]": ["Data file"]}
            if offset:
                params["offset"] = offset
            r = requests.get(url, headers=headers, params=params, timeout=30)
            r.raise_for_status()
            d = r.json()
            ids += [rec["id"] for rec in d["records"]
                    if (df := (rec["fields"].get("Data file") or "").strip())
                    and pred(df)]
            offset = d.get("offset")
            if not offset:
                break
        for i in range(0, len(ids), 10):
            r = requests.delete(url, headers=headers, timeout=30,
                                params={"records[]": ids[i:i + 10]})
            r.raise_for_status()
            time.sleep(0.2)
        if ids:
            print(f"[airtable] deleted {len(ids)} {label} row(s) from {table!r}")
        return len(ids)
    except Exception as exc:  # noqa: BLE001
        print(f"[airtable] delete FAILED (stale row remains): {exc}")
        return 0


def _fill_blank_meta(url: str, headers: dict, rows: list[dict]) -> int:
    """Fill every BLANK metadata cell (Name, Source URL, Notes, Source type,
    Scraper module) on firm rows — so a new firm needs zero manual entry. Never
    overwrites a non-blank cell. Needs data.records:read; skipped if not granted."""
    by_df = {r.get("data_file"): r for r in rows if r.get("data_file")}
    at_rows, offset = [], None
    try:
        while True:
            params = {"pageSize": 100,
                      "fields[]": list(_META_FIELDS) + ["Data file"]}
            if offset:
                params["offset"] = offset
            r = requests.get(url, headers=headers, params=params, timeout=30)
            r.raise_for_status()
            d = r.json()
            at_rows += d["records"]
            offset = d.get("offset")
            if not offset:
                break
    except requests.HTTPError as exc:
        print(f"[airtable] skipped auto-fill (read not permitted: {exc})")
        return 0

    updates = []
    for rec in at_rows:
        f = rec["fields"]
        df = (f.get("Data file") or "").strip()
        if not df:
            continue
        fills = {}
        for field in _META_FIELDS:
            cur = (f.get(field) or "").strip()
            if not cur or (field == "Notes" and _stale_note(cur)):
                val = _derive(field, df, by_df.get(df, {}))
                if val and val != cur:
                    fills[field] = val
        if fills:
            updates.append({"id": rec["id"], "fields": fills})

    for i in range(0, len(updates), 10):
        r = requests.patch(url, headers=headers,
                          json={"records": updates[i:i + 10], "typecast": True},
                          timeout=30)
        r.raise_for_status()
        time.sleep(0.2)
    return len(updates)
