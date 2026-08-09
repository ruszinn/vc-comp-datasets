#!/usr/bin/env python3
"""
Founders Fund portfolio scraper -> foundersfund_companies.json

Scrapes Founders Fund's portfolio (https://foundersfund.com/portfolio/) into a
JSON file. The portfolio page is JS-rendered, but the site is WordPress and
exposes the `company` custom post type through the standard REST API:

    GET /wp-json/wp/v2/company?per_page=100&page=N   (62 companies, ~1 page)

Each record carries everything the page shows: title (name), content
(description), `link` (FF profile URL), a `profiles` HTML blob holding the
external website link, a `founders` list, the `industry` display name, the
`featured_image_thumbnail_url` logo, and a `class_list` whose
`company_industry-*` entries are the source of truth for sector(s) (one company,
Cedar, carries two). No API key, no per-company crawling, no LLM.

Notes on what FF does NOT expose (so these fields are intentionally absent, not
N/A to invent): no investment stage, no status/exit/acquirer/ticker (checked the
names + descriptions per the "Empty != absent" rule -- no exit state is encoded
there), no founded year, no location. The API's founder objects include a
`founder_crunchbase_slug` URL that is misnamed -- in practice it points to the
founder's Twitter / LinkedIn / Wikipedia / personal site (and sometimes
Crunchbase). These are source-published links (not external enrichment we fetched
from a banned DB), so per an explicit user decision they are kept as
`founders[].url` alongside `founders[].name`.

requirements:
    pip install requests

usage:
    python3 foundersfund_scraper.py            # writes ../data/foundersfund_companies.json
    python3 foundersfund_scraper.py --limit 10 # only the first ~10 for a test run
"""

import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from html import unescape

import requests

API = "https://foundersfund.com/wp-json/wp/v2/company"
SOURCE_URL = "https://foundersfund.com/portfolio/"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "data", "foundersfund_companies.json")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}
TIMEOUT = 45
RETRIES = 3

# FF's `company_industry-<slug>` taxonomy -> human display names (source of truth
# for sectors; one company carries two, so we read the class_list, not the single
# `industry` field which only shows one).
SECTOR_SLUG_DISPLAY = {
    "advanced-machines-intelligence": "Advanced Machines & Intelligence",
    "aerospace-transportation": "Aerospace & Transportation",
    "analytics-software": "Analytics & Software",
    "biotechnology-health": "Biotechnology & Health",
    "consumer-internet-media": "Consumer Internet & Media",
    "real-estate-technology": "Real Estate & Technology",
}

# FF's 6 sectors -> the 17-tag everywhere_tags taxonomy. "Analytics & Software"
# is intentionally NOT mapped: it just means "software" (the company could be
# fintech, dev-tools, data, AI, etc.), so it's left to the keyword fallback to
# classify by the market served. AI alone is not a category.
SECTOR_TAG_MAP = {
    "Aerospace & Transportation": ["Transportation / Mobility"],
    "Biotechnology & Health": ["BioTech", "Health"],
    "Real Estate & Technology": ["PropTech"],
    "Advanced Machines & Intelligence": ["Deeptech / Robotics / AR/VR"],
    "Consumer Internet & Media": ["Consumer"],
}

# everywhere_tags keyword classifier (substrings, lowercased) -- copied from
# menlo_scraper.py / rre_scraper.py. Refines the coarse FF sectors (esp.
# "Analytics & Software") from name + description.
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
                             "capital markets", "investing"]),
    ("Web3 / Crypto", ["crypto", "blockchain", "web3", "token", "on-chain", "ethereum", "bitcoin", "decentral", "stablecoin", "nft"]),
    ("Gaming / Media / Entertainment", ["game", "gaming", "music", "video", "creator", "content", "publish",
                                        "entertain", "newsletter", "podcast", "film", "streaming", "social media", "media platform"]),
    ("Dev Tools / Cloud", ["developer", " api ", "apis", "api platform", "infrastructure", "database", "cloud",
                           "open source", "devops", "sdk", "kubernetes", "container", "observability", "deploy", "compute",
                           "storage", "serverless", "inference", "networking", "ethernet", "coding", "codebase", "low-code",
                           "no-code", "source code", "development platform", "incident", " sre", "voicemail", "communications",
                           "llm", "foundation model", "interpretability", "voice keyboard", "voice recognition", "frame relay",
                           "log management", "file sharing", "tech stack", "voice agent", "appliance software",
                           "voip", "messaging"]),
    ("Data & Analytics", ["analytics", "business intelligence", "data platform", "data warehouse", "data lake",
                          "data pipeline", "insights", "dashboard", "experimentation", "decision intelligence", "data quality",
                          "analyz", "data curation", "quality management", "relationship intelligence",
                          "data discovery", "data analysis", "edge-data", "complex data", "real-world data", "analyst"]),
    ("Future of Work", ["workforce", "hiring", "recruit", "employee", "productivity", "collaboration", "talent",
                        "workplace", "human resources", " hr ", "learning platform", "customer success", "customer service",
                        "customer support", "presales", " sales ", "onboarding", "workflow", "saas management", "ai assistant",
                        "project management", "partnerships platform", "partnership", "teamwork"]),
    ("Transportation / Mobility", ["mobility", "vehicle", "transport", "autonomous", "fleet", "driving", "aviation",
                                   "aircraft", "electric vehicle", "scooter", " bike", "boat", "watercraft", "rideshar", "travel"]),
    ("Logistics / Supply Chain", ["logistics", "supply chain", "supply and demand", "freight", "warehouse",
                                  "delivery", "procurement", "inventory", "fulfillment", "shipping"]),
    ("PropTech", ["real estate", "property", "housing", "mortgage", "rental", "construction", "tenant"]),
    ("CPG", ["beverage", "snack", "consumer packaged", "beauty", "cosmetic", "apparel", "grocery", "skincare",
             "eyewear", "glasses", "footwear", "pet sitter", "pet "]),
    ("Climate / Sustainability", ["climate", "carbon", "renewable", "solar", "battery", "sustainab", "emission",
                                  "clean energy", "ev charging", "electrif", "energy", "power is produced", "power grid"]),
    ("RegTech/Gov/Legal", ["legal", "compliance", "government", "regulat", "law firm", "attorney", "risk services", "lawsuit"]),
    ("Deeptech / Robotics / AR/VR", ["robot", "hardware", "semiconductor", "chip", "drone", "aerospace",
                                     "augmented reality", "virtual reality", "satellite", "quantum", "sensor", "rfid", "wifi",
                                     "space", "rocket", "launch vehicle"]),
    ("Consumer", ["marketplace", "consumer", "shopping", "social network", "community", "app for", "app that",
                  "ecommerce", "e-commerce", "subscription", "retailer", "universit", "student", "education",
                  "learning", "fashion"]),
]


def get_json(url, params=None):
    last = None
    for attempt in range(1, RETRIES + 1):
        try:
            r = requests.get(url, headers=HEADERS, params=params, timeout=TIMEOUT)
            r.raise_for_status()
            return r.json(), r.headers
        except requests.RequestException as e:  # noqa
            last = e
            wait = 1.5 * attempt
            print(f"  ! request failed ({e}); retry {attempt}/{RETRIES} in {wait:.1f}s", file=sys.stderr)
            time.sleep(wait)
    raise SystemExit(f"FATAL: could not fetch {url}: {last}")


def clean(s):
    if s is None:
        return None
    s = re.sub(r"\s+", " ", unescape(s)).strip()
    return s or None


def strip_html(h):
    if not h:
        return None
    return clean(re.sub(r"<[^>]+>", " ", h))


def website_from_profiles(profiles_html):
    """The `profiles` blob is a tiny HTML fragment whose first link is the
    company's external website. Some hrefs are malformed (e.g. 'http:///www...')."""
    if not profiles_html:
        return None
    m = re.search(r'href="([^"]+)"', profiles_html)
    if not m:
        return None
    url = m.group(1).strip()
    url = re.sub(r"^(https?:)/+", r"\1//", url)  # collapse 'http:///' -> 'http://'
    return url or None


def sectors_from_class_list(class_list):
    out = []
    for cls in class_list or []:
        if cls.startswith("company_industry-"):
            slug = cls[len("company_industry-"):]
            disp = SECTOR_SLUG_DISPLAY.get(slug)
            if disp and disp not in out:
                out.append(disp)
    return out


def everywhere_tags(name, description, sectors):
    """FF sectors first (mapped via SECTOR_TAG_MAP), then keyword fallback on
    name + description to add/refine. Order most->least relevant, cap at 4."""
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


def fetch_all():
    rows = []
    page = 1
    while True:
        data, headers = get_json(API, params={"per_page": 100, "page": page})
        if not data:
            break
        rows.extend(data)
        total_pages = int(headers.get("X-WP-TotalPages", page))
        if page >= total_pages:
            break
        page += 1
        time.sleep(0.5)
    return rows


def main():
    limit = int(sys.argv[sys.argv.index("--limit") + 1]) if "--limit" in sys.argv else None
    raw = fetch_all()
    if limit:
        raw = raw[:limit]

    scraped_at = datetime.now(timezone.utc).isoformat()
    out = []
    for c in raw:
        name = clean(c.get("title", {}).get("rendered"))
        if not name:
            continue
        description = strip_html(c.get("content", {}).get("rendered"))
        founders = []
        for f in c.get("founders") or []:
            fn = clean(f.get("founder_name"))
            if not fn:
                continue
            url = clean(f.get("founder_crunchbase_slug"))  # misnamed: holds the founder's
            if url:                                          # Twitter/LinkedIn/Wikipedia/etc.
                url = re.sub(r"^(https?:)/+", r"\1//", url)   # collapse 'http:///' typos
            entry = {"name": fn, "url": url}
            if entry not in founders:
                founders.append(entry)
        sectors = sectors_from_class_list(c.get("class_list"))
        out.append({
            "company_name": name,
            "description": description,
            "company_url": website_from_profiles(c.get("profiles")),
            "company_profile_url": clean(c.get("link")),
            "logo_url": clean(c.get("featured_image_thumbnail_url")),
            "founders": founders,
            "sectors": sectors,
            "everywhere_tags": everywhere_tags(name, description, sectors),
            "source_url": SOURCE_URL,
            "scraped_at": scraped_at,
        })

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2)

    # ---- summary ----
    n = len(out)
    print(f"wrote {n} companies -> {OUT}")
    for field in ("description", "company_url", "logo_url"):
        miss = sum(1 for r in out if not r[field])
        print(f"  {field:18s} missing: {miss}/{n}")
    print(f"  founders empty:    {sum(1 for r in out if not r['founders'])}/{n}")
    print(f"  sectors empty:     {sum(1 for r in out if not r['sectors'])}/{n}")
    untagged = [r['company_name'] for r in out if not r['everywhere_tags']]
    print(f"  untagged:          {len(untagged)}/{n}" + (f" -> {untagged}" if untagged else ""))
    by_tag = {}
    for r in out:
        for t in r['everywhere_tags']:
            by_tag[t] = by_tag.get(t, 0) + 1
    print("  by everywhere_tag:")
    for t, k in sorted(by_tag.items(), key=lambda x: -x[1]):
        print(f"    {k:3d}  {t}")


if __name__ == "__main__":
    main()
