# VC comps automation — one self-contained nightly pipeline

> **PARTIALLY STALE (2026-07-24):** the Zapier/catch-hook sections below are retired —
> the pipeline now writes **directly to Airtable** (`airtable_writer.py`), the sync target
> is the **Private Comps** table (not "Portfolio Companies"), and a Claude-API **scraper
> factory** auto-generates bespoke scrapers in discover mode. See **CLAUDE.md §Automation**
> for the authoritative current design and session-handoff state.

Discovery **and** refresh in a single pass, purpose-built for this repo. One
service, one loop, no work done twice.

## The one idea
**A new firm is a firm whose previous dataset is empty.** So one loop covers both:
- an existing firm → diff its fresh list vs GitHub → added / dropped / exited;
- a brand-new firm → its "previous" is `[]` → everything is *added*, registry row
  marked `new`.

No separate discovery service, no second phase, no double dedup.

## Flow
```
candidates.json ─┐
                 ├─► roster (dedup once) ─► for each firm, exactly once:
scripts/*_scraper.py ┘        │
                              ├─ bespoke  → run scripts/<slug>_scraper.py
                              └─ generic  → resolve portfolio URL → extract
                              │
                        diff vs GitHub  ─►  commit if safe & changed
                              │
                    one payload ─► one Zapier Catch Hook ─► Airtable upserts
                                                            ├─ VC Comp Portcos (registry)
                                                            └─ Portfolio Companies (deltas)
```

## Modules (all self-contained; nothing imports another service)
| file | role |
|---|---|
| `pipeline.py` | the cron entrypoint — the single pass above |
| `roster.py` | builds the one firm list (bespoke + new candidates), **deduped once** |
| `identity.py` | the *only* place slug / company-key / exit rules live |
| `gh.py` | this automation's own GitHub Contents client (list / read / commit / raw url) |
| `extract.py` | requests-only: resolve a portfolio URL + generic-extract companies |
| `diff.py` | added / dropped / exited + registry health (uses `identity`) |
| `zapier_client.py` | fire-only, chunked POST to the one Catch Hook |
| `candidates.json` | discovery queue (edit by hand; `newsletter-scout` appends) |
| `backfill_airtable.py` | ONE-TIME local bulk load (Airtable PAT on your Mac) |

## Where redundancy was removed
- **Dedup once.** Only `roster.py` decides "already have this firm," from the
  `data/<slug>_companies.json` filenames. Airtable needs no Find-Record step
  because the writes are **upserts** keyed on a stable id.
- **Fetch once.** A firm's page is fetched by exactly one path (its bespoke scraper
  *or* the generic extractor) — never scraped by a finder and again by a service.
- **One pass, one service.** Discovery and refresh are the same loop in one cron
  job — no separate `/scrape` web service exists.
- **One Zapier round-trip.** One Catch Hook; the summary/registry envelope and the
  company chunks all land there; two Zaps split on the `part` field.

## Secret model (unchanged, minimal)
The pipeline holds two secrets: a GitHub PAT scoped to this one repo, and a
fire-only Zapier hook URL. Neither can read Airtable or Drive. The Airtable PAT
lives only in Zapier (nightly writes) and on your laptop (one-time backfill). No
Google credential anywhere.

---

## Airtable side

### Table A — `VC Comp Portcos` (registry, existing) — add:
| field | type |
|---|---|
| Record count / Prev count / Δ count | Number |
| Health | Single select: `new` `grew` `same` `shrank` `count-drop` `broke` |
| Status | Single select: `active` `needs-scraper` `broke` |
| Last run | Date (with time) · Last commit | Single line (sha) |

### Table B — `Portfolio Companies` (new):
| field | type | notes |
|---|---|---|
| **Key** | Single line (primary) | `firm_slug\|company_key` — the upsert match field |
| Company / Firm / Company URL / Description | text / URL / long text | |
| Change | Single select: `new` `active` `dropped` `exited` | |
| First seen / Last seen | Date | |
| Source file | Single line | `<slug>_companies.json` |

Load Table B once with `backfill_airtable.py`; nightly deltas keep it current.

### Zapier — one Catch Hook, one Zap, two Paths (no Find-Record)
Put the hook URL in `ZAPIER_CATCH_HOOK_URL`. Trigger = Webhooks by Zapier → Catch
Hook, then a **Paths** step branches on the `part` field.

**Path A — companies** (`part = companies`). Loop over
`companies`. Airtable **Create Record with "Use a field to match records" = Key**
(that's an upsert): map Key ← `{{key}}`, Company, Firm, Company URL, Description,
Change, Source file ← `firm_data_file`, Last seen ← `run_at`, First seen ← `run_at`.
Matching on Key makes it idempotent — updates on repeat, never duplicates. The same
action handles added/dropped/exited (it just sets `Change`).

**Path B — registry** (`part = summary`). Loop over `registry`. Airtable **Create Record with match = Data file** (upsert): map Name ←
`firm_name`, Source URL ← `source_url`, Record count / Prev count / Δ ← `delta`,
Health, Status, Output URL ← `output_url`, Last commit ← `commit_sha`, Last run ←
`run_at`. A brand-new firm's row is *created* here (Status `active`/`new`); an
existing firm's row is *updated*.

### Airtable views = your alerts (no push)
- `Portfolio Companies`: **New this week** (`Change` in [new, added] & First seen ≤ 7d);
  **Dropped / Exited** (`Change` in [dropped, exited] & Last seen ≤ 14d).
- `VC Comp Portcos`: **Broken scrapers** (`Health` in [broke, count-drop] or
  `Status` = needs-scraper); **Moved last night** (`Last run` = today & Δ ≠ 0).

---

## Run it
```bash
cd "VC comps"
pip install -r automation/requirements.txt
cp automation/.env.example automation/.env   # fill in GITHUB_TOKEN, ZAPIER_CATCH_HOOK_URL
set -a; source automation/.env; set +a

python3 automation/pipeline.py --dry-run --limit 3   # scrape+diff, no writes
python3 automation/pipeline.py --only accel,felicis  # a couple firms, live
python3 automation/pipeline.py                       # full nightly pass
```
Deploy: one Railway service, **Root Directory = repo root**, variables from
`.env.example`, schedule from `railway.toml` (`cron`). Run `backfill_airtable.py`
from your laptop once before the first cron.

## Honest limits
- **Generic extraction is requests-only.** Server-rendered portfolio pages extract
  well; heavy-JS sites return little and come back `needs-scraper` — write a
  `scripts/<slug>_scraper.py` for those (repo Quickstart), after which the firm is
  handled as bespoke automatically. Generic records carry name + URL only until a
  bespoke scraper enriches them.
- **`dropped`** means "left the public list" — usually an exit or a site cull, but
  occasionally just a re-slugged card. `exited` (a status flip) is the higher-
  confidence signal.
- **Health guard** catches empties and >20% craters, not subtler wrong-but-plausible
  scrapes.
