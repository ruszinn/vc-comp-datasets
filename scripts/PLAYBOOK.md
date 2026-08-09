# PLAYBOOK.md — building & maintaining VC portfolio scrapers

Companion to the root `CLAUDE.md`. This is the hands-on methodology so a fresh session can
add a new firm or re-derive an existing one without rediscovery.

---

## §Recon — figure out where the data lives
Always start read-only. Save HTML to `/tmp`, never the repo.

```bash
curl -s -L -A "Mozilla/5.0 ... Chrome/124 Safari/537.36" "<portfolio-url>" -o /tmp/x.html -w "%{http_code} %{size_download}\n"
grep -o -E "__NEXT_DATA__|_next/|wp-content|wp-json|application/json|data-component|gatsby|webflow|algolia" /tmp/x.html | sort | uniq -c | sort -rn
```
Decide the data source (in priority order):
1. **Baked into static HTML** (big page, company nodes present) → parse with BeautifulSoup.
   Look for repeating row/card classes; find the per-company container.
2. **Embedded JSON blob** (`__NEXT_DATA__`, `application/json` script) → extract & `json.loads`.
3. **WordPress REST** → `curl .../wp-json/wp/v2/types` to find the custom post type's
   `rest_base`; check if `acf`/`meta` carry the fields (often empty → need a custom route).
4. **Custom / Vue / JS API** (empty container + `data-component`, small HTML) → grep the
   theme JS bundle for endpoints:
   `curl <site>/wp-content/themes/<t>/dist/js/app.min.js | grep -oE '/wp-json/[a-z0-9/_v-]+'`
   Then call those endpoints directly.

Also locate: **filters** (often `?param=value` query strings doing server-side filtering →
fetch each to get per-company sector/status) and a **per-company detail** endpoint/page
(for description, website, founders, etc.). Note pagination (`X-WP-Total` header, `?page=N`,
or a `max`/`rows` payload).

Caveats seen: responses can be **double-JSON-encoded** (a JSON string containing JSON);
sites may **intermittently bot-block** (retry / vary timing); some firms **redirect**
sub-portfolios to another host (Lightspeed → lsip.com).

**Empty ≠ absent — mine names + prose before declaring a field N/A.** A structured field
that is empty for *every* record is a red flag that the data is **denormalized elsewhere**,
not proof the firm doesn't publish it. Two places to always check before finalizing the
schema:
- **The display name.** Firms often encode exit state in the name suffix:
  `Foo (Acquired)`, `Bar (NYSE: TICK)`, `Baz (IPO: TICK)` (RRE does exactly this — its
  structured `status` label is blank for all 250, yet 76 names carry the answer). Parse the
  suffix into `status`/`ticker_symbol`.
- **The free-text description.** The last sentence frequently states facts no structured
  field holds: "**Acquired by** Good Technology **in 2012**", "**went public** on the NYSE in
  2020", "(**formerly** Clubhouse)". Regex these out into `acquirer`/`exit_year`/etc.
  (RRE: acquirer+year live only in the description for 60/63 acquired companies.)
So: when a column comes back 100% empty, grep the names and descriptions for the same
concept before concluding the site simply doesn't expose it.

---

## §Per-source cheat-sheet (verified working)

### USV — `usv_scraper.py`
- WordPress, **fully server-rendered** `https://www.usv.com/companies/` (all 214 in one page).
- Row = `.m__list-row` (skip `.m__list-row--mobile`): name link, external website, logo,
  `"Stage, Year"`, `.m__list-row__excerpt` (description), `.m__list-row__link` (USV post),
  `span.exit-detail` (e.g. `Acquired by Google`, `NASDAQ: ETSY`).
- **Sector** & **status** via server-side filters: `?industry-cat=<slug>` (31 sectors) and
  `?status-cat=current|acquired|public|inactive`. Fetch each, union membership by name.
- Companies with no live site render the name as plain text (no `<a>`), so don't require a link.

### Menlo — `menlo_scraper.py`
- One large static page `https://menlovc.com/portfolio/` (~239 `.js-company-block`,
  `data-title` = name). ~211 have a `.detail-portfolio-card`; 28 legacy ones don't.
- Detail card: `.partnership`-style sections → website (`.portfolio-details-link`), socials,
  **Milestones** timeline (`Founded` year, `Partnered, <stage>` year, exit), **Leadership**
  (founders = names whose title contains "Founder"), **Partners** (links to `/team/`),
  "View more" → `menlovc.com/portfolio/<slug>/`. Logos hide in `data-srcset`.
- **No per-company sector** on the page (only a 3-way AJAX "Focus" filter) → `sectors=[]`,
  rely on `everywhere_tags`. Page intermittently returns ~4KB (bot-block) — just retry.

### Insight — `insight_scraper.py`
- Vue app over WordPress. Grid: `GET /wp-json/insight/v1/get-companies?page=N`
  (**12/page**, returns a **JSON string** → `json.loads` twice; `{max, rows}`). Rows give
  id, slug, name, location, logo (verticals/stage come back empty here — ignore).
- Detail: `GET /wp-json/insight/v1/get-company-content?id=<ID>&detail=true` → `{content: HTML}`.
  Parse the fragment:
  - `.partnership-content__body` → description
  - `.partnership-content__header` external links → website (the non-social link) + socials
  - `.partnership-content__roles` → label/value divs (`span.font-semibold` = label):
    **Founder**, **CEO**, **Investment Team** (=Insight partners), **Sectors**
    (`a[href*="vertical="]`), **Initial Investment** (date), **Status** (Current/Prior Investment)
  - `.partnership-content__milestones` → funding timeline ("YYYY Insight led $X Series Y").
- Insight only says Current/Prior Investment — **no acquirer/ticker/founded-year** structured.
- `get-company-id-by-slug?slug=<slug>` maps slug→id. Full run ≈ 71 + 847 requests (~8 min).

### RRE Ventures — `rre_scraper.py`
- **Webflow** site `https://rre.com/portfolio` with two parallel **Finsweet CMS** lists:
  a compact "card" grid and a richer "modal" list. Scrape **only the modal list** — it
  carries everything the card has plus website, description, founded/invested years, HQ.
- Modal list is paginated server-side at **20/page** via `?79e6a7d8_page=N` (the card grid
  uses a *different* key `155d7971_page=N`, 50/page — ignore it). 13 pages = **250 companies**;
  stop when a page has no `.portfolio_modal-slider-item`.
- Per `.portfolio_modal-slider-item`: `slider-name` attr / `h2[fs-list-field="name"]` = name;
  `a[company-link]` = website (always present); `img.portfolio_modal-image` = logo; the single
  `<h3>` in a `.portfolio-modal_grid` = description; `[fs-list-field="category"]` (hidden list
  at top) = RRE's own categories. Detail rows are `.portfolio_modal-details` = `.text-size-eyebrow`
  (label) + `.text-size-large` (value, empty when `w-dyn-bind-empty`).
- Detail labels seen: **Founded** (~236), **RRE Invested** (~248), **Headquarters** (only 3),
  plus `status`/`RRE participation` labels that are **always empty** → omitted from the schema.
- **No investment-stage field anywhere.** Exit state is encoded only in the company NAME
  suffix, so `derive_status()` parses it: `(NYSE: PLTR)`/`(IPO: DOOR)` → `status="Public"` +
  `ticker_symbol`; `(Acquired)` → `status="Acquired"` + `acquirer`/`exit_year` pulled from the
  description's trailing "Acquired by <X> in <YYYY>." (60/63 name an acquirer); else `"Active"`.
  Result: 174 Active / 63 Acquired / 13 Public. `company_name` keeps the suffix verbatim.
- RRE categories (11): Enterprise/Saas, CONSUMER, Fintech, AI, Media, Hardware, Crypto,
  Healthcare, Featured, Robotics, Space (casing is inconsistent → match case-insensitively).
  `SECTOR_TAG_MAP` maps the clean verticals; **AI** and **Enterprise/Saas** are left to the
  keyword classifier (AI alone isn't a category; Enterprise/Saas has no single tag). `Featured`
  is a curation flag, not a sector — ignored. ~6 vague AI/SaaS stragglers stay untagged.

### Founders Fund — `foundersfund_scraper.py`
- WordPress; the portfolio page (`foundersfund.com/portfolio/`) is JS-rendered, but the
  `company` custom post type is exposed via the **standard WP REST API** (no custom route):
  `GET /wp-json/wp/v2/company?per_page=100&page=N`. **62 companies**, fits in ~1 page;
  paginate via `X-WP-TotalPages`.
- Each record is self-contained — no per-company crawl needed: `title.rendered` = name,
  `content.rendered` = description (strip HTML), `link` = FF profile URL,
  `featured_image_thumbnail_url` = logo, `founders` = list of `{founder_name, ...}` objects,
  `industry` = sector display name, and `class_list` holds `company_industry-<slug>` entries
  (the **source of truth for sectors** — read these, not `industry`, since one company,
  Cedar, carries two). The external **website** lives in a small `profiles` HTML blob
  (`<a href=...>Website</a>`; first link) — `acf`/`meta` are empty. Some hrefs are malformed
  (`http:///www...` → collapse the triple slash).
- **6 FF sectors** → `SECTOR_SLUG_DISPLAY`: Advanced Machines & Intelligence, Aerospace &
  Transportation, Analytics & Software, Biotechnology & Health, Consumer Internet & Media,
  Real Estate & Technology. `SECTOR_TAG_MAP` maps all but **"Analytics & Software"** (≈ just
  "software" — left to the keyword classifier to tag by market served).
- **Founders kept as `{name, url}` objects** (unlike usv/menlo/insight, which store names
  only). The API's misnamed `founder_crunchbase_slug` field actually holds the founder's
  Twitter / LinkedIn / Wikipedia / personal site (137 entries, 132 with a URL). These are
  links FF itself publishes — not external enrichment we fetched from a banned DB — so per an
  explicit user decision they're retained. `Empty ≠ absent` checked: no status/exit/acquirer/ticker is
  encoded in names or descriptions (the regex hits were false positives like "customer
  acquisition", "public safety"); FF also publishes no stage / founded-year / location, so
  those fields are intentionally omitted, not invented.
- Legit empties at source: 2 with no website (Asana, Nanotronics — blank `profiles`), 2 with
  no sector (Impulse, Postmates), 1 with no founders (OpenAI — source founder is literally
  blank). ~4 vague AI/SaaS stragglers stay untagged.

### ICONIQ Growth — `iconiq_scraper.py`
- **Webflow + Finsweet CMS**, but unlike RRE the data is **fully server-rendered in the one
  static page** `iconiq.com/growth/companies` — no API, no pagination. Just `GET` it and parse.
- The list is **rendered twice** (a reveal/animation duplicate: 200 `.companies-list_grid-item`
  for 100 companies) → **dedupe by name**. Per item: `h2.heading-style-h3` = name,
  `.text-size-medium.is-companies` = description (2 empty), `a.companies-list_grid-item-reveal-wrap`
  = external website (all 100; no ICONIQ detail page), `img.companies-list_logo` = logo.
- **Sectors are an "Empty ≠ absent" trap.** The same 5 sectors are encoded in TWO places that
  disagree per record: the `data-groups` attr (lowercase slugs `["all","ai",...]`) and the
  `.hidden-params p[fs-cmsfilter-field="category"]` display names. **Neither is a superset** —
  6 companies differ (e.g. Anduril's only sector lives in `data-groups`; Calendly's only in the
  category text), so **UNION both** (map slugs via `SLUG_DISPLAY`, skip `all`/blanks). Reading
  only the category `<p>` text leaves Anduril sector-less. 5 sectors: AI, Consumer Internet,
  Enterprise SaaS, Fintech, Healthcare.
- `SECTOR_TAG_MAP` maps Healthcare/Fintech/Consumer Internet; **AI** and **Enterprise SaaS**
  are left to the keyword classifier (AI alone isn't a category; Enterprise SaaS spans
  dev-tools/work/data/security). ICONIQ exposes no founders/stage/status/exit/founded/location
  (names + descriptions checked — the lone "exit" hit, Adyen, is a false positive). ~6 terse
  AI/SaaS stragglers stay untagged.

### Lerer Hippeau — `lererhippeau_scraper.py`
- **Webflow + Finsweet**, `lererhippeau.com/portfolio`. The page holds **three** collection
  lists: a 24-card "featured" grid (strict subset, and its cards carry **no sector sub-list**),
  a 1-item decorative heading list, and the full alphabetical portfolio. Pick the full one by
  **"is the wrapper that contains a `.w-pagination-wrapper`"** — positional indexing breaks if
  Lerer re-orders the sections.
- Server-side pagination `?c33e6893_page=N` at **64/page** → 5 pages = **305 companies**;
  stop when there's no `a.w-pagination-next`.
- Per `.portfolio-collection-item`: **there is no name node** — the card renders
  "Visit / `<Name>` / →" as three `.website-url` divs inside `a.link-holder`, so the name is
  the middle one (logo `alt` attrs are empty). Also: `p.portfolio-details` (description, 305/305),
  `a.link-holder[href]` (external site, 305/305), `img.potfolio-logo` (**sic** — Lerer's own
  class typo), `.year-holder` → "SINCE `<year>`" (first investment year, 305/305).
- **Status**: a `.exit-tag` chip ("Exited") — verified it is *never* rendered hidden
  (174 occurrences, all plain-class), so presence/absence is a trustworthy signal →
  174 Exited / 131 Active. Spot-checked: Warby Parker/Allbirds/Casper/Axios/Giphy = Exited,
  Glossier/Hungryroot/Zipline = Active.
- **Sectors** = a nested `.sector-collection-wrapper` list per card
  (`[fs-cmsfilter-field="sector"]`), 0–3 each, 15 values. Note **NBSP** inside
  "Energy Transition &`\xa0`Climate" → normalize whitespace before mapping.
  `SECTOR_TAG_MAP` leaves **AI/ML** to the keyword classifier (AI alone isn't a category);
  **Marketing Services** → `Gaming / Media / Entertainment` (adtech/martech sits in media —
  the same call `hustlefund_scraper.py` makes for "Advertising / Marketing" and `enrich.py`
  makes for "advertis"/"marketing"; it was the single biggest source of untagged records here,
  8 of the original 12). Two Lerer sectors conflate two of our tags ("FinTech, DeFi, &
  Blockchain", "Data & Security") — each maps to the dominant tag only and keywords add the
  second when the blurb warrants. Plus a small `KEYWORD_TAGS_EXTRA` for AI/ML-only stragglers
  ("frontline", "pre-trained transformer", "engineering agi"). **1 untagged.**
- "Empty ≠ absent" checked: no acquirer/ticker/exit-year in names or descriptions (exit state
  lives only in the chip); no founders/HQ/stage anywhere, and there are **no detail pages**.

### 2048 Ventures — `2048_scraper.py`
- `2048.vc/**companies**` (`/portfolio` **404s**). Webflow; whole portfolio server-rendered in
  one page — **75** `.card-investment` items, no pagination, no API (the Jetboost dropdowns
  filter client-side over the rendered DOM).
- Per card: `a.heading-h3.link-companies` = name + external site (no 2048 detail page);
  `.companies-description p` = description; `.partner-content` blocks = the **company's**
  founders/execs (`.partner-name`/`.partner-title`/`.partner-avatar`) — Webflow pads every card
  to 3 slots, so skip `w-condition-invisible` / `w-dyn-bind-empty` (75/75 have ≥1, 158 people);
  `.companies-body` richtext = latest-news headline + press link (24/75);
  `a.button-invested` = the "Why we invested" 2048 blog post (41/75, `href="#"` + invisible when absent).
- **Stage and location chips share the class `.button-outline-invested`** — the only way to tell
  them apart is the icon filename (`...filter-fund.webp` vs `...filter-location.webp`).
- Stage vocabulary is Pre-Seed/Seed/Series A/Series B/Growth/**Exited** — "Exited" is a *value of
  the stage field*, not a separate column, so it's kept verbatim in `stage` rather than split into
  a fabricated `status` for the other 74. Source casing is inconsistent ("Pre-seed", "Seed+",
  "Seed +") and is preserved.
- **No per-company sector or investment year in the markup**: the Jetboost sector
  (Vertical AI / Deep Tech / Health / Bio / Other) and year (2019–2026) dropdowns resolve through
  Jetboost's own service via `.jetboost-list-item` inputs that carry only the company slug.
  Those columns are omitted; tagging is name+description only. Because the blurbs are one terse
  line each, the shared `KEYWORD_TAGS` left 23/75 untagged → the scraper adds a documented
  `KEYWORD_TAGS_EXTRA` supplement (every term appears verbatim in a 2048 blurb; same precedent as
  `orbimed_scraper.py`/`coatue_scraper.py`), bringing it to **3 untagged**.

### Hustle Fund — `hustlefund_scraper.py`
- **No `/portfolio` path** (404) and **the portfolio page is absent from `sitemap.xml`** — the
  company list lives on `hustlefund.vc/**founders**`, in a `.founder-list-wrapper` Finsweet list.
  The big logo collages higher up that page (`.portfolio-image*`) are decorative, not the data.
- Server-side pagination `?5d649f4d_page=N` at **9/page** → 38 pages = **335 companies**
  (stop on the first page with no `.company-info`).
- **Every card is rendered twice** — a desktop layout plus a `.company-info-mobile` duplicate.
  `.decompose()` the mobile block first, otherwise every value double-counts.
- The two location values are **cross-labelled** between the layouts: the desktop
  `[fs-cmsfilter-field="location"]` holds the COARSE bucket (US/Asia/Africa/…) while the
  *unattributed* `.filter-label` beside it holds the FINER value ("US - Bay Area",
  "Asia - Philippines") — and the mobile block tags the finer one as `location`. Captured as
  `region` (coarse) + `location` (fine).
- Also per card: `.company-title` = name; `a.company-link-with-arrow[href]` = site (**75/335 are
  `href="#"` at source** → null, verified genuine, not a selector miss);
  `[fs-cmsfilter-field="sectors"]`; `[fs-cmsfilter-field="funds"]` (fund I 90 / fund II 245).
- **31 sectors**, and a few cards carry **two comma-joined in the one cell**
  ("Finance - Other,Blockchain / Crypto / NFT / Web 3.0") → split on the comma (no individual
  label contains one). The filter dropdown only shows the first 12 — harvest the vocabulary from
  a full run, not the dropdown. `SECTOR_TAG_MAP` leaves Manufacturing / General - Industry
  Agnostic / Personal & Professional Services unmapped. **7 untagged, and they stay that way:**
  Hustle Fund publishes no description, so the only signal for those records is the company
  name plus a sector that is deliberately unmapped (or absent) — tagging them would mean
  inventing a market from a name alone. This is the one dataset where the straggler count
  can't be driven down without fabricating.
- **No description, founders, founded year, stage or status anywhere**, and no detail page →
  those columns are omitted and tagging runs on name + sector label only (coarser than usual).

### Lightspeed — `companies.json` (no script)
- Built from `https://lsvp.com/company-sitemap.xml` (all `/company/<slug>/` URLs), fetching
  each detail page (Status, Founded, Stage Invested, LSVP Investment yr, Lightspeed Team =
  partners, Leadership = founders, sectors [often "NOT SHOWN"], website, logo, image).
- India/scale-up companies **302-redirect** `lsvp.com/company/<slug>/` → `lsip.com/...`.
- This one was assembled via many one-off fetches, not a reusable script. To refresh it
  you'd re-fetch ~425 detail pages (heavy) or rely on `enrich.py` for gaps.

---

## §Template — shape of a `<firm>_scraper.py`
```python
import json, os, re, sys, time
from datetime import datetime, timezone
import requests
from bs4 import BeautifulSoup            # if HTML parsing needed

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "data", "<firm>_companies.json")
HEADERS = {"User-Agent": "Mozilla/5.0 ... Chrome/124 Safari/537.36"}

def fetch(url, params=None):             # GET + raise_for_status + retries/backoff + sleep
    ...

KEYWORD_TAGS = [ ... ]                   # copy from menlo_scraper.py / insight_scraper.py
def everywhere_tags(name, description, sectors): ...   # union sector-map then keyword fallback, cap 4

def main():
    limit = int(sys.argv[sys.argv.index("--limit")+1]) if "--limit" in sys.argv else None
    rows = ...                           # grid / page parse
    out = []
    scraped_at = datetime.now(timezone.utc).isoformat()
    for r in (rows[:limit] if limit else rows):
        out.append({ ...tailored fields..., "everywhere_tags": ..., "source_url": ..., "scraped_at": scraped_at })
    json.dump(out, open(OUT, "w"), ensure_ascii=False, indent=2)
    # print summary: count, coverage, by-tag, untagged
```
Tailored fields differ per firm — include only what the site exposes. Common ones:
`company_name, description, company_url, company_profile_url, logo_url, location,
founders, partners, sectors, first_investment_*/status/exit_*, social_urls,
everywhere_tags, source_url, scraped_at`.

### everywhere_tags classifier notes
- Reuse the `KEYWORD_TAGS` list in `menlo_scraper.py` / `insight_scraper.py` (and the
  `SECTOR_TAG_MAP` idea in `usv_scraper.py` to map a firm's own sectors → the 17 tags).
- **Substring-match traps** (cost real bugs already):
  - In non-raw Python strings `"\b"` is a backspace, not a word-boundary — use raw strings
    or `re` with explicit `\b`, or plain substrings with surrounding spaces.
  - Stems matter: `"finance"` does NOT match "financial" → use `"financ"`. `"health"`
    matches "healthier" (false positive) → prefer `"healthcare"/"patient"/...`.
  - Short tokens (`api`, `ev`, `ar`, `hr`) over-match — pad with spaces or use multiword.

---

## §Enrichment — `enrich.py` (Wikidata)
Flow per company that has a `company_url`:
1. `domain_of(company_url)` (strip scheme/`www`/path).
2. `wbsearchentities` by name → candidate Q-ids → `wbgetentities props=claims`.
3. **Gate:** accept a candidate only if its `P856` (official website) domain == the company
   domain. No match → skip (this is what prevents wrong-entity data).
4. Extract `P112` founders, `P571` inception year, `P452` industry, `P414`+qualifier `P249`
   ticker. Resolve founder/industry/exchange Q-ids → labels via batched `wbgetentities`.
5. **Fill only empty fields**; never overwrite. Drop ultra-generic industries
   (`GENERIC_SECTORS` stoplist: technology, software, business, ...).
6. Append `{file, company, source, wikidata_id, filled}` to `data/enrichment_report.json`.
- Also holds the Menlo `everywhere_tags` retag overrides for a few stubborn untagged records.
- `--limit N` for test runs. Polite `User-Agent` with contact + small sleeps.

---

## §Validation checklist (run after any scrape/enrich)
- `json.load` succeeds; top-level is a list; record count == expected.
- Every object has the **same field set** (and, for edits, unchanged field order).
- Every `everywhere_tags` value ∈ the 17-set; no duplicates within a record; cap ≤ 4.
- Enrichment: assert **0 overwrites** (diff vs a backup; only None/""/[] → value changes).
- Print coverage: per-field missing counts, by-tag counts, untagged count; spot-check a few
  known companies (e.g. a public one's ticker, a famous founder).
- **Investigate any 100%-empty column.** If a field is null/[] for *every* record, don't ship
  it as "N/A" until you've grepped the names + descriptions for that concept (see §Recon
  "Empty ≠ absent") — the data is often denormalized into the name suffix or description prose.
- Back up files (or rely on git) before destructive re-runs; scrapers write to `data/`.
