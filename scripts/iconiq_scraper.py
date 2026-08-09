#!/usr/bin/env python3
"""
ICONIQ Growth portfolio scraper -> iconiq_companies.json

Scrapes ICONIQ Growth's portfolio (https://www.iconiq.com/growth/companies) into
a JSON file. The site is a Webflow build with a Finsweet CMS list, and unlike
RRE the company data is **fully server-rendered in the static HTML** -- one page,
no pagination, no API. Each company is a `.companies-list_grid-item` and the list
is rendered **twice** (a reveal/animation duplicate), so we dedupe by name.

Per `.companies-list_grid-item`:
  - `<h2 class="heading-style-h3 is-companies">`           -> company name
  - `.text-size-medium.text-style-3lines.is-companies`     -> description (2 empty)
  - `a.companies-list_grid-item-reveal-wrap[href]`         -> external website (all 100)
  - `img.companies-list_logo[src]`                         -> logo
  - sector(s): the SAME 5 sectors are encoded in TWO places that DISAGREE per record
    (Empty != absent) -- the `data-groups` attr (lowercase slugs, e.g. ["all","ai"]) AND
    the `.hidden-params p[fs-cmsfilter-field="category"]` display names. Neither is a
    superset (6 companies differ; Anduril's only sector lives in data-groups, while
    Calendly's only shows in the category text), so we UNION both, skipping "all"/blanks.

What ICONIQ does NOT expose (so intentionally absent, not N/A to invent): no
founders, no investment stage, no status/exit/acquirer/ticker (checked names +
descriptions per the "Empty != absent" rule -- the only hit, Adyen, is a false
positive: "payments platform"), no founded year, no location, no per-company
ICONIQ detail page (the only link is the company's own site).

requirements:
    pip install requests beautifulsoup4

usage:
    python3 iconiq_scraper.py            # writes ../data/iconiq_companies.json
    python3 iconiq_scraper.py --limit 10 # only the first ~10 for a test run
"""

import json
import os
import re
import sys
import time
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

URL = "https://www.iconiq.com/growth/companies"
SOURCE_URL = "https://www.iconiq.com/growth/companies"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "data", "iconiq_companies.json")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}
TIMEOUT = 45
RETRIES = 3

# The `data-groups` attribute uses lowercase slugs; map them to the same display
# names the category <p> fields use, so the union dedupes cleanly. CANON_ORDER
# gives the output a stable, deterministic sector order.
SLUG_DISPLAY = {
    "ai": "AI",
    "consumer": "Consumer Internet",
    "saas": "Enterprise SaaS",
    "fintech": "Fintech",
    "healthcare": "Healthcare",
}
CANON_ORDER = ["AI", "Consumer Internet", "Enterprise SaaS", "Fintech", "Healthcare"]

# ICONIQ's 5 portfolio categories -> the 17-tag everywhere_tags taxonomy. "AI"
# and "Enterprise SaaS" are intentionally NOT mapped: AI alone is not a category
# (classify by the market served) and Enterprise SaaS spans dev-tools / work /
# data / security with no single tag -- both are left to the keyword fallback.
SECTOR_TAG_MAP = {
    "Healthcare": ["Health"],
    "Fintech": ["FinTech / Insurance"],
    "Consumer Internet": ["Consumer"],
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
                       "ransomware", "endpoint", "zero trust", "vulnerab", "authentication", "threat", "defense system", "identity",
                       "information protection"]),
    ("FinTech / Insurance", ["fintech", "payment", "bank", "lending", "loan", "insurance", "credit", "trading",
                             "wallet", "financ", "invoic", "accounting", "payroll", "treasury", "billing", "pricing platform",
                             "rebate", " tax", "audit", "money management", "robo-advisor", "brokerage", "spend management",
                             "capital markets", "investing", "claims"]),
    ("Web3 / Crypto", ["crypto", "blockchain", "web3", "token", "on-chain", "ethereum", "bitcoin", "decentral", "stablecoin", "nft"]),
    ("Gaming / Media / Entertainment", ["game", "gaming", "music", "video", "creator", "content", "publish",
                                        "entertain", "newsletter", "podcast", "film", "streaming", "social media", "media platform"]),
    ("Dev Tools / Cloud", ["developer", " api ", "apis", "api platform", "infrastructure", "database", "cloud",
                           "open source", "devops", "sdk", "kubernetes", "container", "observability", "deploy", "compute",
                           "storage", "serverless", "inference", "networking", "ethernet", "coding", "codebase", "low-code",
                           "no-code", "source code", "development platform", "incident", " sre", "voicemail", "communications",
                           "llm", "foundation model", "interpretability", "voice keyboard", "voice recognition", "frame relay",
                           "log management", "file sharing", "tech stack", "voice agent", "appliance software",
                           "text to speech"]),
    ("Data & Analytics", ["analytics", "business intelligence", "data platform", "data warehouse", "data lake",
                          "data pipeline", "insights", "dashboard", "experimentation", "decision intelligence", "data quality",
                          "analyz", "data curation", "quality management", "relationship intelligence",
                          "data discovery", "data analysis", "edge-data", "complex data", "real-world data", "analyst",
                          "data intelligence", "data transformation", "data integration"]),
    ("Future of Work", ["workforce", "hiring", "recruit", "employee", "productivity", "collaborat", "talent",
                        "workplace", "human resources", " hr ", "learning platform", "customer success", "customer service",
                        "customer support", "presales", " sales ", "onboarding", "workflow", "saas management", "ai assistant",
                        "project management", "partnerships platform", "partnership", "teamwork", "scheduling", "work assistant"]),
    ("Transportation / Mobility", ["mobility", "vehicle", "transport", "autonomous", "fleet", "driving", "aviation",
                                   "aircraft", "electric vehicle", "scooter", " bike", "boat", "watercraft", "rideshar", "travel"]),
    ("Logistics / Supply Chain", ["logistics", "supply chain", "supply and demand", "freight", "warehouse",
                                  "delivery", "procurement", "inventory", "fulfillment", "shipping"]),
    ("PropTech", ["real estate", "property", "housing", "mortgage", "rental", "construction", "tenant"]),
    ("CPG", ["beverage", "snack", "consumer packaged", "beauty", "cosmetic", "apparel", "grocery", "skincare",
             "eyewear", "glasses", "footwear", "pet sitter", "pet "]),
    ("Climate / Sustainability", ["climate", "carbon", "renewable", "solar", "battery", "sustainab", "emission",
                                  "clean energy", "ev charging", "electrif", "energy", "power is produced", "power grid"]),
    ("RegTech/Gov/Legal", ["legal", "compliance", "government", "regulat", "law firm", "attorney", "risk services",
                           "lawsuit", "lawyer"]),
    ("Deeptech / Robotics / AR/VR", ["robot", "hardware", "semiconductor", "chip", "drone", "aerospace",
                                     "augmented reality", "virtual reality", "satellite", "quantum", "sensor", "rfid", "wifi",
                                     "space", "rocket", "launch vehicle", "optics", "defense"]),
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


def everywhere_tags(name, description, sectors):
    """ICONIQ categories first (mapped via SECTOR_TAG_MAP), then keyword fallback
    on name + description to add/refine. Order most->least relevant, cap at 4."""
    tags = []
    for sec in sectors:
        for mapped in SECTOR_TAG_MAP.get(sec, []):
            if mapped not in tags:
                tags.append(mapped)
    text = f"{name or ''} {description or ''}".lower()
    for tag, kws in KEYWORD_TAGS:
        if tag in tags:
            continue
        if any(kw in text for kw in kws):
            tags.append(tag)
    return tags[:4]


def parse_item(it):
    h2 = it.select_one("h2.heading-style-h3")
    name = clean(h2.get_text()) if h2 else None
    if not name:
        return None

    desc_el = it.select_one(".text-size-medium.is-companies")
    description = clean(desc_el.get_text()) if desc_el else None

    a = it.select_one("a.companies-list_grid-item-reveal-wrap")
    company_url = clean(a.get("href")) if a and a.get("href") else None

    img = it.select_one("img.companies-list_logo")
    logo_url = clean(img.get("src")) if img and img.get("src") else None

    # union the two sector sources (data-groups slugs + category <p> display names)
    sec = set()
    try:
        for g in json.loads(it.get("data-groups") or "[]"):
            disp = SLUG_DISPLAY.get((g or "").lower())
            if disp:
                sec.add(disp)
    except (ValueError, TypeError):
        pass
    for p in it.select('p[fs-cmsfilter-field="category"]'):
        v = clean(p.get_text())
        if v and v.lower() != "all":
            sec.add(v)
    sectors = [s for s in CANON_ORDER if s in sec] + sorted(s for s in sec if s not in CANON_ORDER)

    return {
        "company_name": name,
        "description": description,
        "company_url": company_url,
        "logo_url": logo_url,
        "sectors": sectors,
        "everywhere_tags": everywhere_tags(name, description, sectors),
        "source_url": SOURCE_URL,
    }


def main():
    limit = int(sys.argv[sys.argv.index("--limit") + 1]) if "--limit" in sys.argv else None
    soup = BeautifulSoup(get(URL), "html.parser")
    items = soup.select(".companies-list_grid-item.w-dyn-item")

    scraped_at = datetime.now(timezone.utc).isoformat()
    out, seen = [], set()
    for it in items:
        rec = parse_item(it)
        if not rec or rec["company_name"] in seen:   # list is rendered twice -> dedupe by name
            continue
        seen.add(rec["company_name"])
        rec["scraped_at"] = scraped_at
        out.append(rec)
        if limit and len(out) >= limit:
            break

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2)

    # ---- summary ----
    n = len(out)
    print(f"wrote {n} companies -> {OUT}")
    for field in ("description", "company_url", "logo_url"):
        miss = sum(1 for r in out if not r[field])
        print(f"  {field:12s} missing: {miss}/{n}")
    print(f"  sectors empty: {sum(1 for r in out if not r['sectors'])}/{n}")
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
