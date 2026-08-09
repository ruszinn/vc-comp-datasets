#!/usr/bin/env python3
"""
Index Ventures portfolio scraper -> index_companies.json

Scrapes Index Ventures' portfolio (https://www.indexventures.com/companies/)
into a JSON file. The list page is a Django/Wagtail-style site that renders
the FULL portfolio server-side in one static page -- no API, no pagination,
no lazy-load. Each company is one `<li class="companies__relationships__list__item
js-company">`:
  - `data-sectors`  -> JSON list of sector slugs (13 seen: aiml, business-applications,
    data, entertainment, fintech, future-of-work, healthcare, media, mobility,
    open-source, retail, security, talent) -- mirrors the `<select name="sector">`
    filter dropdown, so slugs are mapped to display names via SECTOR_SLUG_DISPLAY.
  - `data-regions`  -> JSON list of region slugs (africa/asia/australasia/europe/
    latam/north-america); often `[]` (128/311) -- legitimately unpublished, not missing.
  - `data-backed`   -> curation flags (`select`/`seed`/`all-seed`), a portfolio-page
    grouping (e.g. "Backed at Seed"), NOT an exit/status field -- not carried into
    the schema (would be a curation artifact, not a fact about the company).
  - a `<span class="ticker-symbol">` note, present for 23/311 companies. Checked via
    the "Empty != absent" rule: this ONE span is overloaded by Index for three
    different things (verified against each company's own detail page and
    description -- none contain "acquired"/"IPO" prose, so this span is the only
    place the fact lives):
      * a real public ticker           "NASDAQ: DIBS", "AMS:ADYEN", "LON: ROO"
      * an acquirer's name (no prefix) "Netlify" (Gatsby), "Discord" (Ubiquity6)
      * a rename marker                "f.k.a. illicopro" (Matera)
    Kept verbatim as `ticker_or_note`, plus a light regex classification into
    `status` in {"Public", "Acquired/Renamed", None} -- we do NOT invent a
    ticker_symbol/acquirer split since the site itself doesn't structure it that way.

The list page carries no founders, no website, no description, no logo -- those
live only on each company's own detail page `/companies/<slug>/`, so we crawl all
311 of them. Detail page schema (`.company-description__col` blocks), verified
across several companies with 1/2/3 founders and 1/2 Index partners:
  - Founders/CEOs      -> founder-list li's
  - Index Team         -> one or more `/team/<slug>/` links (Index's own partners)
  - Sector / Sectors    -> one or more sector display-name links (kept as a
    cross-check against the list page's slugs; in practice they always agree)
  - Website            -> the external company site (a.company-description__link--external)
The hero also repeats the ticker/description; description ("tagline") comes ONLY
from the detail page (`page-hero__subhead--tagline`), never the list page.

No founded-year, no HQ/location, no funding-stage field exists on the detail page
(checked several companies) -- intentionally omitted, not invented. The only
per-company image is a large editorial/office photo (`og:image`), and 6/16
sampled companies fall back to Index's generic OG placeholder
(`index_ventures_og_image...`) when no such photo exists -- not a real logo, so
no image/logo field is included at all (would be misleading for placeholder cases).

requirements:
    pip install requests beautifulsoup4

usage:
    python3 index_scraper.py            # writes ../data/index_companies.json
    python3 index_scraper.py --limit 15 # only the first ~15 for a test run
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

BASE = "https://www.indexventures.com"
LIST_URL = f"{BASE}/companies/"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "data", "index_companies.json")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}
TIMEOUT = 45
RETRIES = 3
SLEEP = 0.6

# Index's own sector-filter slugs -> display names (from the `<select name="sector">`
# dropdown on the list page; also matches the display names used on detail pages).
SECTOR_SLUG_DISPLAY = {
    "aiml": "AI/ML",
    "business-applications": "Business Applications",
    "data": "Data",
    "entertainment": "Entertainment",
    "fintech": "Fintech",
    "future-of-work": "Future Of Work",
    "healthcare": "Healthcare",
    "media": "Media",
    "mobility": "Mobility",
    "open-source": "Open Source",
    "retail": "Retail",
    "security": "Security",
    "talent": "Talent",
}
REGION_SLUG_DISPLAY = {
    "africa": "Africa",
    "asia": "Asia",
    "australasia": "Australasia",
    "europe": "Europe",
    "latam": "Latin America",
    "north-america": "North America",
}

# Index sectors -> the 17-tag everywhere_tags taxonomy. "AI/ML", "Business
# Applications", "Data", and "Open Source" are intentionally NOT mapped here:
# AI alone is not a category (classify by market served) and the others span
# multiple of the 17 tags -- all four are left to the keyword fallback below.
SECTOR_TAG_MAP = {
    "Healthcare": ["Health"],
    "Fintech": ["FinTech / Insurance"],
    "Security": ["Cybersecurity"],
    "Retail": ["Consumer"],
    "Mobility": ["Transportation / Mobility"],
    "Entertainment": ["Gaming / Media / Entertainment"],
    "Media": ["Gaming / Media / Entertainment"],
    "Talent": ["Future of Work"],
    "Future Of Work": ["Future of Work"],
}

# everywhere_tags keyword classifier (substrings, lowercased) -- copied from
# menlo_scraper.py / iconiq_scraper.py.
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
                           "text to speech", "generative ai", "language ai", "language model", "design platform",
                           "issue tracking", "shared knowledge", "headless cms", "simulating", "physics-driven",
                           "pcb layout", "data-efficiency", "ai research lab", "data authority", "data governance",
                           "data management", "data backup", "data table", "iceberg"]),
    ("Data & Analytics", ["analytics", "business intelligence", "data platform", "data warehouse", "data lake",
                          "data pipeline", "insights", "dashboard", "experimentation", "decision intelligence", "data quality",
                          "analyz", "data curation", "quality management", "relationship intelligence",
                          "data discovery", "data analysis", "edge-data", "complex data", "real-world data", "analyst",
                          "data intelligence", "data transformation", "data integration"]),
    ("Future of Work", ["workforce", "hiring", "recruit", "employee", "productivity", "collaborat", "talent",
                        "workplace", "human resources", " hr ", "learning platform", "customer success", "customer service",
                        "customer support", "presales", " sales ", "onboarding", "workflow", "saas management", "ai assistant",
                        "project management", "partnerships platform", "partnership", "teamwork", "scheduling", "work assistant",
                        "job matching", "job seekers", "applicant", "recruiter"]),
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
                           "lawsuit", "lawyer", "public safety", "first responder"]),
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
    print(f"  ! giving up on {url}: {last}", file=sys.stderr)
    return None


def clean(s):
    if s is None:
        return None
    s = re.sub(r"\s+", " ", s).strip()
    return s or None


def classify_note(note):
    """Index overloads the one ticker-symbol span for three different facts
    (see module docstring). Return a coarse `status` without inventing a split
    the site doesn't structure: "Public" for an EXCHANGE: TICK pattern,
    "Acquired/Renamed" for anything else present (acquirer name or "f.k.a."),
    else None."""
    if not note:
        return None
    if re.match(r"^[A-Za-z.]{2,6}\s*:\s*[A-Za-z0-9.\-]{1,10}$", note):
        return "Public"
    return "Acquired/Renamed"


def everywhere_tags(name, description, sectors):
    """Index sectors first (mapped via SECTOR_TAG_MAP), then keyword fallback on
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


def parse_list_item(li):
    a = li.select_one("a.companies__relationships__list__item__link")
    if not a:
        return None
    href = a.get("href")
    slug = href.strip("/").split("/")[-1] if href else None
    if not slug:
        return None

    ticker_span = a.select_one("span.ticker-symbol")
    ticker_or_note = clean(ticker_span.get_text()) if ticker_span else None

    raw_name = a.get_text()
    if ticker_span:
        raw_name = raw_name.replace(ticker_span.get_text(), "")
    name = clean(raw_name)
    if not name:
        return None

    sector_slugs = []
    m = re.search(r"data-sectors='(.*?)'", str(li))
    if m:
        try:
            sector_slugs = json.loads(m.group(1).replace("&quot;", '"'))
        except (ValueError, TypeError):
            sector_slugs = []
    sectors = [SECTOR_SLUG_DISPLAY.get(s, s) for s in sector_slugs]

    region_slugs = []
    dr = li.get("data-regions")
    if dr:
        try:
            region_slugs = json.loads(dr)
        except (ValueError, TypeError):
            region_slugs = []
    regions = [REGION_SLUG_DISPLAY.get(r, r) for r in region_slugs]

    return {
        "slug": slug,
        "company_name": name,
        "sectors": sectors,
        "regions": regions,
        "ticker_or_note": ticker_or_note,
        "detail_url": f"{BASE}/companies/{slug}/",
    }


def parse_detail(html, base_rec):
    soup = BeautifulSoup(html, "html.parser")

    subhead = soup.select_one(".page-hero__subhead--tagline")
    description = clean(subhead.get_text()) if subhead else None

    founders = []
    for li in soup.select("ul.founder-list li.founder-list__item"):
        v = clean(li.get_text())
        if v:
            founders.append(v)

    index_team = []
    company_url = None
    detail_sectors = []
    for col in soup.select(".company-description__col"):
        header = col.select_one(".company-description__header")
        if not header:
            continue
        label = clean(header.get_text()) or ""
        if label.startswith("Index Team"):
            for a in col.select("a.company-description__link"):
                v = clean(a.get_text())
                if v:
                    index_team.append(v)
        elif label.startswith("Sector"):
            for a in col.select("a.company-description__link"):
                v = clean(a.get_text())
                if v and v not in detail_sectors:
                    detail_sectors.append(v)
        elif label.startswith("Website"):
            a = col.select_one("a.company-description__link--external")
            if a and a.get("href"):
                company_url = clean(a.get("href"))

    # Prefer the list-page slug-derived sectors (stable, machine-mapped); fall
    # back to the detail-page display-name sectors only if the list page had none.
    sectors = base_rec["sectors"] if base_rec["sectors"] else detail_sectors

    status = classify_note(base_rec["ticker_or_note"])

    return {
        "company_name": base_rec["company_name"],
        "description": description,
        "company_url": company_url,
        "sectors": sectors,
        "regions": base_rec["regions"],
        "founders": founders,
        "index_team": index_team,
        "ticker_or_note": base_rec["ticker_or_note"],
        "status": status,
        "everywhere_tags": everywhere_tags(base_rec["company_name"], description, sectors),
        "source_url": base_rec["detail_url"],
    }


def main():
    limit = int(sys.argv[sys.argv.index("--limit") + 1]) if "--limit" in sys.argv else None

    print(f"Fetching list page -> {LIST_URL}")
    list_html = get(LIST_URL)
    if not list_html:
        raise SystemExit("FATAL: could not fetch the companies list page")
    soup = BeautifulSoup(list_html, "html.parser")
    lis = soup.select("li.companies__relationships__list__item.js-company")
    print(f"  found {len(lis)} companies in the list page")

    base_recs = []
    seen = set()
    for li in lis:
        rec = parse_list_item(li)
        if not rec or rec["slug"] in seen:
            continue
        seen.add(rec["slug"])
        base_recs.append(rec)
    if limit:
        base_recs = base_recs[:limit]

    scraped_at = datetime.now(timezone.utc).isoformat()
    out = []
    for i, base in enumerate(base_recs, 1):
        print(f"[{i}/{len(base_recs)}] {base['company_name']} -> {base['detail_url']}")
        html = get(base["detail_url"])
        if html is None:
            # detail fetch failed after retries -- keep what the list page gave us,
            # never fabricate the missing detail-only fields
            rec = {
                "company_name": base["company_name"],
                "description": None,
                "company_url": None,
                "sectors": base["sectors"],
                "regions": base["regions"],
                "founders": [],
                "index_team": [],
                "ticker_or_note": base["ticker_or_note"],
                "status": classify_note(base["ticker_or_note"]),
                "everywhere_tags": everywhere_tags(base["company_name"], None, base["sectors"]),
                "source_url": base["detail_url"],
            }
        else:
            rec = parse_detail(html, base)
        rec["scraped_at"] = scraped_at
        out.append(rec)
        time.sleep(SLEEP)

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2)

    # ---- summary ----
    n = len(out)
    print(f"\nWrote {n} companies -> {OUT}")
    for field in ("description", "company_url"):
        miss = sum(1 for r in out if not r[field])
        print(f"  {field:15s} missing: {miss}/{n}")
    print(f"  sectors empty:     {sum(1 for r in out if not r['sectors'])}/{n}")
    print(f"  regions empty:     {sum(1 for r in out if not r['regions'])}/{n}")
    print(f"  founders empty:    {sum(1 for r in out if not r['founders'])}/{n}")
    print(f"  index_team empty:  {sum(1 for r in out if not r['index_team'])}/{n}")
    print(f"  ticker_or_note:    {sum(1 for r in out if r['ticker_or_note'])}/{n}")
    print(f"  status Public:     {sum(1 for r in out if r['status'] == 'Public')}/{n}")
    print(f"  status Acquired/Renamed: {sum(1 for r in out if r['status'] == 'Acquired/Renamed')}/{n}")
    untagged = [r["company_name"] for r in out if not r["everywhere_tags"]]
    print(f"  untagged:          {len(untagged)}/{n}" + (f" -> {untagged}" if untagged else ""))
    by_tag = Counter(t for r in out for t in r["everywhere_tags"])
    print("  by everywhere_tag:")
    for t, k in by_tag.most_common():
        print(f"    {k:3d}  {t}")
    by_sector = Counter(s for r in out for s in r["sectors"])
    print("  by sector:")
    for t, k in by_sector.most_common():
        print(f"    {k:3d}  {t}")


if __name__ == "__main__":
    main()
