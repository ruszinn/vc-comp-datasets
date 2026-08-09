#!/usr/bin/env python3
"""
RRE Ventures portfolio scraper -> rre_companies.json

Scrapes RRE Ventures' portfolio (https://rre.com/portfolio) into a JSON file.
The site is a Webflow build with two parallel Finsweet CMS lists: a compact
"card" grid and a richer "modal" list. The modal list carries everything the
card has plus the website, description, founded/invested years and headquarters,
so this scraper reads ONLY the modal collection. It is paginated server-side at
20 items/page via Webflow's `?79e6a7d8_page=N` query string (13 pages, 250
companies). No API key, no per-company crawling, no LLM.

requirements:
    pip install requests beautifulsoup4

usage:
    python3 rre_scraper.py            # writes ../data/rre_companies.json
    python3 rre_scraper.py --limit 40 # only the first ~40 (2 pages) for a test run
"""

import json
import os
import re
import sys
import time
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

BASE = "https://rre.com/portfolio"
MODAL_PAGE_PARAM = "79e6a7d8_page"   # Webflow pagination key for the modal CMS list
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "data", "rre_companies.json")
SOURCE_URL = "https://rre.com/portfolio"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}
TIMEOUT = 45
RETRIES = 3

# Map RRE's own portfolio categories -> the 17-tag everywhere_tags taxonomy.
# "AI" is intentionally NOT mapped (AI alone is not a category -- classify by the
# market it serves, handled by the keyword fallback). "Featured" is a curation
# flag, not a sector, so it is ignored here.
SECTOR_TAG_MAP = {
    "Fintech": "FinTech / Insurance",
    "Crypto": "Web3 / Crypto",
    "Healthcare": "Health",
    "Hardware": "Deeptech / Robotics / AR/VR",
    "Robotics": "Deeptech / Robotics / AR/VR",
    "Space": "Deeptech / Robotics / AR/VR",
    "Media": "Gaming / Media / Entertainment",
    "Consumer": "Consumer",
}

# everywhere_tags keyword classifier (substrings, lowercased) -- copied from
# menlo_scraper.py. Used to refine the firm's coarse categories (esp. "AI" and
# "Enterprise/Saas", which don't map to a single tag) from name + description.
KEYWORD_TAGS = [
    ("BioTech", ["biotech", "drug", "therapeut", "oncolog", "cancer", "tumor", "genomic", "genome",
                 "molecul", "antibod", "protein", "vaccine", "clinical-stage", "medicine", "opioid", "life science",
                 "synthetic biology", "biolog"]),
    ("Health", ["healthcare", "health care", "patient", "clinic", "medical", "mental health", "telehealth",
                "health system", "health record", "diagnos", "surgical", "doctor", "hospital", "pharmac", "therapy"]),
    ("Cybersecurity", ["cybersecurity", "security", "secure", "privacy", "fraud", "phishing", "malware",
                       "ransomware", "endpoint", "zero trust", "vulnerab", "authentication", "threat", "defense system", "identity",
                       "information protection"]),
    ("FinTech / Insurance", ["fintech", "payment", "bank", "lending", "loan", "insurance", "credit", "trading",
                             "wallet", "financ", "invoic", "accounting", "payroll", "treasury", "billing", "pricing platform",
                             "rebate", " tax", "audit", "money management", "robo-advisor", "brokerage", "spend management",
                             "capital markets"]),
    ("Web3 / Crypto", ["crypto", "blockchain", "web3", "token", "on-chain", "ethereum", "bitcoin", "decentral", "stablecoin", "nft"]),
    ("Gaming / Media / Entertainment", ["game", "gaming", "music", "video", "creator", "content", "publish",
                                        "entertain", "newsletter", "podcast", "film", "streaming", "social media", "media platform"]),
    ("Dev Tools / Cloud", ["developer", " api ", "apis", "api platform", "infrastructure", "database", "cloud",
                           "open source", "devops", "sdk", "kubernetes", "container", "observability", "deploy", "compute",
                           "storage", "serverless", "inference", "networking", "ethernet", "coding", "codebase", "low-code",
                           "no-code", "source code", "development platform", "incident", " sre", "voicemail", "communications",
                           "llm", "foundation model", "interpretability", "voice keyboard", "voice recognition", "frame relay",
                           "log management", "file sharing", "tech stack", "voice agent", "appliance software"]),
    ("Data & Analytics", ["analytics", "business intelligence", "data platform", "data warehouse", "data lake",
                          "data pipeline", "insights", "dashboard", "experimentation", "decision intelligence", "data quality",
                          "analyz", "data curation", "quality management", "relationship intelligence",
                          "data discovery", "data analysis", "edge-data", "complex data", "real-world data"]),
    ("Future of Work", ["workforce", "hiring", "recruit", "employee", "productivity", "collaboration", "talent",
                        "workplace", "human resources", " hr ", "learning platform", "customer success", "customer service",
                        "customer support", "presales", " sales ", "onboarding", "workflow", "saas management", "ai assistant",
                        "project management", "partnerships platform", "partnership"]),
    ("Transportation / Mobility", ["mobility", "vehicle", "transport", "autonomous", "fleet", "driving", "aviation",
                                   "aircraft", "electric vehicle", "scooter", " bike", "boat", "watercraft", "rideshar", "travel"]),
    ("Logistics / Supply Chain", ["logistics", "supply chain", "supply and demand", "freight", "warehouse",
                                  "delivery", "procurement", "inventory", "fulfillment", "shipping"]),
    ("PropTech", ["real estate", "property", "housing", "mortgage", "rental", "construction", "tenant"]),
    ("CPG", ["beverage", "snack", "consumer packaged", "beauty", "cosmetic", "apparel", "grocery", "skincare",
             "eyewear", "glasses", "footwear", "pet sitter", "pet "]),
    ("Climate / Sustainability", ["climate", "carbon", "renewable", "solar", "battery", "sustainab", "emission",
                                  "clean energy", "ev charging", "electrif", "energy", "power is produced", "power grid"]),
    ("RegTech/Gov/Legal", ["legal", "compliance", "government", "regulat", "law firm", "attorney", "risk services"]),
    ("Deeptech / Robotics / AR/VR", ["robot", "hardware", "semiconductor", "chip", "drone", "aerospace",
                                     "augmented reality", "virtual reality", "satellite", "quantum", "sensor", "rfid", "wifi",
                                     "space", "rocket", "launch vehicle"]),
    ("Consumer", ["marketplace", "consumer", "shopping", "social network", "community", "app for", "app that",
                  "ecommerce", "e-commerce", "subscription", "retailer", "universit", "student", "education",
                  "learning", "fashion"]),
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


def to_year(s):
    s = clean(s)
    if s and s.isdigit() and 1900 <= int(s) <= 2100:
        return int(s)
    return None


def derive_status(name, description):
    """RRE has no structured stage/status field; exits are encoded in the company
    NAME suffix instead. Return (status, acquirer, ticker_symbol, exit_year):
      - "(NYSE: PLTR)" / "(IPO: DOOR)" -> Public, ticker = "NYSE: PLTR"
      - "(Acquired)"   -> Acquired; acquirer + year parsed from the description's
                          trailing "Acquired by <X> in <YYYY>." sentence (when present)
      - otherwise      -> Active
    """
    nm = name or ""
    mt = re.search(r"\((?:[A-Za-z]{2,6}|IPO):\s*[A-Za-z.\-]{1,8}\)\s*$", nm)
    if mt:
        return "Public", None, clean(mt.group(0).strip("() ")), None
    if re.search(r"\(Acquired\)\s*$", nm, re.I):
        acquirer = exit_year = None
        m = re.search(r"acquired by\s+(.+?)(?:\s+(?:in\s+)?(\d{4}))?\s*(?:\.|$)",
                      description or "", re.I)
        if m:
            acquirer = clean(m.group(1))
            if m.group(2):
                exit_year = int(m.group(2))
        return "Acquired", acquirer, None, exit_year
    return "Active", None, None, None


def everywhere_tags(name, description, categories):
    """RRE categories first (mapped via SECTOR_TAG_MAP), then keyword fallback on
    name + description to add/refine. Order most->least relevant, cap at 4."""
    tags = []
    cat_map = {k.lower(): v for k, v in SECTOR_TAG_MAP.items()}
    for cat in categories:
        mapped = cat_map.get((cat or "").lower())
        if mapped and mapped not in tags:
            tags.append(mapped)
    text = f"{name or ''} {description or ''}".lower()
    for tag, kws in KEYWORD_TAGS:
        if tag in tags:
            continue
        if any(kw in text for kw in kws):
            tags.append(tag)
    return tags[:4]


def parse_modal_item(item):
    name = clean(item.get("slider-name"))
    h2 = item.select_one('h2[fs-list-field="name"]')
    name = clean(h2.get_text()) if h2 else name
    if not name:
        return None

    web = item.select_one("a[company-link]")
    company_url = clean(web.get("href")) if web and web.get("href") else None

    logo = item.select_one("img.portfolio_modal-image")
    logo_url = clean(logo.get("src")) if logo else None

    # categories: the hidden list at the top of each slider item
    categories = []
    for c in item.select('[fs-list-field="category"]'):
        v = clean(c.get_text())
        if v and v not in categories:
            categories.append(v)

    # description: the single <h3> inside the modal grids (header is an <h2>)
    description = None
    for h3 in item.select(".portfolio-modal_grid h3"):
        txt = clean(h3.get_text())
        if txt:
            description = txt
            break

    # detail rows: eyebrow label -> .text-size-large value (empty ones skipped)
    details = {}
    for d in item.select(".portfolio_modal-details"):
        eb = d.select_one(".text-size-eyebrow")
        val = d.select_one(".text-size-large")
        if not eb:
            continue
        label = clean(eb.get_text())
        v = clean(val.get_text()) if val else None
        if label and v:
            details[label.lower()] = v

    status, acquirer, ticker_symbol, exit_year = derive_status(name, description)

    return {
        "company_name": name,
        "description": description,
        "company_url": company_url,
        "logo_url": logo_url,
        "categories": categories,
        "headquarters": details.get("headquarters"),
        "year_founded": to_year(details.get("founded")),
        "rre_invested_year": to_year(details.get("rre invested")),
        "status": status,
        "acquirer": acquirer,
        "ticker_symbol": ticker_symbol,
        "exit_year": exit_year,
        "everywhere_tags": everywhere_tags(name, description, categories),
        "source_url": SOURCE_URL,
        "scraped_at": datetime.now(timezone.utc).isoformat(),
    }


def main():
    limit = None
    if "--limit" in sys.argv:
        limit = int(sys.argv[sys.argv.index("--limit") + 1])

    companies, seen = [], set()
    page = 1
    while True:
        url = f"{BASE}?{MODAL_PAGE_PARAM}={page}"
        print(f"Fetching page {page} -> {url}")
        soup = BeautifulSoup(get(url), "html.parser")
        items = soup.select(".portfolio_modal-slider-item")
        if not items:
            break
        for it in items:
            rec = parse_modal_item(it)
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

    companies.sort(key=lambda o: o["company_name"].lower())

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(companies, f, ensure_ascii=False, indent=2)

    from collections import Counter
    by_status = Counter(o["status"] for o in companies)
    by_cat = Counter(c for o in companies for c in o["categories"])
    by_tag = Counter(t for o in companies for t in o["everywhere_tags"])
    print(f"\nWrote {len(companies)} companies -> {OUT}")
    print("By status:", dict(by_status),
          "| with acquirer:", sum(1 for o in companies if o["acquirer"]),
          "| with ticker:", sum(1 for o in companies if o["ticker_symbol"]))
    print("With website:", sum(1 for o in companies if o["company_url"]),
          "| with description:", sum(1 for o in companies if o["description"]),
          "| with founded:", sum(1 for o in companies if o["year_founded"]),
          "| with RRE-invested:", sum(1 for o in companies if o["rre_invested_year"]),
          "| with HQ:", sum(1 for o in companies if o["headquarters"]),
          "| untagged:", sum(1 for o in companies if not o["everywhere_tags"]))
    print("By RRE category:")
    for t, c in by_cat.most_common():
        print(f"  {c:>4}  {t}")
    print("By everywhere_tag:")
    for t, c in by_tag.most_common():
        print(f"  {c:>4}  {t}")


if __name__ == "__main__":
    main()
