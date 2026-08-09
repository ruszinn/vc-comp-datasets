#!/usr/bin/env python3
"""
Notable Capital portfolio scraper -> notable_companies.json

Scrapes Notable Capital's (formerly GGV Capital U.S.) portfolio
(https://www.notablecap.com/companies) into a JSON file. The page is a
Webflow build with several parallel Finsweet CMS lists rendered as tabs
(FEATURED, ALL, AI, cloud infrastructure, Cybersecurity, consumer, fintech,
Enterprise, EXITED). This scraper reads the **ALL** tab, which is the
complete, de-duplicated portfolio list (127 companies over 5 pages of 30,
paginated server-side via `?7c8e1c9f_page=N`).

Each ALL-tab company card carries: name (image alt text, sometimes with a
"(NASDAQ: TICK)" / "(fka OldName)" / "(now NewName)" suffix), external
website link, a short description (frequently containing exit prose, e.g.
"X, which Y acquired in <year>..." or "...went public (NASDAQ: TICK) in
<year>"), a hidden location-facet hierarchy (location1 = most specific
region), and 0-2 small structured "tags" (e.g. "ACQ. BY IBM", "NASDAQ:HCP")
that directly encode acquirer / stock ticker for exited companies.

Sector: the ALL tab has no per-company sector field, but Notable also
publishes single-sector filter tabs (AI, cloud infrastructure, Cybersecurity,
consumer, fintech, Enterprise) that are each a subset of the same portfolio.
This scraper fetches those tabs too and unions membership by company name to
populate `sectors` (site-published categorization, not external enrichment).

No API key, no per-company detail crawling, no LLM.

requirements:
    pip install requests beautifulsoup4

usage:
    python3 notable_scraper.py            # writes ../data/notable_companies.json
    python3 notable_scraper.py --limit 20 # only the first ~20 for a test run
"""

import json
import os
import re
import sys
import time
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

BASE = "https://www.notablecap.com/companies"
ALL_PAGE_PARAM = "7c8e1c9f_page"  # Webflow/Finsweet pagination key for the "ALL" tab
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "data", "notable_companies.json")
SOURCE_URL = "https://www.notablecap.com/companies"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}
TIMEOUT = 45
RETRIES = 3

# Sector filter tabs to union against the ALL list (data-w-tab id -> display name).
# "FEATURED" and "EXITED" are curation flags, not sectors, so they're excluded.
SECTOR_TABS = {
    "Tab 12": "AI",
    "Tab 7": "cloud infrastructure",
    "Tab 8": "Cybersecurity",
    "Tab 9": "consumer",
    "Tab 10": "fintech",
    "Tab 11": "Enterprise",
}

# Map Notable's own sector tabs -> the 17-tag everywhere_tags taxonomy.
# "AI" and "cloud infrastructure"/"Enterprise" are intentionally left to the
# keyword classifier: AI alone is not a category, and "cloud infrastructure" /
# "Enterprise" span multiple of the 17 tags (Dev Tools / Cloud, Future of Work,
# Data & Analytics, Cybersecurity, ...) depending on the company.
SECTOR_TAG_MAP = {
    "Cybersecurity": "Cybersecurity",
    "consumer": "Consumer",
    "fintech": "FinTech / Insurance",
}

# everywhere_tags keyword classifier (substrings, lowercased) -- copied from
# menlo_scraper.py / rre_scraper.py.
KEYWORD_TAGS = [
    ("BioTech", ["biotech", "drug", "therapeut", "oncolog", "cancer", "tumor", "genomic", "genome",
                 "molecul", "antibod", "protein", "vaccine", "clinical-stage", "medicine", "opioid", "life science",
                 "synthetic biology", "biolog"]),
    ("Health", ["healthcare", "health care", "patient", "clinic", "medical", "mental health", "telehealth",
                "health system", "health record", "diagnos", "surgical", "doctor", "hospital", "pharmac", "therapy"]),
    ("Cybersecurity", ["cybersecurity", "security", "secure", "privacy", "fraud", "phishing", "malware",
                       "ransomware", "endpoint", "zero trust", "vulnerab", "authentication", "threat", "defense system",
                       "identity", "information protection"]),
    ("FinTech / Insurance", ["fintech", "payment", "bank", "lending", "loan", "insurance", "credit", "trading",
                             "wallet", "financ", "invoic", "accounting", "payroll", "treasury", "billing",
                             "pricing platform", "rebate", " tax", "audit", "money management", "robo-advisor",
                             "brokerage", "spend management", "capital markets", "cash back", "buy now, pay later"]),
    ("Web3 / Crypto", ["crypto", "blockchain", "web3", "token", "on-chain", "ethereum", "bitcoin", "decentral",
                       "stablecoin", "nft"]),
    ("Gaming / Media / Entertainment", ["game", "gaming", "music", "video", "creator", "content", "publish",
                                        "entertain", "newsletter", "podcast", "film", "streaming", "social media",
                                        "media platform", "lip-sync", "radio station"]),
    ("Dev Tools / Cloud", ["developer", " api ", "apis", "api platform", "infrastructure", "database", "cloud",
                           "open source", "devops", "sdk", "kubernetes", "container", "observability", "deploy",
                           "compute", "storage", "serverless", "inference", "networking", "ethernet", "coding",
                           "codebase", "low-code", "no-code", "source code", "development platform", "incident",
                           " sre", "voicemail", "communications", "llm", "foundation model", "interpretability",
                           "dns", "traffic management", "internet traffic", "app framework", "multi-cloud",
                           "hybrid environ"]),
    ("Data & Analytics", ["analytics", "business intelligence", "data platform", "data warehouse", "data lake",
                          "data pipeline", "insights", "dashboard", "experimentation", "decision intelligence",
                          "data quality", "analyz", "data curation", "quality management", "relationship intelligence",
                          "data discovery", "data analysis", "asset discovery", "observability"]),
    ("Future of Work", ["workforce", "hiring", "recruit", "employee", "productivity", "collaboration", "talent",
                        "workplace", "human resources", " hr ", "learning platform", "customer success",
                        "customer service", "customer support", "presales", " sales ", "onboarding", "workflow",
                        "saas management", "ai assistant", "project management", "crowdsourcing", "governance, risk",
                        "compliance platform"]),
    ("Transportation / Mobility", ["mobility", "vehicle", "transport", "autonomous", "fleet", "driving", "aviation",
                                   "aircraft", "electric vehicle", "scooter", " bike", "boat", "watercraft",
                                   "rideshar", "travel"]),
    ("Logistics / Supply Chain", ["logistics", "supply chain", "supply and demand", "freight", "warehouse",
                                  "delivery", "procurement", "inventory", "fulfillment", "shipping", "grocery stores"]),
    ("PropTech", ["real estate", "property", "housing", "mortgage", "rental", "construction", "tenant"]),
    ("CPG", ["beverage", "snack", "consumer packaged", "beauty", "cosmetic", "apparel", "grocery", "skincare",
             "eyewear", "glasses", "footwear", "pet sitter", "pet ", "intimate apparel"]),
    ("Climate / Sustainability", ["climate", "carbon", "renewable", "solar", "battery", "sustainab", "emission",
                                  "clean energy", "ev charging", "electrif"]),
    ("RegTech/Gov/Legal", ["legal", "compliance", "government", "regulat", "law firm", "attorney"]),
    ("Deeptech / Robotics / AR/VR", ["robot", "hardware", "semiconductor", "chip", "drone", "aerospace",
                                     "augmented reality", "virtual reality", "satellite", "quantum", "sensor",
                                     "rfid", "wifi"]),
    ("Consumer", ["marketplace", "consumer", "shopping", "social network", "community", "app for", "app that",
                  "ecommerce", "e-commerce", "subscription", "retailer", "universit", "student", "education",
                  "learning", "fashion", "hotel", "fantasy sports", "cash back", "gifting", "intimate apparel",
                  "urban logistics" ]),
]


def get(url):
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


def tab_pane(soup, tab_id):
    for p in soup.select(".tabs-content > .w-tab-pane"):
        if p.get("data-w-tab") == tab_id:
            return p
    return None


def parse_status(name, description, tags):
    """Derive (status, acquirer, ticker_symbol, exit_year) from:
    1. Structured tags: "ACQ. BY <X>" (acquirer, ALL CAPS as displayed) and
       "<EXCHANGE>:<TICKER>" (ticker).
    2. The description's exit prose ("X, which Y acquired in <year>...", "...went
       public (NASDAQ: TICK) in <year>...", "...completed its IPO... in <year>"),
       which gives a properly-cased acquirer name -- preferred over the all-caps tag
       when both are present.
    3. A "(EXCHANGE:TICKER)" suffix in the company name, as a last resort for ticker.
    Notable publishes no separate structured status field -- exit state lives only
    in these tags / prose / name suffix (Empty != absent).
    """
    acquirer_tag = ticker = exit_year = None
    status = "Active"

    for t in tags:
        m = re.match(r"ACQ\.?\s*BY\s+(.+)$", t, re.I)
        if m:
            acquirer_tag = clean(m.group(1))
            status = "Acquired"
            continue
        m = re.match(r"([A-Z]{2,10}):\s*([A-Za-z.\-]{1,10})$", t)
        if m:
            ticker = f"{m.group(1)}:{m.group(2)}"
            if status == "Active":
                status = "Public"

    text = description or ""
    acquirer_desc = None
    m = re.search(r"which\s+([A-Z][\w.&,\- ]*?)\s*(?:\([^)]*\))?\s+acquired\s+in", text)
    if m:
        acquirer_desc = clean(m.group(1))
        status = "Acquired"
    if not acquirer_desc:
        m = re.search(
            r"acquired by\s+(?:an investor group\s+)?([A-Z][\w.&,\- ]*?)"
            r"(?:,|\.|\s+in\s+(?:[A-Za-z]+\s+)?\d{4}|\s*\()",
            text,
        )
        if m:
            acquirer_desc = clean(m.group(1))
            status = "Acquired"
    acquirer = acquirer_desc or acquirer_tag

    if not exit_year and status == "Acquired":
        m = re.search(r"acquir\w*[^.]*?\bin\s+(?:[A-Za-z]+\s+)?(\d{4})", text, re.I)
        if m:
            exit_year = int(m.group(1))
    if not exit_year:
        m = re.search(r"(?:went public|ipo|completed its ipo)[^.]*?\bin\s+(?:[A-Za-z]+\s+)?(\d{4})", text, re.I)
        if m:
            exit_year = int(m.group(1))
            if status == "Active":
                status = "Public"
    if not ticker:
        m = re.search(r"\(([A-Z]{2,10}):\s*([A-Za-z.\-]{1,10})\)", name or "")
        if m:
            ticker = f"{m.group(1)}:{m.group(2)}"
            if status == "Active":
                status = "Public"
    if status == "Active" and re.search(r"\bacquir", text, re.I):
        status = "Acquired"

    return status, acquirer, ticker, exit_year


def everywhere_tags(name, description, sectors):
    tags = []
    cat_map = {k.lower(): v for k, v in SECTOR_TAG_MAP.items()}
    for s in sectors:
        mapped = cat_map.get((s or "").lower())
        if mapped and mapped not in tags:
            tags.append(mapped)
    text = f"{name or ''} {description or ''}".lower()
    for tag, kws in KEYWORD_TAGS:
        if tag in tags:
            continue
        if any(kw in text for kw in kws):
            tags.append(tag)
    return tags[:4]


def parse_all_item(item):
    a = item.select_one("a.c-logo_list_wrap")
    website = clean(a.get("href")) if a and a.get("href") not in (None, "#") else None
    if a and a.get("href") == "#":
        website = None

    img = item.select_one("img.c-company_logo_list")
    name = clean(img.get("alt")) if img else None
    logo_url = clean(img.get("src")) if img else None
    if not name:
        return None

    tags = []
    for tag_box in item.select(".c-tag.cc-stroke"):
        if "w-condition-invisible" in (tag_box.get("class") or []):
            continue
        txt = tag_box.select_one(".c-text_xs_new")
        v = clean(txt.get_text()) if txt else None
        if v:
            tags.append(v)

    desc_el = item.select_one('p[fs-cmsfilter-field="description"]')
    description = clean(desc_el.get_text()) if desc_el else None

    loc_wrap = item.select_one(".c-locations-filters")
    location = None
    if loc_wrap:
        loc1 = loc_wrap.select_one('[fs-cmsfilter-field="location1"]')
        location = clean(loc1.get_text()) if loc1 else None

    status, acquirer, ticker_symbol, exit_year = parse_status(name, description, tags)

    return {
        "company_name": name,
        "description": description,
        "company_url": website,
        "logo_url": logo_url,
        "location": location,
        "status": status,
        "acquirer": acquirer,
        "ticker_symbol": ticker_symbol,
        "exit_year": exit_year,
        "tags": tags,
        "sectors": [],  # filled in main() from the sector-tab union
        "everywhere_tags": [],  # filled in main() once sectors are known
        "source_url": SOURCE_URL,
        "scraped_at": None,  # filled in main()
    }


def fetch_all_tab(limit=None):
    """Paginate the ALL tab (?7c8e1c9f_page=N), 30/page, until an empty page."""
    companies, seen = [], set()
    page = 1
    while True:
        url = f"{BASE}?{ALL_PAGE_PARAM}={page}"
        print(f"Fetching ALL tab page {page} -> {url}")
        soup = BeautifulSoup(get(url), "html.parser")
        pane = tab_pane(soup, "Tab 2")
        items = pane.select(".c-wrap.cc-width_100.cc-relative.w-dyn-item") if pane else []
        if not items:
            break
        for it in items:
            rec = parse_all_item(it)
            if not rec:
                continue
            k = rec["company_name"].strip().lower()
            if k in seen:
                print(f"  ! duplicate '{rec['company_name']}' — keeping first", file=sys.stderr)
                continue
            seen.add(k)
            companies.append(rec)
        if limit and len(companies) >= limit:
            companies = companies[:limit]
            break
        page += 1
        time.sleep(1.0)
    return companies


def fetch_sector_members():
    """Fetch each single-sector filter tab (paginating as needed) and return
    {sector_name: set(company_name_lower)}. These tabs are subsets of the same
    ALL portfolio, site-published by Notable itself (not external enrichment)."""
    members = {name: set() for name in SECTOR_TABS.values()}
    # First page of every tab is already present on the base page - fetch once.
    print("Fetching sector tabs (base page) ...")
    soup = BeautifulSoup(get(BASE), "html.parser")
    time.sleep(1.0)
    pane_keys = {}
    for tab_id, sector in SECTOR_TABS.items():
        pane = tab_pane(soup, tab_id)
        if not pane:
            continue
        for c in pane.select('[fs-cmsfilter-field="company"]'):
            v = clean(c.get_text())
            if v:
                members[sector].add(v.lower())
        # look for this tab's own pagination key on the page (only present if >30 rows)
        next_link = pane.select_one("a.w-pagination-next")
        if next_link and next_link.get("href"):
            m = re.search(r"\?([0-9a-f]+)_page=2", next_link["href"])
            if m:
                pane_keys[tab_id] = m.group(1)

    # Fetch additional pages for any sector tab that has more than one page.
    for tab_id, key in pane_keys.items():
        sector = SECTOR_TABS[tab_id]
        page = 2
        while True:
            url = f"{BASE}?{key}_page={page}"
            print(f"Fetching sector tab '{sector}' page {page} -> {url}")
            soup2 = BeautifulSoup(get(url), "html.parser")
            pane2 = tab_pane(soup2, tab_id)
            names = pane2.select('[fs-cmsfilter-field="company"]') if pane2 else []
            if not names:
                break
            new_count = 0
            for c in names:
                v = clean(c.get_text())
                if v and v.lower() not in members[sector]:
                    members[sector].add(v.lower())
                    new_count += 1
            if new_count == 0:
                break
            page += 1
            time.sleep(1.0)
    return members


def main():
    limit = None
    if "--limit" in sys.argv:
        limit = int(sys.argv[sys.argv.index("--limit") + 1])

    companies = fetch_all_tab(limit=limit)

    sector_members = fetch_sector_members()
    for c in companies:
        name_l = c["company_name"].strip().lower()
        secs = [sector for sector, names in sector_members.items() if name_l in names]
        c["sectors"] = secs
        c["everywhere_tags"] = everywhere_tags(c["company_name"], c["description"], secs)

    scraped_at = datetime.now(timezone.utc).isoformat()
    for c in companies:
        c["scraped_at"] = scraped_at

    companies.sort(key=lambda o: o["company_name"].lower())

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(companies, f, ensure_ascii=False, indent=2)

    from collections import Counter
    by_status = Counter(o["status"] for o in companies)
    by_sector = Counter(s for o in companies for s in o["sectors"])
    by_tag = Counter(t for o in companies for t in o["everywhere_tags"])
    print(f"\nWrote {len(companies)} companies -> {OUT}")
    print("By status:", dict(by_status),
          "| with acquirer:", sum(1 for o in companies if o["acquirer"]),
          "| with ticker:", sum(1 for o in companies if o["ticker_symbol"]),
          "| with exit_year:", sum(1 for o in companies if o["exit_year"]))
    print("With website:", sum(1 for o in companies if o["company_url"]),
          "| with description:", sum(1 for o in companies if o["description"]),
          "| with location:", sum(1 for o in companies if o["location"]),
          "| with sectors:", sum(1 for o in companies if o["sectors"]),
          "| untagged:", sum(1 for o in companies if not o["everywhere_tags"]))
    print("By sector (site-published):")
    for t, c in by_sector.most_common():
        print(f"  {c:>4}  {t}")
    print("By everywhere_tag:")
    for t, c in by_tag.most_common():
        print(f"  {c:>4}  {t}")


if __name__ == "__main__":
    main()
