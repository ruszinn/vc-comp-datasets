# CLAUDE.md — VC portfolio datasets & scrapers

Guidance for working in this repo. Read this first; deeper build methodology is in
[`scripts/PLAYBOOK.md`](scripts/PLAYBOOK.md).

## What this repo is
Structured JSON datasets of VC firms' portfolio companies, plus the reusable Python
scrapers that produce them. Each dataset is one JSON array, one object per company.

Current datasets (in `data/`):
| firm | file | records | source |
|---|---|---|---|
| Lightspeed | `companies.json` | 425 | lsvp.com (built from sitemap; no script) |
| USV | `usv_companies.json` | 214 | usv.com/companies (`usv_scraper.py`) |
| Menlo Ventures | `menlo_companies.json` | 239 | menlovc.com/portfolio (`menlo_scraper.py`) |
| Insight Partners | `insight_companies.json` | 847 | insightpartners.com/portfolio (`insight_scraper.py`) |
| RRE Ventures | `rre_companies.json` | 250 | rre.com/portfolio (`rre_scraper.py`) |
| Founders Fund | `foundersfund_companies.json` | 62 | foundersfund.com/portfolio (`foundersfund_scraper.py`) |
| ICONIQ Growth | `iconiq_companies.json` | 100 | iconiq.com/growth/companies (`iconiq_scraper.py`) |
| Sequoia Capital | `sequoia_companies.json` | 412 | sequoiacap.com/our-companies (`sequoia_scraper.py`) |
| Andreessen Horowitz | `a16z_companies.json` | 849 | a16z.com/portfolio (`a16z_scraper.py`) |
| Accel | `accel_companies.json` | 766 | accel.com — own Sanity CMS API (`accel_scraper.py`) |
| Index Ventures | `index_companies.json` | 311 | indexventures.com/companies (`index_scraper.py`) |
| Kleiner Perkins | `kleinerperkins_companies.json` | 385 | kleinerperkins.com/partnerships (`kleinerperkins_scraper.py`) |
| NEA | `nea_companies.json` | 903 | nea.com — Statamic GraphQL (`nea_scraper.py`) |
| Greylock | `greylock_companies.json` | 159 | greylock.com/portfolio (`greylock_scraper.py`) |
| Bessemer | `bessemer_companies.json` | 516 | bvp.com/companies (`bessemer_scraper.py`) |
| Khosla Ventures | `khosla_companies.json` | 132 | khoslaventures.com sector pages (`khosla_scraper.py`) |
| General Catalyst | `generalcatalyst_companies.json` | 584 | generalcatalyst.com — own Algolia index (`generalcatalyst_scraper.py`) |
| Ribbit Capital | `ribbit_companies.json` | 148 | ribbitcap.com/rebels (`ribbit_scraper.py`) |
| Parkway VC | `parkway_companies.json` | 25 | parkway.vc/portfolio (`parkway_scraper.py`) |
| General Atlantic | `generalatlantic_companies.json` | 405 | generalatlantic.com/investments (`generalatlantic_scraper.py`) |
| Notable Capital | `notable_companies.json` | 127 | notablecap.com/companies (`notable_scraper.py`) |
| IVP | `ivp_companies.json` | 156 | ivp.com/portfolio `_payload.json` (`ivp_scraper.py`) |
| Dragoneer | `dragoneer_companies.json` | 29 | dragoneer.com/companies — curated subset (`dragoneer_scraper.py`) |
| Mayfield | `mayfield_companies.json` | 135 | mayfield.com/meet-our-founders (`mayfield_scraper.py`) |
| OrbiMed | `orbimed_companies.json` | 200 | orbimed.com/portfolio (`orbimed_scraper.py`) |
| Coatue | `coatue_companies.json` | 372 | coatue.com/portfolio API (`coatue_scraper.py`) |
| Spark Capital | `spark_companies.json` | 48 | sparkcapital.com/companies (`spark_scraper.py`) |
| SV Angel | `svangel_companies.json` | 150 | svangel.com/portfolio (`svangel_scraper.py`) |
| Battery Ventures | `battery_companies.json` | 343 | battery.com/company admin-ajax (`battery_scraper.py`) |
| Bedrock | `bedrock_companies.json` | 6 | bedrockcap.com/investments — full disclosure (`bedrock_scraper.py`) |
| Paradigm | `paradigm_companies.json` | 105 | paradigm.xyz/investments (`paradigm_scraper.py`) |
| Oak HC/FT | `oakhcft_companies.json` | 107 | oakhcft.com/portfolio (`oakhcft_scraper.py`) |
| Atlas Venture | `atlas_companies.json` | 79 | atlasventure.com/portfolio (`atlas_scraper.py`) |
| Venrock | `venrock_companies.json` | 250 | venrock.com — WP REST (`venrock_scraper.py`) |
| Meritech | `meritech_companies.json` | 48 | meritechcapital.com/companies (`meritech_scraper.py`) |
| Norwest | `norwest_companies.json` | 514 | norwest.com/companies (`norwest_scraper.py`) |
| CRV | `crv_companies.json` | 183 | crv.com/companies — RSC payload (`crv_scraper.py`) |
| Bain Capital Ventures | `baincapital_companies.json` | 269 | baincapitalventures.com — own Sanity API (`baincapital_scraper.py`) |
| Inflection Ventures | `inflection_companies.json` | 16 | inflectionvc.com/portfolio (`inflection_scraper.py`) |
| First Round Capital | `firstround_companies.json` | 190 | firstround.com/companies (`firstround_scraper.py`) |
| 8VC | `8vc_companies.json` | 172 | 8vc.com/companies (`8vc_scraper.py`) |
| TCV | `tcv_companies.json` | 151 | tcv.com/partnerships (`tcv_scraper.py`) |
| Lux Capital | `lux_companies.json` | 215 | luxcapital.com/companies via sitemap (`lux_scraper.py`) |
| ARCH Venture Partners | `arch_companies.json` | 128 | archventure.com/portfolio (`arch_scraper.py`) |
| Afore Capital | `afore_companies.json` | 100 | afore.vc/portfolio (`afore_scraper.py`) |
| Lerer Hippeau | `lererhippeau_companies.json` | 305 | lererhippeau.com/portfolio (`lererhippeau_scraper.py`) |
| 2048 Ventures | `2048_companies.json` | 75 | 2048.vc/companies (`2048_scraper.py`) |
| Hustle Fund | `hustlefund_companies.json` | 335 | hustlefund.vc/founders (`hustlefund_scraper.py`) |
| Amplify Partners | `amplifypartners_companies.json` | 114 | amplifypartners.com/portfolio/company (auto-gen `amplifypartners_scraper.py`) |
| Felicis | `felicis_companies.json` | 275 | felicis.com/portfolio (auto-gen `felicis_scraper.py`) |
| Foundry Group | `foundrygroup_companies.json` | 56 | foundrygroup.com/portfolio (auto-gen `foundrygroup_scraper.py`) |
| Homebrew | `homebrew_companies.json` | 162 | homebrew.co (auto-gen `homebrew_scraper.py`) |
| Wing VC | `wingvc_companies.json` | 99 | wing.vc (auto-gen `wingvc_scraper.py`) |
| Matrix Partners | `matrixpartners_companies.json` | 45 | matrixpartners.com (auto-gen `matrixpartners_scraper.py`) |
| True Ventures | `trueventures_companies.json` | 50 | trueventures.com (auto-gen `trueventures_scraper.py`) |
| Notation Capital | `notationcapital_companies.json` | 77 | notation.vc (auto-gen `notationcapital_scraper.py`) |
| Gradient Ventures | `gradientventures_companies.json` | 130 | gradientventures.com (auto-gen `gradientventures_scraper.py`) |
| Mosaic Ventures | `mosaicventures_companies.json` | 79 | mosaicventures.com (auto-gen `mosaicventures_scraper.py`) |
| Storm Ventures | `stormventures_companies.json` | 100 | stormventures.com (auto-gen `stormventures_scraper.py`) |
| Primary Venture Partners | `primaryventurepartners_companies.json` | 25 | primary.vc (auto-gen `primaryventurepartners_scraper.py`) |
| Point Nine Capital | `pointninecapital_companies.json` | 185 | pointnine.com/companies (`pointninecapital_scraper.py` — hand-written 2026-08-03, replacing a factory scraper whose 25 records were all junk) |

**61 datasets, 14,001 companies, 98.0% carrying `everywhere_tags`** (2026-08-03).

**Retired and DELETED** (bespoke-or-nothing): SignalFire — burned all 4 factory attempts on
"description coverage < 30%", so `_retire_fully` removed the dataset on 2026-07-30. Do not
re-add it without a hand-written scraper. Wing VC went the other way on the same re-armed
burst and graduated bespoke.

**Firms verified to publish NO portfolio on their own site** (no dataset possible under the
no-third-party-sources rule): Benchmark, Thrive Capital, DST Global, Tiger Global, Altimeter,
Sutter Hill Ventures, Greenoaks. Each was exhaustively checked (sitemaps, guessed paths,
embedded JS) — their sites are minimal brochures / gated LP portals.

**Network note (2026-07-01):** this machine intermittently cannot route to Webflow's current
CDN IP (`cdn.webflow.com` → 198.202.211.1). Affected scrapers (`parkway`, `khosla`, `spark`,
`oakhcft`, `8vc`, `lux`, `dragoneer`, also existing `rre`/`iconiq`) implement a fallback chain:
direct HTTPS → legacy-IP pin (75.2.70.75) → `r.jina.ai` read-only relay of the same page. On a
healthy network they use the direct route. Datasets fetched relay-only at build time: 8VC, Lux,
Dragoneer, Spark (spot-checked correct; re-run to refresh when routing is healthy). Afore
(`afore_companies.json`, added 2026-07-22) was transcribed from afore.vc's own portfolio page via
a read-only fetch relay for the same reason — its `afore_scraper.py` carries the standard
direct→legacy-IP→r.jina.ai fallback chain and re-derives straight from the source HTML on a
healthy network (verify the Finsweet card selectors against live markup on the first clean re-run).

## Layout
```
VC comps/
├── CLAUDE.md                 ← this file
├── railway.toml              ← Railway build/cron config (repo root; see §Automation)
├── requirements.txt          ← repo-root deps for Railway (requests, bs4)
├── data/                     ← ALL JSON (datasets + reports)
│   ├── companies.json + 51× <firm>_companies.json   (one per firm — see table above)
│   ├── + 2 thin datasets left: wingvc + signalfire — both RE-ARMED 2026-07-28
│   │     with a burst of 2 strong-model tries each (attempts set to 2 in
│   │     gen_attempts.json); bespoke-or-nothing: if they fail again, dataset,
│   │     Airtable row and all are deleted
│   ├── gen_attempts.json               ← factory attempt memory (see §Automation)
│   ├── discovered_candidates.json      ← auto-found firm queue (see §Automation);
│   │     in data/ NOT automation/ on purpose — Watch Paths would self-redeploy
│   ├── scrape_attempts.json            ← generic-scrape memory (§Automation)
│   ├── spend.json                      ← month-to-date API spend (§Automation)
│   ├── enrichment_report.json          ← provenance for enrich.py fills
│   └── everywhere_tagging_report.json  ← Lightspeed tagging report
├── scripts/                  ← per-firm scrapers (51 bespoke, 4 of them factory
│                               auto-generated; source in each docstring)
│   ├── enrich.py             ← Wikidata back-fill of empty fields
│   └── PLAYBOOK.md           ← how to scrape a new firm / per-source cheat-sheet
└── automation/               ← nightly Railway pipeline (see §Automation below)
```
Scripts resolve `../data` relative to their own file, so run from the repo root:
`python3 scripts/usv_scraper.py` (writes `data/usv_companies.json`). Each scraper has a
`--limit N` flag for quick test runs. Deps: `pip install requests beautifulsoup4`.

## Core principles (do not violate)
- **Site-tailored schema.** Only include fields the source actually exposes. Don't force
  every firm into the same shape; each `*_companies.json` has its own field set.
- **Never fabricate.** Missing scalar → `null`; missing list → `[]`. If the site doesn't
  publish it, leave it empty.
- **`scraped_at`** = the real run timestamp (ISO-8601 UTC). If genuinely unknown, `null` —
  never a guessed/today's date passed off as the scrape time. (Lightspeed `companies.json`
  has `scraped_at = null` for this reason.)
- **No Crunchbase / LinkedIn / PitchBook / investor databases.** External enrichment is
  Wikidata-only (see below).
- Most empty cells are **legitimately N/A** (e.g. exit/acquirer/ticker for active
  companies; sector when the firm never tags it). That is not "missing data" to invent.
- **Empty ≠ absent.** Before declaring a field N/A, check whether the data is *denormalized*
  into the **name suffix** (`Foo (Acquired)`, `Bar (NYSE: TICK)`) or **description prose**
  ("Acquired by X in YYYY"). A structured field that's empty for *every* record is a cue to
  go look there, not proof the site omits it. (See PLAYBOOK §Recon "Empty ≠ absent".)

## `everywhere_tags` taxonomy (exactly these 17 — verbatim spelling)
```
BioTech
Health
Cybersecurity
Dev Tools / Cloud
Consumer
Future of Work
Transportation / Mobility
FinTech / Insurance
RegTech/Gov/Legal
Deeptech / Robotics / AR/VR
Data & Analytics
Logistics / Supply Chain
Web3 / Crypto
PropTech
Gaming / Media / Entertainment
CPG
Climate / Sustainability
```
Rules:
- **AI alone is not a category** — classify an AI company by the *market it serves*
  (AI for devs → Dev Tools / Cloud; for work → Future of Work; for health → Health; etc.).
- Include a **vertical + enabling-tech** tag only when both clearly apply (e.g. health data
  → `Health` + `Data & Analytics`); if one is much weaker, drop it.
- Don't use **Consumer** or **Future of Work** as catch-alls.
- Order most→least relevant; **cap at 4**; no duplicates; every value must be one of the 17.
- Derive from the firm's own sector tags first (when present), else keyword-classify the
  name + description. Tagging is keyword-based (no LLM) and intentionally coarse — a few
  untagged stragglers / over-tags are acceptable.
- **The tags are the product** (user, 2026-07-27): the manager's dashboard agent builds
  comps by looking up `everywhere_tags`, so an untagged dataset is dead weight to the
  system's main consumer. Shared implementation: `automation/tags.py`
  (`classify`/`fill_empty`, keyword map lifted from `lererhippeau_scraper.py`), applied
  by the pipeline to every scrape result and by the factory before persist — fills ONLY
  empty tags, so a hand-written scraper's own tagging always wins. The pipeline also
  runs `tags.carry_forward` first (fresh records with empty tags inherit the previous
  dataset's tags, matched on company_url then name), so one-off enrichment survives
  weekly refreshes instead of being silently wiped. ONE sanctioned exception to
  "no LLM": `automation/llm_tag_backfill.py` (user decision 2026-07-27) — a one-off
  Haiku pass over companies with NO text signal, unknown→[] (never guess), verbatim-tag
  validation, provenance in `data/llm_tag_report.json`, spend booked to the ledger as
  `tag-backfill`. Recurring tagging stays keyword-based.

## Enrichment (`scripts/enrich.py`)
Back-fills empty fields in `companies.json`, `menlo_companies.json`, `usv_companies.json`
from **Wikidata** (free + attributable):
- **Fills ONLY empty fields; never overwrites** a non-empty value.
- Matches a company to its Wikidata item by **verified official-website (P856) domain** —
  ambiguous name-only matches are skipped (prevents wrong data).
- Pulls founders (P112), founding year (P571), industry→sectors (P452), ticker (P414/P249).
- Records every fill + source Q-id in `data/enrichment_report.json`.
- Coverage is partial by design (well-known companies match; long-tail private startups
  won't). Re-runnable and idempotent.

## Conventions
- Politeness: custom User-Agent, timeouts, retries/back-off, small sleeps between requests.
- **DO NOT PUSH TO ANY REMOTE. This is not negotiable (user decision, 2026-08-09.)**
  `https://github.com/michaeleverywhere/vc-comp` belongs to the former manager. The
  internship is over, so nothing from this working copy belongs on that account. The
  push URL is disabled locally, but treat that as a backstop rather than the rule:
  never run `git push`, never re-enable the push URL, and never add a new remote.
- **Local commits only, and only when the user asks.** Concise message; NO co-author
  trailer (user preference, 2026-07-24). Commits use
  `git -c user.email="ruszinfilay@gmail.com" -c user.name="rus.perish"`.
- `git pull origin main --no-rebase --no-edit` is still fine if the user wants to see
  what the nightly pipeline has committed upstream. Reading is not writing.
- Use `/tmp` (or the session scratchpad) for recon HTML/temp files, not the repo.

## Quickstart: add a new VC firm
Usually nobody has to: `candidate_finder.py` tops the queue up on every discovery run
(§Automation). To force a specific firm, add `{firm_name, homepage}` to
`automation/candidates.json`, push, and let the discovery service handle it (finds the
portfolio page, scrapes, adds to Airtable, and the scraper factory attempts a bespoke
scraper). Manual path (for hard sites):
1. Recon the portfolio page (`curl` raw HTML; identify the data source). See PLAYBOOK §Recon.
2. Write `scripts/<firm>_scraper.py` following the shared template (PLAYBOOK §Template);
   output `data/<firm>_companies.json` with a site-tailored schema + `everywhere_tags`.
3. Test with `--limit`, then full run; validate (PLAYBOOK §Validation).
4. Optionally `python3 scripts/enrich.py` (add the file to its list) to Wikidata-fill gaps.

## Automation (`automation/` — Railway → Airtable, built 2026-07-23/24)
One self-contained pipeline; **no Zapier anywhere** (evaluated, then removed). Core idea:
a new firm is just a firm whose previous dataset is empty, so one loop handles both
discovery and refresh. Modes: `python3 automation/pipeline.py --mode discover|refresh|all`.

**Data flow:** candidate finder (discover mode only — tops up the queue) → roster (dedup
once, keyed on `data/<slug>_companies.json` filenames) →
per firm: scrape (bespoke script | generic extractor — **but new firms are
FACTORY-ONLY since 2026-07-28**, user decision: a dataset-less candidate is never
generic-scraped; it waits for the factory, so its first dataset is bespoke. Accepted
costs, chosen knowingly: no day-one data if the burst fails, and the
≥50%-of-baseline validation gate has no reference for new firms — the generic
extractor now only ever runs for dataset-bearing scraperless firms) → diff vs GitHub
(added/dropped/exited + health; `safe_to_commit` guard blocks empty/>20%-crater
overwrites) → commit to GitHub → **direct Airtable upsert** (`airtable_writer.py`, matched on `Data file`) with
auto-fill of every blank metadata cell (Name, Source URL, Notes, Source type, Scraper
module — via `names.py`; nothing is ever entered by hand, existing values never overwritten).

**Airtable:** base `Comps - Automations` (`appdSRg0657zG3oef`), table **`Private Comps`**
(NOT "Portfolio Companies" — that table exists but was cut from scope; `backfill_airtable.py`
is legacy). Table = 1 row per firm: registry metadata + Record/Prev/Delta count, `Scraper
health` (new/grew/same/shrank/count-drop/broke), Status (+`needs-scraper`,`broke`), Last
run/commit, and **17 per-tag Number columns**
(A firm that retires with NO dataset is DELETED from the table, not labelled — Airtable
is "firms with data", not a graveyard of attempts; user decision 2026-07-27, replacing
a needs-scraper→retired status flip that shipped and was superseded the same day.
`airtable_writer.delete_firms` removes the row on retirement night, and a
`delete_strays` reconcile pass on every discovery run backstops anything stranded —
keep-set = repo datasets + tonight's graduates + still-eligible firms, guarded by
`_MIN_KEEP` so an upstream glob bug can never empty the table. **Bespoke-or-nothing
(2026-07-28, user decision "I would like a bespoke script for every eligible firm"
+ "if the EV tags are missing, I do not need the data", superseding the previous
day's thin-is-not-failed stance):** a factory-failed firm now loses its thin
dataset too — `pipeline._retire_fully` drops the registry row, queues the Airtable
delete AND deletes the dataset from the repo (`gh.delete_json`; git history is the
undo). Lightspeed's hand-built `companies.json` can never get here — factory
`targets()` excludes it by construction. Repo-side memory files are unaffected by
all of this — they are what stops re-proposing/re-scraping.) (exact taxonomy names; note `Health` the tag
≠ `Scraper health`) holding how many portfolio companies carry each tag (double-counting
across tags is intended). `create_at_fields.py` created the schema; `fix_dupes.py`/`audit_at.py`
repaired a dup incident (trailing-whitespace Data-file keys — root cause fixed).

**Railway** (project on the MANAGER's account — corrected 2026-08-03; an earlier note here
said "user's account", which was wrong and would have sent a leaving intern hunting for a
project transfer that isn't needed. Michael owns the Railway org, the GitHub repo and the
dashboard agent; the only things that were ever intern-side are API keys minted on intern
accounts. Deploys from `michaeleverywhere/vc-comp`, root dir = repo root): service 1 "discovery" — `--mode discover`, cron daily `0 7 * * *`, LIVE and
verified (added Amplify 124 / Homebrew 106 / SignalFire 103 / Foundry Group / Wing VC;
9 JS-heavy sites correctly flagged needs-scraper: emergence, foundation, uncork, craft,
boldstart, costanoa, susa, bullpen, scaleventurepartners — as of the scrape memory they
are scraped once, fail, and are never scraped again, instead of every run forever).
Railway vars: GITHUB_TOKEN (fine-grained, **must be created on michaeleverywhere**,
Contents r/w on the one repo), GITHUB_REPO=michaeleverywhere/vc-comp, GITHUB_BRANCH,
GITHUB_DATA_DIR, AIRTABLE_PAT (data.records read+write), AIRTABLE_BASE_ID, AIRTABLE_TABLE.
Local `automation/.env` mirrors these (git-ignored; Airtable schema-write PAT stays
laptop-only). Data commits from Railway land as `Nightly: <slug> …`.

**Candidate finder** (`candidate_finder.py`, added 2026-07-27; runs FIRST in discover/all
mode, before the roster is built, so a firm found tonight is scraped — and possibly
factory-generated — the same night). Fixes the queue's dead end: `candidates.json` was a
hand-written starter list, so once the factory drained it discovery had nothing left to do.
Each run asks the Claude API for VC firms not already covered (`FIND_PROPOSALS`, default 12
per call; the exclude list is every repo firm + both candidate lists + the no-portfolio 7),
then **verifies every proposal against its live site before queueing it** — a model can
invent a firm or misattribute a domain, so a proposal is a LEAD, not data. Gates:
homepage must serve HTML; the PAGE (not the domain — that would be circular, since a
made-up firm gets a domain spun from its own name) must carry the firm name; the site must
read like a VC firm. Portfolio-URL resolution is a HARD gate (2026-07-27 — it was soft
for exactly one night, and the one firm the soft gate queued, LocalGlobe, was dead
within a single run: terminal no-url retirement means the factory can never rescue a
firm the resolver can't see, so a null-portfolio candidate is a guaranteed tombstone):
no resolvable portfolio page → rejected permanently, and the loop keeps testing that
night's remaining proposals, so a run queues a firm with a real path to a dataset or
queues nothing. Every queued entry now carries a portfolio URL. Per user
decision **`FIND_MAX_PER_RUN=1`** (one new firm per run), with `FIND_MAX_OPEN=25` pausing
the finder while the backlog is deep; `FIND_MODEL` defaults to claude-sonnet-4-5.
**That backlog count excludes FINISHED firms** (`_pending`): a factory-retired or
scrape-dead firm never gains a dataset, so counting it as "unprocessed" made the number
rise monotonically — measured, the finder shut itself off on night 16 and stayed off.
Retired ≠ pending. Regression-tested with a 30-night simulation.
State is `data/discovered_candidates.json` (a JSON list, repo-committed like
`gen_attempts.json` so ephemeral containers read it back). **Rejected entries are kept on
purpose** — they stop the model re-proposing and re-verifying the same dead domain nightly.
`roster.py` reads both candidate lists as one; `names.py` reads the queue too, so
discovered firms get real Airtable names instead of title-cased slugs. Failures are caught
and printed — the finder must never break the run. Test locally:
`python3 automation/candidate_finder.py --dry-run`.

**Spend ceiling + model escalation** (`budget.py` + `data/spend.json`, added 2026-07-27,
user requirement "under $5/month **with one firm per day**"). The only variable cost is
the Claude API — the factory is ~97% of it, the finder ~$0.006/run. `GEN_MAX_PER_RUN`
caps FIRMS per night, not dollars, so it cannot enforce a budget. Two changes:

1. **Escalation** (`scraper_gen.model_for`): a burst runs the CHEAP model
   (`GEN_MODEL_CHEAP`, default claude-haiku-4-5) for its early tries and the strong one
   (`GEN_MODEL`, default claude-sonnet-4-5) for the last `GEN_STRONG_TRIES` (**2**).
   Rationale: the guard rejects bad output rather than committing it, so a cheap first
   attempt risks nothing but a retry. A 1-try burst goes straight to strong. Set the two
   model vars equal to disable. Firm cost fell ~$0.21 → ~$0.10; **a firm a day now costs
   ~$3.09/month**. TWO strong tries, not one, and `GEN_MAX_ATTEMPTS` default went 3 → 4
   to pay for it: escalation initially left Sonnet a single shot where it previously had
   the firm's whole budget with failure feedback between tries (the loop that got
   foundrygroup through on try 2) — a quality regression smuggled in as a cost saving.
   At the observed mix the extra try is free, since most firms finish on try 1.
   Relatedly, `_feedback_block` now LABELS each attempt with the model that produced it
   and, when the code being shown came from a different model, tells the reader it was a
   smaller one and to discard rather than patch a wrong approach — unlabelled, Sonnet
   read Haiku's broken code as its own prior reasoning.
2. **Ledger**: every response's `usage` is priced (cache writes 1.25x, reads 0.1x —
   folding those into base input would overstate a burst ~3x) and accumulated per
   calendar month. `MONTHLY_BUDGET_USD` default **4.50**. NOTE the prompt cache is
   PER-MODEL, so the escalated Sonnet try pays a fresh cache write, not a 0.1x read —
   a worst-case 3-try burst is ~$0.24, not ~$0.11.

`can_generate()` requires the burst estimate PLUS a `_FINDER_RESERVE` ($0.25): without
it the factory spends to the line and the finder's remaining nights push the month over
(observed in test: $4.53 against a $4.50 budget). The finder itself stops only when the
budget is truly gone. Month rolls over on its own — a different `month` key loads a fresh
ledger, no cron. Unknown model strings are charged at the dearest known rate, so a model
swap throttles early rather than overspending silently. Ledger is repo-committed (data/,
outside Watch Paths) and saved BEFORE the factory flush, like the scrape memory, for the
same redeploy-kills-the-container reason. Measured over a simulated 30-night month:
**observed mix = 30/30 firms for $3.09; worst case (every firm fails all 3 tries) =
17/30 firms for $4.29** — throttles rather than overspends. Residual leak: a container
killed mid-burst loses ≤$0.24 unrecorded. The Anthropic Console spend limit is still the
real wall; this guard is cooperative. Tests: `automation/test_budget.py`.

**Scrape memory** (`scrape_state.py` + `data/scrape_attempts.json`, added 2026-07-27).
Closes the last memoryless stage: a candidate the generic extractor couldn't read was
re-scraped IN FULL every night forever (homepage + ~15 guessed portfolio URLs ≈ 16
fetches × 9 stuck candidates ≈ **144 requests/night** to reproduce 9 known failures,
plus 9 `needs-scraper` Airtable writes). **Per user decision: one failure and the firm is
never scraped again** (`next_attempt: null`). A widening 1→3→7→30-day retry was built
first and rejected as too fiddly to reason about — it survives as the
`SCRAPE_BACKOFF_DAYS="1,3,7,30"` env override, and deleting a firm's entry re-arms it.
Accepted cost: a site that merely happened to be down that night is written off, and the
way back is manual. A successful scrape deletes the entry. Kept SEPARATE from
`gen_attempts.json` on purpose: different question ("can the extractor read this page?"
vs "can Claude write a scraper?"), different retirement terms, and merging them would let
a factory retry silently reset the scrape memory.
**Factory retirement now also stops the scraping** — `gen_state.eligible()` is checked in
the scrape loop, which is what CLAUDE.md always claimed but the code never did
(`gen_state` was imported only inside the factory block). Measured over 30 simulated
nights: 4320 → 144 fetches, **97% fewer** — one night's worth, then zero forever.
Two implementation constraints, both load-bearing: the skip runs BEFORE any network call
and does NOT remove the firm from `firms` (the factory iterates that list, so filtering
it would delay generation); and the state is committed right after the scrape loop, not
at the end — the factory's flush pushes to `scripts/` and redeploys Railway, killing the
container before any later write lands. Bespoke firms are exempt — a broken hand-written
scraper is real breakage and must never be silenced. Tests:
`automation/test_scrape_backoff.py` (offline; includes the 30-night request-count
simulation — a backoff that looks right but still fetches nightly would pass every unit
test and fix nothing).

**Scraper factory** (`scraper_gen/guard/runner/factory.py`, wired into discover mode):
auto-generates BESPOKE rich scrapers via Claude API — user chose **no human approval**, so
three machine gates replace review: AST allowlist (no env/subprocess/eval/open/getattr…),
sandboxed run with token-scrubbed env, output validation (≥10 recs, ≥50% of generic
baseline, ≥95% names, ≥60% urls, ≥30% descriptions, no stringified lists/dicts). Pass → commit scraper (trusted
runnable footer) + rich dataset; the push triggers Railway rebuild so the firm becomes
bespoke. Generate-once per firm; `GEN_MAX_PER_RUN=3`; targets = the 6 thin datasets first
(proven scrapeable), then needs-scraper candidates. Accepted residual risk: subtly-wrong
values can pass validation (revert the firm's commit if so). **Live since 2026-07-27**,
first runs validated everything in production: amplify (114 recs, try 1), felicis (275,
try 1), foundrygroup (56, try 2 — the feedback loop's first win), homebrew (162, try 1)
graduated bespoke; signalfire retired after 3 tries all failing "description coverage
< 30%" (the legitimately-thin case working as designed). Cache write→read visible in
`[gen]` token log lines; a full 3-try burst ran ~$0.26.
**Attempt memory + burst retries (added 2026-07-26, burst 2026-07-27;**
`automation/gen_state.py`**):** per user decision, a firm gets its WHOLE generation
budget the first night it's tried: `attempt()` bursts up to `GEN_MAX_ATTEMPTS` (default 3)
tries in one run — site context fetched once, each retry prompted with the burst's
earlier failure reasons + last code so it varies its approach; the context block is
cache-marked (prompt caching: retries pay ~10% input on it, usage printed per call)
— and if none passes the
gates the firm is **retired the same night** (no rolling backlog; every targeted firm
leaves the queue as bespoke or retired). Failures are logged to `data/gen_attempts.json`
(committed via the store like a dataset, so ephemeral Railway runs read it back; local
runs use the file directly); retired/`"skip": true` firms are filtered out of `targets()`
*before* the `[:GEN_MAX_PER_RUN]` slice (`GEN_MAX_PER_RUN` still caps FIRMS per run).
`generation error:` API-transport flukes abort the burst uncounted (firm retries next
run). Manual `"skip": true` = "legitimately thin, leave it alone"; success deletes the
firm's entry; re-arm a retired firm by deleting/editing its entry.
**Terminal no-url retirement (2026-07-27, user decision "if no portfolio page resolves
on step 1 — retire the firm"):** `attempt()` marks a "no portfolio url" result
`terminal: True`, and `gen_state.record_failure(..., terminal=True)` sets
`"retired": true` on the entry — the firm is retired the SAME night, whatever its
attempt count. Rationale: the 4-try budget exists for generation failures, where
feedback between tries helps; a no-url failure shares nothing with that — there is no
page to generate against, so each "retry" was one counted no-op per night (~2 weeks of
`needs-scraper` in Airtable for the 9 JS-heavy candidates). Recorded as a FLAG, not
`attempts = max`, so the count stays honest; guarded by `counted`, so a transport fluke
can never retire a firm. The flag lives inside `eligible()`, so it propagates to all
three consumers at once: factory `targets()`, the scrape-loop skip, and the finder's
`_pending` backlog count. `"portfolio page unreachable"` (URL resolved, fetch failed)
stays NON-terminal on purpose — that shape is transient network, not structural.
Fixing this exposed a latent Airtable hole: a firm retiring on a night the
scrape memory had SKIPPED it had no registry row, so the needs-scraper→retired status
flip found nothing and the status stranded at `needs-scraper` forever. The first fix
synthesized a minimal `retired` row; hours later the user chose deletion over
labelling, so the retire branch now calls `pipeline._retire_from_airtable` instead —
in-run row dropped before the upsert, prior nights' row deleted after it (see the
Airtable section). Net effect as it actually played out: emergence/foundation/uncork
retired on the first live run ($0), and the remaining six were pre-retired by hand
the same evening rather than burning three more nights. Tests:
`automation/test_factory_retire.py` (offline; includes a 30-night simulation proving
exactly ONE attempt is ever recorded — a flag that looked right but left the firm in
`targets()` would pass every unit test and fix nothing).
**Self-redeploy guard (2026-07-27):** a mid-run GitHub push makes Railway redeploy the
service and STOP the running container (observed: felicis's success commit killed the
foundrygroup burst). Factory commits are therefore deferred to end-of-run
(`pipeline._flush_factory_commits`, after the Airtable upsert; scraper before dataset so
a kill between them self-heals). Pair with **Watch Paths** on both Railway services
(`automation/**`, `scripts/**`, `requirements.txt`, `railway*.toml`) so data-only
commits — incl. the refresh service's per-firm `Nightly:` commits — never rebuild.

**State at session end (2026-07-27):**
- DONE (2026-07-26/27 session): factory LIVE and validated end-to-end in production —
  burst retries w/ failure feedback, prompt caching, type-integrity gate, attempt
  memory + same-night retirement, deferred end-of-run commits (self-redeploy guard),
  Watch Paths set on discovery. 4 firms graduated bespoke (amplify, felicis,
  foundrygroup, homebrew — see table), signalfire retired. `ANTHROPIC_API_KEY` + spend
  limit live on the discovery service. Refresh service CREATED (named "monthly refresh"
  but cadence is WEEKLY per user decision; settings partially verified — item 1). Laptop `automation/.env` repaired (PAT was comment-mangled).
- DONE (2026-07-27, later): **candidate finder** written + wired (see above), so the
  discovery queue refills itself and `candidates.json` stops being a dead end. Gates are
  covered by `automation/test_candidate_finder.py` (offline, no key/network/cost —
  injected fetchers + stubbed proposals; run it after touching `_PROMPT`, `verify()` or
  `identity.py`). First live `--dry-run` proposed Canaan + Matrix (genuinely new, good)
  and **Lightspeed — a firm the repo already has**, which exposed a latent bug in the
  system's only dedup gate: `companies.json` predates the `<slug>_companies.json`
  convention, so `identity.slug_from_file()` returned None for it and Lightspeed had NO
  slug — invisible to `is_known()` and absent from the finder's exclude list. Any
  candidate named Lightspeed would have been scraped into a competing
  `lightspeed_companies.json`. FIXED via `identity._FILE_ALIASES` (bidirectional
  file↔slug map) + `_excludes` now asking identity what counts as a dataset instead of
  doing its own `endswith` test. Regression-tested.
- DONE (2026-07-28): **first live run, and it worked** — `spend.json` created
  ($0.3907 / 6 calls), 10 firms written to the scrape memory, 3 retirements logged,
  10 Airtable rows upserted, run cost $0.39. Escalation confirmed in the logs
  (wingvc: Haiku $0.0347 → Haiku $0.0113 → **Sonnet** $0.1154 → **Sonnet** $0.0476)
  and with it the PER-MODEL cache: `cache_write` on try 1, `cache_read` on try 2,
  then `cache_write` AGAIN on the switch to Sonnet — the cost model was right.
  Two defects found and fixed:
  (a) **the finder queued "Andreessen Horowitz"** though `a16z_companies.json` holds
  852 of its companies. Lightspeed's bug in a new guise — the slug is a NICKNAME, so
  `is_known()` slugified to "andreessenhorowitz" and never reached "a16z", AND
  `firm_names.json`'s single display name is "a16z", so the model was never told the
  full name. Fixed with `identity._ALT_NAMES` (a16z, usv, nea, crv, ivp, tcv, svangel,
  8vc, lightspeed), which feeds BOTH the dedup gate and the exclude list. Self-heals:
  the stale queue entry is now filtered out by the roster. **Add to `_ALT_NAMES`
  whenever a dataset slug cannot be spelled out from the firm's name.**
  (b) signalfire's generated code had a duplicate kwarg — `ast.parse` ACCEPTS that
  (it's a bytecode-compile error), so it passed `static_check` and died in the
  sandbox instead. Nothing shipped; defence in depth worked. `static_check` now also
  runs `compile()`, so it fails in milliseconds with a clear reason.
- DONE (2026-07-27, later session): **terminal no-url retirement** (see the factory
  section above): "no portfolio url" now retires a firm the same night via a
  `"retired": true` flag in `gen_attempts.json`, instead of burning 4 counted no-ops
  across ~2 weeks; the retire branch synthesizes a minimal Airtable row when the
  scrape-skip meant none existed (latent stranded-at-needs-scraper bug). The 9 stuck
  JS-heavy candidates should flip to `retired` in Airtable over the next ~3 nights
  (≤3/night). Takes effect on Railway once pushed (the push rebuilds discovery via
  Watch Paths on `automation/**`); sandbox commits work but the push runs from the
  Mac terminal.
  Wingvc + signalfire had already exhausted the 4-attempt path the old way; with both
  retired, the factory queue is empty except emergencecapital (now retires on its next
  pass) and whatever the finder adds. Tests: `automation/test_factory_retire.py`.
- DONE (2026-07-27, same evening — first live run of the terminal path, plus user
  feedback "no failed firms piling up in Airtable/GitHub; a day should add a REAL
  firm"): the deploy-run retired emergence/foundation/uncork same-night at $0
  (terminal path verified live) but also queued LocalGlobe — verified real, no
  portfolio page — which was dead within the run: the soft finder gate was
  manufacturing tombstones. Three changes, all offline-tested: (1) the finder's
  portfolio gate is now HARD (see finder section; regression test
  `test_no_portfolio_is_hard_gate`); (2) Airtable tombstones are deleted, not
  labelled — delete-on-retire + per-run `delete_strays` reconcile (see Airtable
  section; supersedes both the `retired` status flip and the synthesized-row fix
  from earlier the same day); (3) one-time cleanup: the six never-factory-tried
  JS-heavy firms pre-retired via `"retired": true` with `attempts: 0` (honest — no
  try ran), and the LocalGlobe + stale a16z queue entries flipped to `rejected`.
  The ~11 tombstone rows vanish on the next discovery run via the reconcile (the
  Cowork sandbox cannot reach api.airtable.com; Railway does it). Factory queue is
  now EMPTY; the finder starts clean, and every firm it queues carries a portfolio
  URL. Watch the first hard-gate `[find]` lines for over-rejection: if too many
  real firms bounce on "no portfolio page resolved", the resolver (not the gate)
  is the thing to improve.
- DONE (2026-07-28): **Notes column now carries the RAW dataset URL.**
  `names.notes_url` returned the github.com *blob* form from when a private repo
  was assumed; blob serves HTML, so the manager's dashboard couldn't read it —
  first seen on matrixpartners, the finder's first hard-gate graduate (queued →
  generic 110 recs → bespoke 45 the same night; the 110 included footer junk like
  "Matrix Press Kit", so the drop is a cleanup, not a crater). Rows seeded by
  `populate_registry` already had raw links; only auto-filled rows were affected.
  Fixed + `_fill_blank_meta` upgrades stale blob Notes in place (exact blob-prefix
  match only — hand-written notes untouched). Data quirks in
  `matrixpartners_companies.json` to revisit: the generated scraper pads
  descriptions/founders from each company's OWN homepage (gate-gaming the ≥30%
  description rule — founders caught junk like "Join Our" / "Claude Code");
  sectors/stage/status/profile_url are all-null stubs; two names have trailing
  commas. Consider a factory-prompt rule (firm's own pages only) + a founders
  plausibility gate. Also note the ≥50%-of-baseline gate is vacuous for same-night
  graduates (generic result isn't written locally, so baseline reads 0).
- DONE (2026-07-28, "I would like a bespoke script for every eligible firm"): three
  user decisions, offline-tested: (1) **new firms are FACTORY-ONLY** — the scrape
  loop skips dataset-less generic candidates entirely (no fetches, no needs-scraper
  row, no scrape-memory entry); their first dataset is the bespoke one. The generic
  extractor's remaining role is refreshing dataset-bearing scraperless firms, a
  class that is now only wingvc/signalfire (transiently) + protected Lightspeed.
  (2) **bespoke-or-nothing retirement** — `_retire_fully` (renamed from
  `_retire_from_airtable`) also deletes a retiree's thin dataset from the repo via
  new `gh.delete_json` (git-recoverable; data/ is outside Watch Paths so the
  deletion never redeploys). (3) **wingvc + signalfire re-armed** with `attempts: 2`
  in gen_attempts.json = a burst of exactly 2 strong-model (Sonnet) tries each on
  the next discovery run, ~$0.50 worst case; if either fails both, it vanishes
  fully per (2). Watch that run's `[factory]` lines. NOTE the ≥50%-of-baseline
  validation gate is now permanently vacuous for new firms (no generic result to
  compare against) — absolute thresholds (≥10 recs, ≥95% names, ≥60% urls,
  ≥30% descriptions) are the whole gate; if a generated scraper ever ships a
  suspiciously small portfolio, that's the hole it walked through.
- PENDING (next session picks up here):
  0. Supervise the finder's first LIVE discovery run: check the `[find]` lines, confirm
     the queued firm is real and its site genuinely publishes a portfolio, and confirm
     `data/discovered_candidates.json` was committed. If the model's suggestions skew
     junky, tighten `_PROMPT` or raise the bar in `verify()`. No new Railway var is
     required (`ANTHROPIC_API_KEY` is already on discovery).
  1. ~~Finish refresh service settings.~~ **RESOLVED 2026-07-27 (evidence, not a
     dashboard check):** the repo received `Nightly:` commits for **32 firms that have
     a bespoke scraper** — a16z, accel, bessemer, insight, nea, sequoia, norwest and the
     rest. Discovery mode filters bespoke firms out before it starts, so only
     `--mode refresh` can produce those; the service therefore ran a full pass and it
     worked (small sensible deltas, no `held back` lines, no breakage). Config-as-code
     path = `railway.refresh.toml` is LOAD-BEARING (file config overrides dashboard);
     cron `0 8 * * 1` (WEEKLY, Mondays 08:00 UTC). Remaining caveat, unchanged: without
     a cron schedule set Railway runs the start command on EVERY deploy, so any push
     touching `automation/**` can launch an unscheduled ~30-60 min refresh. Harmless,
     but that is why an unexpected run appears after a deploy.
  2. ~~Factory queue drains at `GEN_MAX_PER_RUN=3`/night.~~ **RESOLVED 2026-07-27:**
     wingvc + signalfire exhausted their bursts; the 9 JS-heavy candidates and
     LocalGlobe are retired (terminal/manual). Queue is empty — from here it holds
     only what the finder adds (1/night, portfolio URL guaranteed).
  3. ~~`everywhere_tags` gap: auto-generated datasets ship `[]`.~~ **RESOLVED
     2026-07-27 (user: "the tags are the main value — the dashboard agent builds
     comps from them"):** shared keyword tagger in `automation/tags.py`, applied in
     the pipeline scrape loop (covers weekly refreshes of factory scrapers) and in
     the factory before persist; 7 untagged datasets backfilled in place
     (415/836 companies tagged — matrix 68%, amplify 84%, felicis 65%, foundry 60%,
     homebrew 40%; wingvc 0% and signalfire 8% are STRUCTURAL, no descriptions to
     classify, which is why the factory retired them). Airtable tag counts refresh
     per firm on its next scrape night (Monday refresh for bespoke firms); wingvc +
     signalfire never scrape again, so their Airtable columns stay 0 — but the
     dashboard reads the raw JSON, which now carries every tag that exists. If
     their coverage matters, the options are a Wikidata sector fill (`enrich.py`)
     or a one-off LLM pass (breaks the no-LLM convention; would stick, since both
     datasets are frozen). Tests: `automation/test_tags.py`.
     **Follow-up, same day:** keyword backfill extended to ALL datasets (+276 more
     companies; hand-written scrapers' stragglers). The ~1,900 companies with no
     text signal at all (coatue 322, generalcatalyst 187, ribbit 130…) → user
     chose the one-off LLM pass: `automation/llm_tag_backfill.py` (see the
     taxonomy section for its honesty rules) + `tags.carry_forward` in the
     pipeline so the enrichment survives refreshes. The script must run on the
     Mac (Cowork sandbox cannot reach api.anthropic.com); est. ~$0.2 of the
     $3.94 July headroom. Re-runnable: touches only still-empty tags.
     **Ran 2026-07-28 (name-only): 1,314 of 1,902 tagged, $0.14.** The 568 the
     model didn't recognize → script upgraded to FETCH-FIRST (user decision
     "if the EV tags are missing, I do not need the data"): each remaining
     company's own homepage is fetched ($0, we already hold company_url),
     parked-domain/JS-shell guards, keyword pass on the fetched text free,
     and only the still-unresolved go to Haiku WITH the homepage snippet as
     grounding. Fetched text is classification input + report provenance
     ONLY — never written into `description` (the line matrixpartners
     blurred). Keyword fills are banked to disk before any LLM spend.
     Re-run the same command to finish the job.
  4. ~~Eyeball signalfire.com.~~ **RESOLVED 2026-07-30:** it failed all 4 attempts on
     description coverage and `_retire_fully` deleted the dataset. Permanent unless
     someone hand-writes a scraper.
  5. Laptop `.env` `GITHUB_TOKEN` is ruszinn-minted → cannot write to the
     michaeleverywhere repo (fine-grained PATs are resource-owner-bound; push through
     it fails "denied to ruszinn"). Replace with a michaeleverywhere-created PAT if
     commit-capable local runs are wanted; Railway has the correct token.
  6. Optional cleanup (carried over): retire `backfill_airtable.py` + "Portfolio
     Companies" table; prune stale candidates from `candidates.json`.
  7. Finish the tag backfill: 287 companies still carry `[]` after the keyword pass
     (gradientventures 37, mosaicventures 26, coatue 22, homebrew 22, ribbit 22,
     stormventures 21, nea 17…). 134 of them have no `company_url`, so the
     fetch-first path can't ground them and they go to the model on name alone.
     Run `python3 automation/llm_tag_backfill.py` FROM THE MAC (the Cowork sandbox
     cannot reach api.anthropic.com). Re-runnable; touches only still-empty tags.
- DONE (2026-08-03): **the working copy was 41 commits behind and 44 files dirty** —
  the fetch-first tag backfill had been run locally and never committed, while Railway
  kept committing nightly. Merged with `sync_and_push.sh` (deleted after use): remote
  datasets won every collision, local tags were re-applied on top with
  `tags.carry_forward`, which is precisely what that function exists for. Pushed as
  `a3d2650`. **Watch for this pattern** — carry_forward can only inherit tags it can
  SEE, and uncommitted enrichment is invisible to Railway, so a weekly refresh will
  silently rebuild those records without them. Commit enrichment the day it is made.
  The merge also surfaced a `modify/delete` on signalfire: `git checkout --theirs`
  cannot take a version that no longer exists, so the file survived into the merge
  commit and would have resurrected a deliberately-deleted dataset. Caught before the
  push and removed in `593f0ff`. Any future merge-after-retirement has this same trap.
- DONE (2026-08-03): **pointninecapital rewritten by hand, 25 junk records → 185 clean.**
  The factory scraper called `get_text()` on a row whose name/year/flag/city/country/
  status are SEPARATE elements, gluing them into `15Five2013🇺🇸San FranciscoUSAActive`;
  when the listing gave it nothing it fell back to fetching each COMPANY'S OWN homepage
  for description and sectors, which is where "Download logo pack" came from. It also
  read 1 of 8 pages. The replacement reads `stripped_strings`, identifies fields by what
  they ARE (year pattern, membership in Point Nine's own country/sector/stage filter
  vocabularies) rather than by position, walks `?<hash>_page=N` by picking the next page
  NUMBER (taking the first matching link grabbed the *previous*-page link and stopped the
  walk at 49 records), and never leaves pointnine.com. 100% coverage on every field.
  NOTE the stage label lives in the row, NOT the anchor — worth checking on other
  Finsweet/Webflow sites.
- DONE (2026-08-03): **four new factory gates** in `scraper_guard.validate_output`,
  because every existing gate measured COVERAGE and the pointnine junk was 100% covered:
  (a) name plausibility — flag emoji, a status word welded to the previous field
  (`...USAActive`), or a year with letters both sides; verified not to fire on
  ActiveCampaign / 15Five / 2048 Ventures / Studio 2049;
  (b) self-sourcing — boilerplate like "request a demo" / "accept all cookies" in
  descriptions, the signature of a scraper that wandered onto portfolio companies'
  own sites to clear the ≥30% description bar;
  (c) stub fields — **3 or more** fields empty on every record. The threshold is
  load-bearing: ONE all-empty field is the legitimate N/A case this file already
  describes (sequoia `ticker_symbol`, menlo `sectors`, arch `acquirer`), and failing
  it would retire a firm over the site's editorial choices — which under
  bespoke-or-nothing DELETES the dataset. Measured: every dataset at 3+ is
  factory-generated, no hand-written one reaches it;
  (d) founders plausibility — a vocabulary test, not a structural one, since nothing
  structural separates "Bea Patricia" from "Park Ave".
  Six datasets were cleaned to match (matrixpartners, gradientventures, mosaicventures,
  trueventures, notationcapital, foundrygroup): stub fields dropped, 11 junk founder
  entries removed, 2 trailing-comma names fixed. All now pass.
- DONE (2026-08-03): `test_candidate_finder.py` "seed list still intact" fixed. Its
  `before` set was built from the REAL discovered queue, so it mixed seed-list firms
  with discovered ones (bcapitalgroup); the test then swaps that queue for a fake and
  the discovered firms legitimately vanish. The assertion is about `candidates.json`
  surviving the merge, so the discovered side is now stubbed empty while measuring it.
  A test bug that passed only while the finder had queued nothing — it broke the moment
  the finder started working.
- Docs debt: `automation/PIPELINE.md`/`README.md` still describe the retired Zapier flow
  in places; this section is authoritative where they conflict.
- Sandbox note (Cowork sessions): the sandbox mount can CREATE but not UNLINK files in
  `.git`, so merges strand `*.lock` files (sweep them into `_git_lock_trash/`).
  Commits from the sandbox work; `git pull` merges and pushes run on the Mac terminal
  (`rm -f` the stranded locks first).
- Context: repo stays **public** (dashboard agent reads raw.githubusercontent.com links
  tokenlessly; raw links + private repo are incompatible). The user's manager (Michael)
  owns the repo + a dashboard agent that consumes the raw JSON links.
