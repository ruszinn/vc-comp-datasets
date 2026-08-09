#!/usr/bin/env python3
"""
Venrock portfolio scraper -> venrock_companies.json

Scrapes Venrock's portfolio (https://www.venrock.com/companies) into a JSON
file. The site's `/companies` page is a client-side-rendered React app (WP
Engine-hosted WordPress backing a `venrock-2022` theme bundle) with no
server-rendered HTML, but the underlying data is the standard WordPress REST
API with a custom `investment` post type -- no auth, no per-company crawling:

    GET /wp-json/wp/v2/investment?per_page=100&page=N&_embed=1   (250 companies, 3 pages)

Each record is self-contained: `title`/`content` give name/description, ACF
fields carry website, first-investment stage, funding year, investment status
(Private/Acquired/Public/Merged), stock exchange + ticker, acquirer
(`acquired_by`), merger partner (`merged_with`), and a `company_team_members`
list of founder {name, url} objects (present for ~30%). `class_list` entries
prefixed `sector-` are Venrock's own multi-value sector taxonomy (`_embed=1`
also returns the same terms resolved under `wp:term`, but class_list is
lighter and doesn't need extra parsing). `_embed=1` additionally inlines the
featured-image logo URL so no per-company media fetch is needed.

`acf.venrock_team` references a small set of partner post IDs; those are
resolved via a single bulk fetch of `/wp-json/wp/v2/teammember` (55 records,
1 page) into a name lookup -- a handful of referenced IDs 404 (deleted alumni
posts) and are simply omitted, not invented.

"Empty != absent" checked: exit state (status/acquirer/ticker/merged-with) is
already a first-class structured ACF field here (unlike RRE/Founders Fund,
where it had to be mined from name suffixes or description prose) -- spot
checked against well-known historic Venrock exits (Apple/NASDAQ:AAPL,
Intel/NASDAQ:INTC, Illumina/NASDAQ:ILMN, DoubleClick/acquired by Google,
athenahealth/NASDAQ:ATHN) and all matched, so no additional name/description
mining was needed for those fields.

requirements:
    pip install requests

usage:
    python3 venrock_scraper.py            # writes ../data/venrock_companies.json
    python3 venrock_scraper.py --limit 20 # only the first ~20 for a test run
"""

import json
import os
import re
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from html import unescape

import requests

API = "https://www.venrock.com/wp-json/wp/v2/investment"
TEAM_API = "https://www.venrock.com/wp-json/wp/v2/teammember"
SOURCE_URL = "https://www.venrock.com/companies"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "data", "venrock_companies.json")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}
TIMEOUT = 45
RETRIES = 3

# Venrock's own `sector-<slug>` taxonomy (from class_list) -> the 17-tag
# everywhere_tags taxonomy. "technology", "ai", "saas", and "other" are
# intentionally NOT mapped here (AI alone is not a category; "Technology"/
# "SaaS"/"Other" are too generic to place a single company) -- left to the
# keyword classifier to place by the market served.
SECTOR_TAG_MAP = {
    "therapeutics": ["BioTech"],
    "life-science-tools": ["BioTech"],
    "health-tech": ["Health"],
    "medical-devices": ["Health"],
    "venture-healthcare": ["Health"],
    "public-healthcare": ["Health"],
    "veterinary": ["Health"],
    "cybersecurity": ["Cybersecurity"],
    "finance-payments": ["FinTech / Insurance"],
    "legaltech": ["RegTech/Gov/Legal"],
    "crypto": ["Web3 / Crypto"],
    "developerinfrastructure": ["Dev Tools / Cloud"],
    "computing-semi": ["Deeptech / Robotics / AR/VR"],
    "devices-new-technologies": ["Deeptech / Robotics / AR/VR"],
    "defense-aerospace": ["Deeptech / Robotics / AR/VR"],
    "vehicle-technology": ["Transportation / Mobility"],
    "sustainability": ["Climate / Sustainability"],
    "biofuels": ["Climate / Sustainability"],
    "energy": ["Climate / Sustainability"],
    "distributed-electricity": ["Climate / Sustainability"],
    "consumer": ["Consumer"],
}

# everywhere_tags keyword classifier (substrings, lowercased) -- copied from
# rre_scraper.py / foundersfund_scraper.py. Refines Venrock's coarse sectors
# (esp. "Technology"/"AI"/"SaaS"/"Other") from name + description.
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
                             "capital markets", "investing", "wealth management"]),
    ("Web3 / Crypto", ["crypto", "blockchain", "web3", "token", "on-chain", "ethereum", "bitcoin", "decentral", "stablecoin", "nft"]),
    ("Gaming / Media / Entertainment", ["game", "gaming", "music", "video", "creator", "content", "publish",
                                        "entertain", "newsletter", "podcast", "film", "streaming", "social media", "media platform"]),
    ("Dev Tools / Cloud", ["developer", " api ", "apis", "api platform", "infrastructure", "database", "cloud",
                           "open source", "open-source", "devops", "sdk", "kubernetes", "container", "observability", "deploy", "compute",
                           "storage", "serverless", "inference", "networking", "ethernet", "coding", "codebase", "code base",
                           "code-base", "low-code", "no-code", "source code", "development platform", "incident", " sre",
                           "voicemail", "communications", "llm", "foundation model", "interpretability", "microprocessor",
                           "semiconductor components", "log management", "file sharing", "tech stack", "voice agent",
                           "appliance software", "code bases"]),
    ("Data & Analytics", ["analytics", "business intelligence", "data platform", "data warehouse", "data lake",
                          "data pipeline", "insights", "dashboard", "experimentation", "decision intelligence", "data quality",
                          "analyz", "data curation", "quality management", "relationship intelligence",
                          "data discovery", "data analysis", "edge-data", "complex data", "real-world data", "sequencing"]),
    ("Future of Work", ["workforce", "hiring", "recruit", "employee", "productivity", "collaboration", "talent",
                        "workplace", "human resources", " hr ", "learning platform", "customer success", "customer service",
                        "customer support", "presales", " sales ", "onboarding", "workflow", "saas management", "ai assistant",
                        "project management", "partnerships platform", "partnership", "practice management"]),
    ("Transportation / Mobility", ["mobility", "vehicle", "transport", "autonomous", "fleet", "driving", "aviation",
                                   "aircraft", "electric vehicle", "scooter", " bike", "boat", "watercraft", "rideshar", "travel"]),
    ("Logistics / Supply Chain", ["logistics", "supply chain", "supply and demand", "freight", "warehouse",
                                  "delivery", "procurement", "inventory", "fulfillment", "shipping"]),
    ("PropTech", ["real estate", "property", "housing", "mortgage", "rental", "construction", "tenant"]),
    ("CPG", ["beverage", "snack", "consumer packaged", "beauty", "cosmetic", "apparel", "grocery", "skincare",
             "eyewear", "glasses", "footwear", "wine", "spirits"]),
    ("Climate / Sustainability", ["climate", "carbon", "renewable", "solar", "battery", "sustainab", "emission",
                                  "clean energy", "ev charging", "electrif", "energy", "power is produced", "power grid",
                                  "biofuel"]),
    ("RegTech/Gov/Legal", ["legal", "compliance", "government", "regulat", "law firm", "attorney", "risk services", "litigation"]),
    ("Deeptech / Robotics / AR/VR", ["robot", "hardware", "semiconductor", "chip", "drone", "aerospace",
                                     "augmented reality", "virtual reality", "satellite", "quantum", "sensor", "rfid", "wifi",
                                     "space", "rocket", "launch vehicle"]),
    ("Consumer", ["marketplace", "consumer", "shopping", "social network", "community", "app for", "app that",
                  "ecommerce", "e-commerce", "subscription", "retailer", "universit", "student", "education",
                  "learning", "fashion", "smarthome", "smart home"]),
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
    if s is None or isinstance(s, bool):
        return None
    s = re.sub(r"\s+", " ", unescape(str(s))).strip()
    return s or None


def strip_html(h):
    if not h:
        return None
    return clean(re.sub(r"<[^>]+>", " ", h))


def to_year(v):
    if isinstance(v, bool) or v is None:
        return None
    try:
        y = int(v)
    except (TypeError, ValueError):
        return None
    return y if 1900 <= y <= 2100 else None


def sectors_from_class_list(class_list):
    out = []
    for cls in class_list or []:
        if cls.startswith("sector-"):
            slug = cls[len("sector-"):]
            disp = SECTOR_SLUG_DISPLAY.get(slug)
            if disp and disp not in out:
                out.append(disp)
    return out


SECTOR_SLUG_DISPLAY = {
    "health-tech": "Health Tech",
    "ai": "AI",
    "biofuels": "Biofuels",
    "energy": "Energy",
    "devices-new-technologies": "Devices & New Technologies",
    "venture-healthcare": "Venture Healthcare",
    "other": "Other",
    "life-science-tools": "Life Science Tools",
    "vehicle-technology": "Vehicle Technology",
    "computing-semi": "Computing & Semi",
    "distributed-electricity": "Distributed Electricity",
    "medical-devices": "Medical Devices",
    "technology": "Technology",
    "public-healthcare": "Public Healthcare",
    "therapeutics": "Therapeutics",
    "consumer": "Consumer",
    "crypto": "Crypto",
    "veterinary": "Veterinary",
    "cybersecurity": "Cybersecurity",
    "defense-aerospace": "Defense & Aerospace",
    "developerinfrastructure": "Developer Infrastructure",
    "finance-payments": "FinTech",
    "legaltech": "LegalTech",
    "saas": "SaaS",
    "sustainability": "Sustainability",
}


def everywhere_tags(name, description, sectors):
    """Venrock sectors first (mapped via SECTOR_TAG_MAP, using the lowercased
    slug), then keyword fallback on name + description to add/refine. Order
    most->least relevant, cap at 4."""
    tags = []
    slug_by_display = {v: k for k, v in SECTOR_SLUG_DISPLAY.items()}
    for sec in sectors:
        slug = slug_by_display.get(sec)
        for mapped in SECTOR_TAG_MAP.get(slug, []):
            if mapped not in tags:
                tags.append(mapped)
    text = f"{name or ''} {description or ''}".lower()
    for tag, kws in KEYWORD_TAGS:
        if tag in tags:
            continue
        if any(kw in text for kw in kws):
            tags.append(tag)
    return tags[:4]


def fetch_all_investments(limit=None):
    rows = []
    page = 1
    while True:
        data, headers = get_json(API, params={"per_page": 100, "page": page, "_embed": 1})
        if not data:
            break
        rows.extend(data)
        total_pages = int(headers.get("X-WP-TotalPages", page))
        print(f"  fetched investment page {page}/{total_pages} ({len(data)} rows)")
        if limit and len(rows) >= limit:
            break
        if page >= total_pages:
            break
        page += 1
        time.sleep(0.5)
    return rows


def fetch_team_lookup():
    """Bulk-fetch Venrock's teammember post type (55 records, 1 page) into an
    id -> display name lookup, used to resolve acf.venrock_team references.
    Some referenced ids point to deleted/alumni posts no longer in this list
    (404 if fetched individually) -- those are simply left unresolved, not
    invented."""
    lookup = {}
    page = 1
    while True:
        data, headers = get_json(TEAM_API, params={"per_page": 100, "page": page})
        if not data:
            break
        for m in data:
            nm = clean(m.get("title", {}).get("rendered"))
            if nm:
                lookup[m["id"]] = nm
        total_pages = int(headers.get("X-WP-TotalPages", page))
        if page >= total_pages:
            break
        page += 1
        time.sleep(0.5)
    return lookup


def main():
    limit = int(sys.argv[sys.argv.index("--limit") + 1]) if "--limit" in sys.argv else None

    print("Fetching Venrock team members (for partner-name lookup)...")
    team_lookup = fetch_team_lookup()

    print("Fetching Venrock investments...")
    raw = fetch_all_investments(limit=limit)
    if limit:
        raw = raw[:limit]

    scraped_at = datetime.now(timezone.utc).isoformat()
    out = []
    for c in raw:
        name = clean(c.get("title", {}).get("rendered"))
        if not name:
            continue
        description = strip_html(c.get("content", {}).get("rendered"))
        acf = c.get("acf") or {}

        logo_url = None
        embedded = c.get("_embedded") or {}
        media = embedded.get("wp:featuredmedia") or []
        if media and isinstance(media, list):
            logo_url = clean(media[0].get("source_url"))

        founders = []
        for f in acf.get("company_team_members") or []:
            fn = clean(f.get("name"))
            if not fn:
                continue
            url = clean(f.get("url"))
            entry = {"name": fn, "url": url}
            if entry not in founders:
                founders.append(entry)

        partners = []
        for t in acf.get("venrock_team") or []:
            tid = t.get("team_member")
            pn = team_lookup.get(tid)
            if pn and pn not in partners:
                partners.append(pn)

        sectors = sectors_from_class_list(c.get("class_list"))

        status = clean(acf.get("investment_status")) or None

        out.append({
            "company_name": name,
            "description": description,
            "company_url": clean(acf.get("website")),
            "company_profile_url": clean(c.get("link")),
            "logo_url": logo_url,
            "founders": founders,
            "venrock_partners": partners,
            "sectors": sectors,
            "first_invested_stage": clean(acf.get("first_invested")) if clean(acf.get("first_invested")) != "Unknown" else None,
            "year_funded": to_year(acf.get("year_funded")),
            "status": status,
            "stock_exchange": clean(acf.get("stock_exchange")),
            "ticker_symbol": clean(acf.get("ticker_symbol")),
            "acquirer": clean(acf.get("acquired_by")),
            "merged_with": clean(acf.get("merged_with")),
            "social_urls": {
                k: v for k, v in {
                    "twitter": clean(acf.get("twitter_feed")),
                    "facebook": clean(acf.get("facebook_profile")),
                    "linkedin": clean(acf.get("linkedin_url")),
                }.items() if v
            },
            "everywhere_tags": everywhere_tags(name, description, sectors),
            "source_url": SOURCE_URL,
            "scraped_at": scraped_at,
        })

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2)

    # ---- summary ----
    n = len(out)
    print(f"\nWrote {n} companies -> {OUT}")
    for field in ("description", "company_url", "logo_url", "first_invested_stage", "year_funded"):
        miss = sum(1 for r in out if not r[field])
        print(f"  {field:22s} missing: {miss}/{n}")
    print(f"  founders empty:        {sum(1 for r in out if not r['founders'])}/{n}")
    print(f"  venrock_partners empty:{sum(1 for r in out if not r['venrock_partners']):>4}/{n}")
    print(f"  sectors empty:         {sum(1 for r in out if not r['sectors'])}/{n}")
    by_status = Counter(r["status"] for r in out)
    print("  by status:", dict(by_status))
    print(f"  with ticker:           {sum(1 for r in out if r['ticker_symbol'])}/{n}")
    print(f"  with acquirer:         {sum(1 for r in out if r['acquirer'])}/{n}")
    print(f"  with merged_with:      {sum(1 for r in out if r['merged_with'])}/{n}")
    untagged = [r["company_name"] for r in out if not r["everywhere_tags"]]
    print(f"  untagged:              {len(untagged)}/{n}" + (f" -> {untagged}" if untagged else ""))
    by_tag = Counter(t for r in out for t in r["everywhere_tags"])
    print("  by everywhere_tag:")
    for t, k in by_tag.most_common():
        print(f"    {k:3d}  {t}")


if __name__ == "__main__":
    main()
