#!/usr/bin/env python3
"""
Bessemer Venture Partners portfolio scraper -> bessemer_companies.json

Scrapes BVP's portfolio (https://www.bvp.com/companies) into a JSON file.
The entire portfolio (516 companies) is server-rendered into ONE static WordPress
page -- no pagination, no lazy-load, no per-company crawl needed. Each company is
an `<article class="box investment">` that already carries everything: name,
description, website, BVP investor(s), founded/partnered years, sector "Roadmap"
tags (canonically identified via the numeric `data-roadmaps` attribute, mapped
through the page's own `app.portfolio_roadmapMap` id->slug + `<select>` id->label),
and -- for exited companies -- a dedicated `enduring` field spelling out
"ACQUIRED BY: X", "MERGED WITH: X", "EXCHANGE: TICKER", or a combination
("NASDAQ: XLRN / ACQUIRED BY: MERCK"). One company (Skype) has its acquirer info
only in the description prose ("Empty != absent" case) -- handled as a fallback.
No API key, no per-company crawling, no LLM.

requirements:
    pip install requests beautifulsoup4

usage:
    python3 bessemer_scraper.py            # writes ../data/bessemer_companies.json
    python3 bessemer_scraper.py --limit 40 # only the first 40 companies (test run)
"""

import html as ihtml
import json
import os
import re
import sys
import time
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

URL = "https://www.bvp.com/companies"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "data", "bessemer_companies.json")
SOURCE_URL = "https://www.bvp.com/companies"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}
TIMEOUT = 45
RETRIES = 3

# BVP's own "Roadmap" filter ids -> display label, read straight off the page's
# <select id="portfolio-roadmaps"> options (verified against every company's
# data-roadmaps attribute -- 0 mismatches across 516 companies). Split into
# sector roadmaps vs. geography roadmaps (Israel/India/Europe aren't sectors).
ROADMAP_ID_LABEL = {
    194: "AI & ML", 195: "Biotech", 196: "Cloud", 197: "Consumer", 199: "Crypto",
    198: "Cybersecurity", 295: "Data", 200: "Deep tech", 201: "Developer",
    219: "Enterprise", 222: "Europe", 202: "Fintech", 203: "Healthcare",
    221: "India", 220: "Israel", 218: "Marketplaces", 204: "Vertical software",
}
GEO_ROADMAPS = {"Europe", "India", "Israel"}

# Map BVP's own sector roadmaps -> the 17-tag everywhere_tags taxonomy.
# "AI & ML", "Enterprise", "Cloud", "Vertical software", "Data", "Marketplaces"
# are intentionally left to the keyword fallback below (AI alone isn't a
# category; the others span multiple of the 17 tags depending on the company).
SECTOR_TAG_MAP = {
    "Biotech": "BioTech",
    "Healthcare": "Health",
    "Cybersecurity": "Cybersecurity",
    "Fintech": "FinTech / Insurance",
    "Crypto": "Web3 / Crypto",
    "Deep tech": "Deeptech / Robotics / AR/VR",
    "Developer": "Dev Tools / Cloud",
}

# everywhere_tags keyword classifier (substrings, lowercased) -- copied from
# menlo_scraper.py / rre_scraper.py. Refines BVP's coarse roadmaps (esp. "AI & ML",
# "Enterprise", "Cloud", "Vertical software", "Marketplaces", "Data") from name +
# description, and covers the handful of companies with zero roadmap tags.
KEYWORD_TAGS = [
    ("BioTech", ["biotech", "drug", "therapeut", "oncolog", "cancer", "tumor", "genomic", "genome",
                 "molecul", "antibod", "protein", "vaccine", "clinical-stage", "medicine", "opioid", "life science",
                 "synthetic biology", "biolog"]),
    ("Health", ["healthcare", "health care", "patient", "clinic", "medical", "mental health", "telehealth",
                "health system", "health record", "diagnos", "surgical", "doctor", "hospital", "pharmac", "therapy"]),
    ("Cybersecurity", ["cybersecurity", "security", "secure", "privacy", "fraud", "phishing", "malware",
                       "ransomware", "endpoint", "zero trust", "vulnerab", "authentication", "threat", "defense system", "identity"]),
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
                           "llm", "foundation model", "interpretability"]),
    ("Data & Analytics", ["analytics", "business intelligence", "data platform", "data warehouse", "data lake",
                          "data pipeline", "insights", "dashboard", "experimentation", "decision intelligence", "data quality",
                          "analyz", "data curation", "quality management", "data streaming", "data infrastructure"]),
    ("Future of Work", ["workforce", "hiring", "recruit", "employee", "productivity", "collaboration", "talent",
                        "workplace", "human resources", " hr ", "learning platform", "customer success", "customer service",
                        "customer support", "presales", " sales ", "onboarding", "workflow", "saas management", "ai assistant",
                        "agentic", "project management"]),
    ("Transportation / Mobility", ["mobility", "vehicle", "transport", "autonomous", "fleet", "driving", "aviation",
                                   "aircraft", "electric vehicle", "scooter", " bike", "boat", "watercraft", "rideshar",
                                   "ride-shar", "ride shar"]),
    ("Logistics / Supply Chain", ["logistics", "supply chain", "supply and demand", "freight", "warehouse",
                                  "delivery", "procurement", "inventory", "fulfillment", "shipping", "last-mile", "last mile"]),
    ("PropTech", ["real estate", "property", "housing", "mortgage", "rental", "construction", "tenant",
                  "home renovation", "homeowner"]),
    ("CPG", ["beverage", "snack", "consumer packaged", "beauty", "cosmetic", "apparel", "grocery", "skincare",
             "eyewear", "glasses", "footwear", "coffee", "food"]),
    ("Climate / Sustainability", ["climate", "carbon", "renewable", "solar", "battery", "sustainab", "emission",
                                  "clean energy", "ev charging", "electrif"]),
    ("RegTech/Gov/Legal", ["legal", "compliance", "government", "regulat", "law firm", "attorney",
                           "govtech", "electronic discovery", "document review", "voting system"]),
    ("Deeptech / Robotics / AR/VR", ["robot", "hardware", "semiconductor", "chip", "drone", "aerospace",
                                     "augmented reality", "virtual reality", "satellite", "quantum", "sensor", "rfid", "wifi",
                                     "physical intelligence", "physical ai"]),
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


def parse_enduring(raw):
    """Parse BVP's 'enduring' status string into (status, ticker_symbol, acquirer).
    Formats seen: 'NASDAQ: TWOU', 'ACQUIRED BY: WORKDAY', 'ACQUIRED BY OPTIONS TECHNOLOGY'
    (no colon), 'MERGED WITH: HEADSPACE', and combinations joined by ' / '
    ('NASDAQ: XLRN / ACQUIRED BY: MERCK')."""
    if not raw:
        return None, None, None
    s = clean(ihtml.unescape(raw))
    status = ticker = acquirer = None
    for part in [clean(p) for p in s.split("/")]:
        if not part:
            continue
        m = re.match(r"ACQUIRED BY:?\s*(.+)$", part, re.I)
        if m:
            acquirer = clean(m.group(1))
            status = "Acquired"
            continue
        m2 = re.match(r"MERGED WITH:?\s*(.+)$", part, re.I)
        if m2:
            acquirer = clean(m2.group(1))
            status = "Merged"
            continue
        m3 = re.match(r"^([A-Za-z][A-Za-z0-9 .]*?):\s*(\S.*)$", part)
        if m3:
            ticker = f"{clean(m3.group(1))}: {clean(m3.group(2))}"
            if status is None:
                status = "Public"
    return status, ticker, acquirer


def derive_exit_from_description(description):
    """Fallback for the rare company (Skype) whose acquirer is stated only in
    the free-text description, with no structured 'enduring' field. Returns
    (status, acquirer) or (None, None)."""
    if not description:
        return None, None
    acquirers = re.findall(r"acquired by\s+([A-Z][A-Za-z0-9&.,' ]*?)(?:\s+and\b|[.,]|$)", description)
    if acquirers:
        return "Acquired", clean(acquirers[-1])
    return None, None


def everywhere_tags(name, description, sectors):
    tags = []
    for sec in sectors:
        mapped = SECTOR_TAG_MAP.get(sec)
        if mapped and mapped not in tags:
            tags.append(mapped)
    text = f"{name or ''} {description or ''}".lower()
    for tag, kws in KEYWORD_TAGS:
        if tag in tags:
            continue
        if any(kw in text for kw in kws):
            tags.append(tag)
    return tags[:4]


def parse_article(article, roadmap_id_label):
    name_el = article.select_one(".company .name.click-to-open") or article.select_one(".company h3.name a")
    if not name_el:
        return None
    name = clean(name_el.get_text())
    if not name:
        return None

    profile_url = clean(name_el.get("href"))

    data_name = article.get("data-name")

    ids_raw = article.get("data-roadmaps") or ""
    roadmap_ids = [int(x) for x in ids_raw.split(",") if x.strip().isdigit()]
    roadmap_labels = [roadmap_id_label.get(i) for i in roadmap_ids if roadmap_id_label.get(i)]
    sectors = [r for r in roadmap_labels if r not in GEO_ROADMAPS]
    regions = [r for r in roadmap_labels if r in GEO_ROADMAPS]

    detail = article.select_one(".details.company")

    intro_el = detail.select_one(".intro") if detail else None
    description = clean(intro_el.get_text()) if intro_el else None

    website = None
    if detail:
        cta = detail.select_one(".ctas a.cta")
        if cta and cta.get("href"):
            website = clean(cta.get("href"))

    investors = []
    if detail:
        for a in detail.select(".investors a.team"):
            nm = clean(a.get_text())
            if nm and nm not in investors:
                investors.append(nm)

    year_founded = None
    if detail:
        f_el = detail.select_one(".founded .year")
        year_founded = to_year(f_el.get_text()) if f_el else None

    partnered_year = None
    if detail:
        p_el = detail.select_one(".partnered .year")
        partnered_year = to_year(p_el.get_text()) if p_el else None

    enduring_el = detail.select_one(".enduring") if detail else None
    enduring_raw = clean(enduring_el.get_text()) if enduring_el else None
    status, ticker_symbol, acquirer = parse_enduring(enduring_raw)

    if status is None:
        # "Empty != absent": fall back to mining the description prose (Skype).
        fb_status, fb_acquirer = derive_exit_from_description(description)
        status = fb_status or "Active"
        acquirer = acquirer or fb_acquirer
    elif status == "Public" and ticker_symbol is None:
        pass  # ticker missing but status known; leave ticker_symbol None

    return {
        "company_name": name,
        "description": description,
        "company_url": website,
        "company_profile_url": profile_url,
        "investors": investors,
        "year_founded": year_founded,
        "bvp_partnered_year": partnered_year,
        "sectors": sectors,
        "regions": regions,
        "status": status,
        "acquirer": acquirer,
        "ticker_symbol": ticker_symbol,
        "everywhere_tags": everywhere_tags(name, description, sectors),
        "source_url": SOURCE_URL,
        "scraped_at": None,  # filled in main() with the real run timestamp
    }


def main():
    limit = None
    if "--limit" in sys.argv:
        limit = int(sys.argv[sys.argv.index("--limit") + 1])

    print(f"Fetching {URL}")
    soup = BeautifulSoup(get(URL), "html.parser")

    articles = soup.select("#portfolio-list article.box.investment")
    print(f"Found {len(articles)} company articles")

    scraped_at = datetime.now(timezone.utc).isoformat()

    companies, seen = [], set()
    for article in articles:
        rec = parse_article(article, ROADMAP_ID_LABEL)
        if not rec:
            continue
        rec["scraped_at"] = scraped_at
        k = rec["company_name"].strip().lower()
        if k in seen:
            print(f"  ! duplicate '{rec['company_name']}' — keeping first", file=sys.stderr)
            continue
        seen.add(k)
        companies.append(rec)
        if limit and len(companies) >= limit:
            break

    companies.sort(key=lambda o: o["company_name"].lower())

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(companies, f, ensure_ascii=False, indent=2)

    from collections import Counter
    by_status = Counter(o["status"] for o in companies)
    by_sector = Counter(s for o in companies for s in o["sectors"])
    by_region = Counter(r for o in companies for r in o["regions"])
    by_tag = Counter(t for o in companies for t in o["everywhere_tags"])

    print(f"\nWrote {len(companies)} companies -> {OUT}")
    print("By status:", dict(by_status),
          "| with acquirer:", sum(1 for o in companies if o["acquirer"]),
          "| with ticker:", sum(1 for o in companies if o["ticker_symbol"]))
    print("With website:", sum(1 for o in companies if o["company_url"]),
          "| with description:", sum(1 for o in companies if o["description"]),
          "| with investors:", sum(1 for o in companies if o["investors"]),
          "| with founded year:", sum(1 for o in companies if o["year_founded"]),
          "| with partnered year:", sum(1 for o in companies if o["bvp_partnered_year"]),
          "| untagged:", sum(1 for o in companies if not o["everywhere_tags"]))
    print("By BVP sector roadmap:")
    for t, c in by_sector.most_common():
        print(f"  {c:>4}  {t}")
    print("By region roadmap:")
    for t, c in by_region.most_common():
        print(f"  {c:>4}  {t}")
    print("By everywhere_tag:")
    for t, c in by_tag.most_common():
        print(f"  {c:>4}  {t}")


if __name__ == "__main__":
    main()
