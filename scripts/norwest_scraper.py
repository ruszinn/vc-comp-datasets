#!/usr/bin/env python3
"""
Norwest Venture Partners portfolio scraper -> norwest_companies.json

Scrapes Norwest's portfolio (https://www.norwest.com/companies/ -- nvp.com
redirects to norwest.com) into a JSON file. The site is WordPress + Elementor
("Dynamic Content for Elementor" loop-grid widget), and the company list is
**fully server-rendered HTML**, no API. Each company is an `<article class="companies ...">`
node carrying WordPress taxonomy terms directly as CSS classes:
  companies_regions-<slug>, companies_sectors-<slug>, companies_stages-<slug>,
  companies_status-<active|alumni>, companies_tags-<slug>  (many granular tags)
Pagination is server-side via `?sf_paged=N` (Search & Filter Pro), **50 companies
per page**, 11 pages -> 514 companies total (last page has 14).

Per article, the widget renders a fixed sequence of `<h2 class="elementor-heading-title">`
nodes:
  1. company name
  2. [optional] exit/status line: "acquired by <Acquirer>" / "Acquired by <Acquirer>"
     or "NASDAQ: <TICK>" / "Nasdaq: <TICK>" -- present only for some alumni companies
  3. "STAGE:" label -> one or more <span> values (Growth Equity / Venture)
  4. [optional] "PARTNER:"/"PARTNERS:" label -> Norwest partner name(s), each linking
     to norwest.com/team/<slug>/
  5. [optional] "ADVISOR:"/"ADVISORS:" label -> advisor name(s), same link pattern
  6. [optional] "VIEW" link -> the company's external website (or, for some acquired
     companies, the acquirer's site/product page -- captured as published, not altered)
A `#tileDesc` div (present for ~42% of companies) holds a one-line description.
No per-company detail page and no logo images in this list view.

Empty != absent, checked: exit/ticker text is denormalized into a "second h2"
between the name and the STAGE: label (not a separate structured field) --
handled by `derive_exit()`. A handful of "alumni" companies (e.g. Act-On
Software) have status=alumni but no exit text at all -- legitimately
undisclosed, not a parsing gap.

requirements:
    pip install requests beautifulsoup4

usage:
    python3 norwest_scraper.py            # writes ../data/norwest_companies.json
    python3 norwest_scraper.py --limit 40 # only the first ~40 for a test run
"""

import json
import os
import re
import sys
import time
from collections import Counter
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

BASE = "https://www.norwest.com/companies/"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "data", "norwest_companies.json")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}
TIMEOUT = 45
RETRIES = 3
SLEEP_BETWEEN_PAGES = 1.2

# Norwest's own "sectors" taxonomy (consumer/enterprise/healthcare) is too coarse
# to map 1:1; the richer "tags" taxonomy (saas, fintech, security, ...) is what
# actually drives everywhere_tags. Sectors are still recorded verbatim on each
# record for reference.
TAG_MAP = {
    "biotech": "BioTech",
    "life-science": "BioTech",
    "therapeutics": "BioTech",
    "diagnostics": "BioTech",
    "pharmaceutical-services": "BioTech",
    "healthcare-services": "Health",
    "healthcare-it": "Health",
    "medical-devices": "Health",
    "security": "Cybersecurity",
    "infra-tech": "Dev Tools / Cloud",
    "software": "Dev Tools / Cloud",
    "fintech": "FinTech / Insurance",
    "data": "Data & Analytics",
    "marketplaces": "Consumer",
    "e-commerce-retail": "Consumer",
    "consumer-products-services": "Consumer",
    "media": "Gaming / Media / Entertainment",
    "adtech": "Gaming / Media / Entertainment",
    "proptech": "PropTech",
    "food-tech-services": "CPG",
    "edtech": "Future of Work",
    "physicalai": "Deeptech / Robotics / AR/VR",
    # Deliberately NOT mapped (too coarse / not a market): "ai", "saas", "mobile",
    # "tech-enabled-services", "growth-products-services", "business-industrial-
    # products-services", the stray truncated terms "med"/"tech-enab" -- left to
    # the keyword classifier on name+description.
}

SECTOR_TAG_MAP = {
    "healthcare": "Health",
}

# everywhere_tags keyword classifier (substrings, lowercased) -- shared shape
# with menlo_scraper.py / rre_scraper.py / iconiq_scraper.py.
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
                             "capital markets", "investing", "claims"]),
    ("Web3 / Crypto", ["crypto", "blockchain", "web3", "token", "on-chain", "ethereum", "bitcoin", "decentral", "stablecoin", "nft"]),
    ("Gaming / Media / Entertainment", ["game", "gaming", "music", "video", "creator", "content", "publish",
                                        "entertain", "newsletter", "podcast", "film", "streaming", "social media", "media platform",
                                        "advertising", "ad network"]),
    ("Dev Tools / Cloud", ["developer", " api ", "apis", "api platform", "infrastructure", "database", "cloud",
                           "open source", "devops", "sdk", "kubernetes", "container", "observability", "deploy", "compute",
                           "storage", "serverless", "inference", "networking", "ethernet", "coding", "codebase", "low-code",
                           "no-code", "source code", "development platform", "incident", " sre", "voicemail", "communications",
                           "llm", "foundation model", "interpretability", "voice keyboard", "voice recognition", "frame relay",
                           "log management", "file sharing", "tech stack", "voice agent", "appliance software", "software company",
                           "saas platform", "enterprise software"]),
    ("Data & Analytics", ["analytics", "business intelligence", "data platform", "data warehouse", "data lake",
                          "data pipeline", "insights", "dashboard", "experimentation", "decision intelligence", "data quality",
                          "analyz", "data curation", "quality management", "relationship intelligence",
                          "data discovery", "data analysis", "edge-data", "complex data", "real-world data", "analyst",
                          "data intelligence", "data transformation", "data integration", "big data"]),
    ("Future of Work", ["workforce", "hiring", "recruit", "employee", "productivity", "collaborat", "talent",
                        "workplace", "human resources", " hr ", "learning platform", "customer success", "customer service",
                        "customer support", "presales", " sales ", "onboarding", "workflow", "saas management", "ai assistant",
                        "project management", "partnerships platform", "partnership", "teamwork", "scheduling",
                        "education", "learning", "student", "universit"]),
    ("Transportation / Mobility", ["mobility", "vehicle", "transport", "autonomous", "fleet", "driving", "aviation",
                                   "aircraft", "electric vehicle", "scooter", " bike", "boat", "watercraft", "rideshar", "travel"]),
    ("Logistics / Supply Chain", ["logistics", "supply chain", "supply and demand", "freight", "warehouse",
                                  "delivery", "procurement", "inventory", "fulfillment", "shipping"]),
    ("PropTech", ["real estate", "property", "housing", "mortgage", "rental", "construction", "tenant"]),
    ("CPG", ["beverage", "snack", "consumer packaged", "beauty", "cosmetic", "apparel", "grocery", "skincare",
             "eyewear", "glasses", "footwear", "pet sitter", "pet ", "food and beverage", "restaurant"]),
    ("Climate / Sustainability", ["climate", "carbon", "renewable", "solar", "battery", "sustainab", "emission",
                                  "clean energy", "ev charging", "electrif", "energy", "power is produced", "power grid"]),
    ("RegTech/Gov/Legal", ["legal", "compliance", "government", "regulat", "law firm", "attorney", "risk services",
                           "lawsuit", "lawyer"]),
    ("Deeptech / Robotics / AR/VR", ["robot", "hardware", "semiconductor", "chip", "drone", "aerospace",
                                     "augmented reality", "virtual reality", "satellite", "quantum", "sensor", "rfid", "wifi",
                                     "space", "rocket", "launch vehicle", "optics", "defense"]),
    ("Consumer", ["marketplace", "consumer", "shopping", "social network", "community", "app for", "app that",
                  "ecommerce", "e-commerce", "subscription", "retailer", "fashion"]),
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


def slug_words(slug):
    return slug.replace("-", " ")


def derive_exit(exit_text):
    """Parse the optional 'second h2' into (status, acquirer, ticker_symbol).
    Patterns seen: 'acquired by <X>' / 'Acquired by <X>', 'NASDAQ: <TICK>' /
    'Nasdaq: <TICK>'. Returns (None, None, None) if exit_text is None/blank."""
    if not exit_text:
        return None, None, None
    m = re.match(r"(?:nasdaq|nyse)\s*:\s*([A-Za-z.\-]{1,8})\s*$", exit_text, re.I)
    if m:
        return "Public", None, m.group(1).upper()
    m = re.match(r"acquired by\s+(.+)$", exit_text, re.I)
    if m:
        return "Acquired", clean(m.group(1)), None
    return None, None, None


def everywhere_tags(name, description, sectors, tags):
    """Norwest tags first (mapped via TAG_MAP), then sectors (SECTOR_TAG_MAP),
    then keyword fallback on name + description. Order most->least relevant,
    cap at 4, no duplicates."""
    out = []
    for t in tags:
        mapped = TAG_MAP.get(t)
        if mapped and mapped not in out:
            out.append(mapped)
    for s in sectors:
        mapped = SECTOR_TAG_MAP.get(s)
        if mapped and mapped not in out:
            out.append(mapped)
    text = f"{name or ''} {description or ''} {' '.join(slug_words(t) for t in tags)}".lower()
    for tag, kws in KEYWORD_TAGS:
        if tag in out:
            continue
        if any(kw in text for kw in kws):
            out.append(tag)
    return out[:4]


def parse_article(article, source_url):
    classes = article.get("class", [])
    post_id = None
    regions, sectors, stages, tags = [], [], [], []
    status_tax = None
    for c in classes:
        if c.startswith("post-"):
            post_id = c[len("post-"):]
        elif c.startswith("companies_regions-"):
            regions.append(c[len("companies_regions-"):])
        elif c.startswith("companies_sectors-"):
            sectors.append(c[len("companies_sectors-"):])
        elif c.startswith("companies_stages-"):
            stages.append(c[len("companies_stages-"):])
        elif c.startswith("companies_tags-"):
            tags.append(c[len("companies_tags-"):])
        elif c.startswith("companies_status-"):
            status_tax = c[len("companies_status-"):]

    h2_elems = article.select("h2.elementor-heading-title")
    texts = [clean(h.get_text()) or "" for h in h2_elems]
    if not texts or not texts[0]:
        return None
    name = texts[0]

    label_idx = {}
    for i, t in enumerate(texts):
        if t in ("STAGE:", "PARTNER:", "PARTNERS:", "ADVISOR:", "ADVISORS:"):
            label_idx.setdefault(t, i)
    view_idx = next((i for i, t in enumerate(texts) if t.startswith("VIEW")), None)

    stage_label_i = label_idx.get("STAGE:")
    exit_text = None
    if stage_label_i is not None and stage_label_i > 1:
        exit_text = texts[1]
    elif stage_label_i is None and view_idx != 1 and len(texts) > 1 and texts[1] not in (
        "PARTNER:", "PARTNERS:", "ADVISOR:", "ADVISORS:"
    ):
        exit_text = texts[1] or None
    status, acquirer, ticker_symbol = derive_exit(exit_text)

    def names_after_label(label):
        i = label_idx.get(label)
        if i is None or i + 1 >= len(h2_elems):
            return []
        return [clean(a.get_text()) for a in h2_elems[i + 1].select("a") if clean(a.get_text())]

    partners = names_after_label("PARTNER:") or names_after_label("PARTNERS:")
    advisors = names_after_label("ADVISOR:") or names_after_label("ADVISORS:")

    company_url = None
    if view_idx is not None:
        a_tag = h2_elems[view_idx].find("a")
        if a_tag and a_tag.get("href"):
            company_url = clean(a_tag.get("href"))

    desc_div = article.select_one("#tileDesc")
    description = clean(desc_div.get_text()) if desc_div else None

    return {
        "company_name": name,
        "description": description,
        "company_url": company_url,
        "status": status if status else ("Alumni" if status_tax == "alumni" else ("Active" if status_tax == "active" else None)),
        "acquirer": acquirer,
        "ticker_symbol": ticker_symbol,
        "stages": stages,
        "sectors": sectors,
        "tags": tags,
        "regions": regions,
        "partners": partners,
        "advisors": advisors,
        "everywhere_tags": everywhere_tags(name, description, sectors, tags),
        "source_url": source_url,
        "_post_id": post_id,  # internal, used only for de-dup; stripped before write
    }


def main():
    limit = int(sys.argv[sys.argv.index("--limit") + 1]) if "--limit" in sys.argv else None

    companies = []
    seen_ids = set()
    page = 1
    while True:
        url = BASE if page == 1 else f"{BASE}?sf_paged={page}"
        print(f"Fetching page {page} -> {url}")
        soup = BeautifulSoup(get(url), "html.parser")
        articles = soup.select("article.companies")
        if not articles:
            break
        added = 0
        for art in articles:
            rec = parse_article(art, url if page == 1 else BASE)
            if not rec:
                continue
            pid = rec.pop("_post_id")
            if pid and pid in seen_ids:
                continue
            if pid:
                seen_ids.add(pid)
            companies.append(rec)
            added += 1
        print(f"  +{added} companies (total {len(companies)})")
        if limit and len(companies) >= limit:
            companies = companies[:limit]
            break
        if len(articles) < 50:
            break  # last page (fewer than the standard 50/page)
        page += 1
        time.sleep(SLEEP_BETWEEN_PAGES)

    scraped_at = datetime.now(timezone.utc).isoformat()
    for c in companies:
        c["scraped_at"] = scraped_at

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(companies, f, ensure_ascii=False, indent=2)

    n = len(companies)
    print(f"\nWrote {n} companies -> {OUT}")

    by_status = Counter(c["status"] for c in companies)
    print("By status:", dict(by_status))
    print("With acquirer:", sum(1 for c in companies if c["acquirer"]),
          "| with ticker:", sum(1 for c in companies if c["ticker_symbol"]))
    print("With website:", sum(1 for c in companies if c["company_url"]),
          "| with description:", sum(1 for c in companies if c["description"]),
          "| with partner(s):", sum(1 for c in companies if c["partners"]),
          "| with advisor(s):", sum(1 for c in companies if c["advisors"]),
          "| with stage:", sum(1 for c in companies if c["stages"]),
          "| with sector:", sum(1 for c in companies if c["sectors"]),
          "| with region:", sum(1 for c in companies if c["regions"]))

    by_sector = Counter(s for c in companies for s in c["sectors"])
    by_tag_raw = Counter(t for c in companies for t in c["tags"])
    by_tag = Counter(t for c in companies for t in c["everywhere_tags"])
    untagged = [c["company_name"] for c in companies if not c["everywhere_tags"]]

    print("\nBy Norwest sector:")
    for s, k in by_sector.most_common():
        print(f"  {k:>4}  {s}")
    print("\nBy Norwest tag (raw taxonomy, top 15):")
    for t, k in by_tag_raw.most_common(15):
        print(f"  {k:>4}  {t}")
    print("\nBy everywhere_tag:")
    for t, k in by_tag.most_common():
        print(f"  {k:>4}  {t}")
    print(f"\nUntagged: {len(untagged)}/{n}")
    if untagged:
        print(" ", untagged[:20], "..." if len(untagged) > 20 else "")


if __name__ == "__main__":
    main()
