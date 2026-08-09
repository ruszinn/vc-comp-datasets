#!/usr/bin/env python3
"""
Coatue portfolio scraper -> coatue_companies.json

Scrapes Coatue's full portfolio (https://www.coatue.com/portfolio) into a JSON
file. The site is Next.js (getStaticProps) backed by Contentful. The static
page HTML / `__NEXT_DATA__` only embeds the first 48 of 372 companies (the
`PortfolioGrid` component's initial page); the rest is paginated client-side
via a small first-party JSON API discovered in the page's JS bundle
(`chunk_511...js`):

    GET https://www.coatue.com/api/portfolio?id=<gridSysId>&types=<t>&statuses=<s>&skip=<n>

`id` is the Contentful `sys.id` of the PortfolioGrid entry on the /portfolio
page (stable across runs unless Coatue rebuilds that page section); `skip`
pages through 48 rows at a time; `types`/`statuses` are the UI's Venture/Growth
and Active/Exit filters (left blank here to fetch everything, then dedupe by
Contentful sys.id). This mirrors what a browser does when you click through
Coatue's own filter buttons -- it's a first-party endpoint on coatue.com, not
a third-party enrichment source.

Schema is minimal because that's genuinely all Coatue's API returns per
company: name, external website (~5% null -- legit, mostly acquired/shut-down
companies), a small logo + a larger "logoWithColor" logo, `type` (Venture vs.
Growth -- which Coatue *fund* invested, not an industry sector), and `status`
(Active vs. Exit -- a coarse binary, no acquirer/ticker/exit-year/date).

"Empty != absent" checked and came up empty: company names carry no
parenthetical suffix (no "(Acquired)", no "(NYSE: X)" -- confirmed against
known post-IPO/acquired names like Box, Confluent, Anaplan, Agora), and the
API returns no description field at all, so there is no prose to mine for
acquirer/exit-year either. Coatue simply doesn't publish that granularity on
this page -- status stays a plain "Active"/"Exit" string, and acquirer/
ticker/exit_year/founders/location/founded-year are intentionally omitted
(not invented as null-scalar fields) since NONE of the API's own fields hint
at them. No per-company sector/vertical is exposed anywhere (the only filters
are fund `type` and `status`), so `everywhere_tags` here is 100% keyword
classification on the company name alone (no description text exists to
supplement it) -- expect a higher untagged/coarser-tagged rate than firms
that publish a description or sector list.

requirements:
    pip install requests beautifulsoup4

usage:
    python3 coatue_scraper.py            # writes ../data/coatue_companies.json
    python3 coatue_scraper.py --limit 20 # only the first ~20 for a test run
"""

import json
import os
import re
import sys
import time
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

PORTFOLIO_PAGE = "https://www.coatue.com/portfolio"
API_URL = "https://www.coatue.com/api/portfolio"
SOURCE_URL = "https://www.coatue.com/portfolio"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "data", "coatue_companies.json")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Referer": PORTFOLIO_PAGE,
}
TIMEOUT = 45
RETRIES = 3
PAGE_SIZE = 48
SLEEP = 0.6

# everywhere_tags keyword classifier (substrings, lowercased). Coatue exposes
# NO description or sector text -- the classifier only ever sees the bare
# company name, which is a much noisier signal than the name+description text
# other scrapers key on. To keep the false-positive rate down, keywords here
# are deliberately more conservative/specific than menlo_scraper.py's list:
# short, generic tokens ("ai", "tech", "data", "work", "car", "app", "market")
# are dropped or padded with spaces, since on bare brand names they collide
# with unrelated words (e.g. "tech" inside "Biotech", "car" inside "Bungalow").
# Net effect: more stragglers stay untagged, but fewer are mistagged.
KEYWORD_TAGS = [
    ("BioTech", ["biotech", "bio-", " bio ", "biosci", "drug", "therapeut", "oncolog", "cancer", "tumor",
                 "genomic", "genome", "molecul", "antibod", " protein", "vaccine", "life science",
                 "synthetic biology", "biopharma", "biolog"]),
    ("Health", ["health", "clinic", "medical", "diagnos", "surgical", "dental", "hospital", "biosciences",
                "therapeutic", "wellness", "patient"]),
    ("Cybersecurity", ["security", "secure", "cyber", "privacy", "fraud", "authentication", "encrypt",
                       "threat"]),
    ("FinTech / Insurance", ["fintech", " pay", "bank", "lending", " loan", "insur", "credit", "trading",
                             "capital", "financ", "wallet", "wealth", "billing", "treasury"]),
    ("Web3 / Crypto", ["crypto", "blockchain", "web3", "token", "coin", "ledger", "nft", "bitcoin"]),
    ("Gaming / Media / Entertainment", ["games", "gaming", " music", " video", "media", "studio", "entertain",
                                        "streaming", "podcast", " news"]),
    ("Dev Tools / Cloud", ["software", "platform", "cloud", "database", "devops", "infrastructure",
                           " api ", "compute", "developer", " labs", "networks", "networking"]),
    ("Data & Analytics", ["analytics", " data ", "-data", "data.", "datalab"]),
    ("Future of Work", ["workforce", "recruiting", "productivity", "collab"]),
    ("Transportation / Mobility", ["mobility", "automotive", "vehicle", "transport", "aviation", "aerospace",
                                   "fleet", "rideshar", "motors"]),
    ("Logistics / Supply Chain", ["logistics", "supply chain", "freight", "warehouse", "delivery",
                                  "fulfillment"]),
    ("PropTech", ["real estate", "realty", "housing", "mortgage", "proptech"]),
    ("CPG", ["beauty", "cosmetic", "apparel", "grocery", "skincare", "beverage", "footwear"]),
    ("Climate / Sustainability", ["climate", "carbon", "solar", "battery", "sustainab", "hydrogen",
                                  "clean energy", "renewable"]),
    ("RegTech/Gov/Legal", ["legal", "compliance", "regulat", "law firm", "safety"]),
    ("Deeptech / Robotics / AR/VR", ["robot", "aerospace", "satellite", "drone", "quantum", "semiconductor",
                                     "biometric", " systems"]),
    ("Consumer", ["marketplace", "commerce", "shopping"]),
]


def fetch_json(url, params=None):
    last = None
    for attempt in range(1, RETRIES + 1):
        try:
            r = requests.get(url, headers=HEADERS, params=params, timeout=TIMEOUT)
            r.raise_for_status()
            return r.json()
        except requests.RequestException as e:  # noqa
            last = e
            wait = 1.5 * attempt
            print(f"  ! request failed ({e}); retry {attempt}/{RETRIES} in {wait:.1f}s", file=sys.stderr)
            time.sleep(wait)
    raise SystemExit(f"FATAL: could not fetch {url}: {last}")


def fetch_text(url):
    last = None
    for attempt in range(1, RETRIES + 1):
        try:
            r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
            r.raise_for_status()
            return r.text
        except requests.RequestException as e:  # noqa
            last = e
            wait = 1.5 * attempt
            print(f"  ! request failed ({e}); retry {attempt}/{RETRIES} in {wait:.1f}s", file=sys.stderr)
            time.sleep(wait)
    raise SystemExit(f"FATAL: could not fetch {url}: {last}")


def clean(s):
    if s is None:
        return None
    s = re.sub(r"\s+", " ", s).strip()
    return s or None


def get_grid_id():
    """Fetch the /portfolio page and pull the PortfolioGrid's Contentful sys.id
    out of __NEXT_DATA__ (this is the `id` param the site's own JS passes to
    /api/portfolio)."""
    html = fetch_text(PORTFOLIO_PAGE)
    soup = BeautifulSoup(html, "html.parser")
    tag = soup.find("script", id="__NEXT_DATA__")
    if not tag or not tag.string:
        raise SystemExit("FATAL: could not find __NEXT_DATA__ on /portfolio")
    data = json.loads(tag.string)
    sections = data["props"]["pageProps"]["page"]["sectionsCollection"]["items"]
    grids = [s for s in sections if s.get("__typename") == "PortfolioGrid"]
    if not grids:
        raise SystemExit("FATAL: no PortfolioGrid section found on /portfolio")
    grid = grids[0]
    return grid["sys"]["id"], grid["itemsCollection"]["total"]


def everywhere_tags(name):
    """Coatue exposes no sector/vertical field and no description -- pure
    keyword classification on the company name. Order most->least relevant,
    cap at 4. Expect more untagged/coarser hits than firms with prose to mine."""
    tags = []
    text = f" {(name or '').lower()} "
    for tag, kws in KEYWORD_TAGS:
        if any(kw in text for kw in kws):
            tags.append(tag)
    return tags[:4]


def main():
    limit = int(sys.argv[sys.argv.index("--limit") + 1]) if "--limit" in sys.argv else None

    grid_id, total = get_grid_id()
    print(f"grid id={grid_id} total={total}")

    scraped_at = datetime.now(timezone.utc).isoformat()

    items, seen = [], set()
    skip = 0
    while True:
        data = fetch_json(API_URL, params={"id": grid_id, "skip": skip})
        batch = data.get("items", [])
        if not batch:
            break
        for it in batch:
            sid = (it.get("sys") or {}).get("id")
            if not sid or sid in seen:
                continue
            seen.add(sid)
            items.append(it)
            if limit and len(items) >= limit:
                break
        if limit and len(items) >= limit:
            break
        skip += PAGE_SIZE
        if skip >= data.get("total", total):
            break
        time.sleep(SLEEP)

    out = []
    for it in items:
        name = clean(it.get("name"))
        if not name:
            continue
        company_url = clean(it.get("url"))
        logo = it.get("logo") or {}
        logo_color = it.get("logoWithColor") or {}
        fund_type = clean(it.get("type"))          # "Venture" or "Growth"
        status = clean(it.get("status"))            # "Active" or "Exit"

        out.append({
            "company_name": name,
            "company_url": company_url,
            "logo_url": clean(logo.get("src")) or clean(logo_color.get("src")),
            "fund_type": fund_type,
            "status": status,
            "everywhere_tags": everywhere_tags(name),
            "source_url": SOURCE_URL,
            "scraped_at": scraped_at,
        })

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2)

    # ---- summary ----
    n = len(out)
    print(f"wrote {n} companies -> {OUT}")
    for field in ("company_url", "logo_url", "fund_type", "status"):
        miss = sum(1 for r in out if not r[field])
        print(f"  {field:12s} missing: {miss}/{n}")
    by_type = {}
    for r in out:
        by_type[r["fund_type"]] = by_type.get(r["fund_type"], 0) + 1
    print(f"  by fund_type: {by_type}")
    by_status = {}
    for r in out:
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1
    print(f"  by status: {by_status}")
    untagged = [r["company_name"] for r in out if not r["everywhere_tags"]]
    print(f"  untagged:      {len(untagged)}/{n}" + (f" -> {untagged}" if untagged else ""))
    by_tag = {}
    for r in out:
        for t in r["everywhere_tags"]:
            by_tag[t] = by_tag.get(t, 0) + 1
    print("  by everywhere_tag:")
    for t, k in sorted(by_tag.items(), key=lambda x: -x[1]):
        print(f"    {k:3d}  {t}")


if __name__ == "__main__":
    main()
