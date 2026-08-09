#!/usr/bin/env python3
"""
Sequoia Capital portfolio scraper -> sequoia_companies.json

Scrapes Sequoia's portfolio (https://www.sequoiacap.com/our-companies/) into a
JSON file. WordPress + FacetWP. The listing page renders a server-side HTML
<table> (one <tr> per company, class `company-listing__head` etc.) that is
paginated with a normal WP query var, `?_paged=N` (confirmed: 7 pages of 52 +
1 page of 48 = 412 total, alphabetical, no overlap/duplication across pages).

Each row gives: internal post id (`data-toggle="collapse" data-target=
"#company_listing-<id>"`), name, a short description, "Current Stage"
(Pre-Seed/Seed|Early|Growth|Acquired|IPO), an abbreviated partner list, and
"First Partnered" stage+year. The richer per-company data (website, logo,
socials, categories, founders, full partner list, founded/partnered/exit
years, "Why we Partnered") only loads on-demand via an admin-ajax POST
(`action=load_company_content&post_id=<id>&nonce=<nonce>`; the nonce is
embedded in the listing page as `window.vars.nonce` and appears reusable
across companies/pages within a scrape run) -- so this scraper does one
listing-page fetch per page plus one detail fetch per company (~412 + 8
requests).

Empty != absent, checked: Sequoia publishes NO ticker symbol anywhere (listing
descriptions, detail descriptions, or milestones -- grepped for NASDAQ/NYSE
across all 67 IPO'd companies' short + long descriptions: zero hits), so
`ticker_symbol` is left null for all records -- this is a genuine site-wide
omission, not denormalized data we missed. Acquirer IS denormalized in prose,
though only for some: the short/long description often says "now part of X",
"now a part of X", or "(acquired by X)" (confirmed on ~20/37 Acquired
companies on a sampled page; the rest, e.g. Armis, simply don't name a buyer
in the description) -- parsed via regex into `acquirer`. Exit year comes from
the structured Milestones list ("Acquired 2010" / "IPO 2020"), not the name
suffix (Sequoia doesn't suffix names like RRE does).

requirements:
    pip install requests beautifulsoup4

usage:
    python3 sequoia_scraper.py            # writes ../data/sequoia_companies.json
    python3 sequoia_scraper.py --limit 20 # only the first ~20 for a test run
"""

import json
import os
import re
import sys
import time
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

BASE = "https://www.sequoiacap.com/our-companies/"
AJAX_URL = "https://sequoiacap.com/wp-admin/admin-ajax.php"
SOURCE_URL = "https://www.sequoiacap.com/our-companies/"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "data", "sequoia_companies.json")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}
TIMEOUT = 45
RETRIES = 3
SLEEP = 0.5

# Sequoia's own 17 "categories" facet slugs -> display names (from
# window.FWP_JSON.preload_data.facets.categories on the listing page; also
# matches the pill text rendered in each company's detail HTML).
CATEGORY_SLUG_DISPLAY = {
    "ai": "AI",
    "climate": "Climate",
    "consumer": "Consumer",
    "crypto": "Crypto",
    "data-analytics": "Data & Analytics",
    "defense": "Defense",
    "developer-tools": "Developer Tools",
    "fintech": "Fintech",
    "gtm": "GTM",
    "hardware": "Hardware",
    "healthcare": "Healthcare",
    "infrastructure": "Infrastructure",
    "legal": "Legal",
    "marketplace": "Marketplace",
    "operations": "Operations",
    "productivity": "Productivity",
    "security": "Security",
}

# Sequoia categories -> the 17-tag everywhere_tags taxonomy. "AI" and "GTM" /
# "Operations" / "Productivity" are intentionally NOT mapped here: AI alone is
# not a category (classify by market served) and GTM/Operations/Productivity
# don't correspond to a single vertical tag -- left to the keyword fallback.
SECTOR_TAG_MAP = {
    "Climate": ["Climate / Sustainability"],
    "Crypto": ["Web3 / Crypto"],
    "Data & Analytics": ["Data & Analytics"],
    "Defense": ["Deeptech / Robotics / AR/VR"],
    "Developer Tools": ["Dev Tools / Cloud"],
    "Fintech": ["FinTech / Insurance"],
    "Hardware": ["Deeptech / Robotics / AR/VR"],
    "Healthcare": ["Health"],
    "Infrastructure": ["Dev Tools / Cloud"],
    "Legal": ["RegTech/Gov/Legal"],
    "Marketplace": ["Consumer"],
    "Consumer": ["Consumer"],
}

# everywhere_tags keyword classifier (substrings, lowercased) -- copied from
# rre_scraper.py / iconiq_scraper.py.
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
                           "text to speech", "code"]),
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


def post(url, data):
    last = None
    for attempt in range(1, RETRIES + 1):
        try:
            r = requests.post(url, data=data, headers=HEADERS, timeout=TIMEOUT)
            r.raise_for_status()
            return r.text
        except requests.RequestException as e:  # noqa
            last = e
            wait = 1.5 * attempt
            print(f"  ! ajax post failed ({e}); retry {attempt}/{RETRIES} in {wait:.1f}s", file=sys.stderr)
            time.sleep(wait)
    print(f"  ! FAILED to fetch detail (post_id={data.get('post_id')}): {last}", file=sys.stderr)
    return None


def clean(s):
    if s is None:
        return None
    s = re.sub(r"\s+", " ", s).strip()
    return s or None


def to_year(s):
    s = clean(s)
    if s and re.fullmatch(r"\d{4}", s):
        y = int(s)
        if 1900 <= y <= 2100:
            return y
    return None


ACQUIRER_PATTERNS = [
    re.compile(r"now (?:a )?part of ([A-Z][A-Za-z0-9&.\- ]*?)(?:,| is | provides | builds | makes |\.|$)", re.I),
    re.compile(r"\(acquired by ([A-Z][A-Za-z0-9&.\- ]*?)\)", re.I),
    re.compile(r"acquired by ([A-Z][A-Za-z0-9&.\- ]*?)(?:,| in | for |\.|$)", re.I),
]


def derive_acquirer(text):
    if not text:
        return None
    for pat in ACQUIRER_PATTERNS:
        m = pat.search(text)
        if m:
            acquirer = clean(m.group(1))
            if acquirer:
                return acquirer.rstrip(", ")
    return None


def everywhere_tags(name, description, categories):
    """Sequoia categories first (mapped via SECTOR_TAG_MAP), then keyword
    fallback on name + description to add/refine. Order most->least
    relevant, cap at 4."""
    tags = []
    for cat in categories:
        for mapped in SECTOR_TAG_MAP.get(cat, []):
            if mapped not in tags:
                tags.append(mapped)
    text = f"{name or ''} {description or ''}".lower()
    for tag, kws in KEYWORD_TAGS:
        if tag in tags:
            continue
        if any(kw in text for kw in kws):
            tags.append(tag)
    return tags[:4]


def parse_listing_row(tr):
    m = re.search(r"#company_listing-(\d+)", tr.get("data-target") or "")
    post_id = m.group(1) if m else None

    th = tr.select_one("th.company-listing__head")
    name = clean(th.get_text()) if th else None
    if not name or not post_id:
        return None

    tds = tr.find_all("td")
    # tds[0] = hidden post id, tds[1] = short description, tds[2] = current
    # stage, tds[3] = abbreviated partner list, tds[4] = "First Partnered"
    # stage (year), tds[5] = toggle button.
    short_description = clean(tds[1].get_text()) if len(tds) > 1 else None
    current_stage = clean(tds[2].get_text()) if len(tds) > 2 else None
    first_partnered_year = to_year((tds[4].get("data-order") if len(tds) > 4 else None))
    first_partnered_stage = clean(tds[4].get_text()) if len(tds) > 4 else None
    # tds[4] text is like "Early (2018)" -- strip the trailing "(YYYY)"
    if first_partnered_stage:
        first_partnered_stage = clean(re.sub(r"\(\d{4}\)\s*$", "", first_partnered_stage))

    return {
        "post_id": post_id,
        "company_name": name,
        "short_description": short_description,
        "current_stage": current_stage,
        "first_partnered_stage": first_partnered_stage,
        "first_partnered_year": first_partnered_year,
    }


def parse_detail(html, row):
    if not html:
        return None
    soup = BeautifulSoup(html, "html.parser")

    logo_a = soup.select_one("a img.company__logo-image")
    company_url = None
    logo_url = None
    if logo_a:
        logo_url = clean(logo_a.get("src"))
        parent_a = logo_a.find_parent("a")
        if parent_a:
            company_url = clean(parent_a.get("href"))

    desc_el = soup.select_one(".wysiwyg.wysiwyg--fs-lg")
    description = clean(desc_el.get_text()) if desc_el else None

    social_urls = {}
    for a in soup.select(".social-sharing__options a[href]"):
        cls = " ".join(a.get("class") or [])
        m = re.search(r"ico--([a-z0-9\-]+)", cls)
        if m:
            social_urls[m.group(1)] = clean(a.get("href"))

    categories = []
    for pill in soup.select('a[data-bs-toggle="facet"][data-bs-target="categories"]'):
        slug = pill.get("data-bs-value")
        disp = CATEGORY_SLUG_DISPLAY.get(slug) or clean(pill.get_text())
        if disp and disp not in categories:
            categories.append(disp)

    # clist sections keyed by heading text: Milestones, Team (=founders),
    # Partner/Partners, Why we Partnered. Headings vary (singular/plural), so
    # match by normalized title rather than a fixed class.
    founded_year = partnered_year = None
    exit_type = None   # "Acquired" | "IPO" | None
    exit_year = None
    founders = []
    partners = []
    why_we_partnered = None

    for clist in soup.select(".clist"):
        title_el = clist.select_one(".clist__title")
        title = clean(title_el.get_text()) if title_el else ""
        title_norm = (title or "").lower()

        if title_norm == "milestones":
            for li in clist.select(".clist__item"):
                txt = clean(li.get_text())
                if not txt:
                    continue
                mm = re.match(r"(Founded|Partnered|Acquired|IPO)\s+(\d{4})", txt)
                if not mm:
                    continue
                label, year = mm.group(1), int(mm.group(2))
                if label == "Founded":
                    founded_year = year
                elif label == "Partnered":
                    partnered_year = year
                elif label in ("Acquired", "IPO"):
                    exit_type = label
                    exit_year = year
        elif title_norm == "team":
            for li in clist.select(".clist__item"):
                txt = clean(li.get_text())
                if txt:
                    founders.append(txt)
        elif title_norm in ("partner", "partners"):
            for li in clist.select(".clist__item"):
                txt = clean(li.get_text())
                if txt:
                    partners.append(txt)
        elif title_norm == "why we partnered":
            content_el = clist.select_one(".clist__content")
            why_we_partnered = clean(content_el.get_text()) if content_el else None

    # status: derive from current_stage (row) primarily, fall back to
    # exit_type from milestones.
    stage = (row.get("current_stage") or "").strip()
    if stage == "Acquired" or exit_type == "Acquired":
        status = "Acquired"
    elif stage == "IPO" or exit_type == "IPO":
        status = "Public"
    elif stage:
        status = "Active"
    else:
        status = None

    acquirer = derive_acquirer(description) if status == "Acquired" else None

    return {
        "company_url": company_url,
        "logo_url": logo_url,
        "description": description,
        "social_urls": social_urls,
        "categories": categories,
        "founders": founders,
        "partners": partners,
        "why_we_partnered": why_we_partnered,
        "founded_year": founded_year,
        "partnered_year": partnered_year,
        "status": status,
        "exit_year": exit_year,
        "acquirer": acquirer,
    }


def fetch_nonce():
    html = get(BASE)
    m = re.search(r'var vars = \{"nonce":"([a-f0-9]+)"', html)
    nonce = m.group(1) if m else None
    if not nonce:
        raise SystemExit("FATAL: could not find ajax nonce on listing page")
    return html, nonce


def main():
    limit = int(sys.argv[sys.argv.index("--limit") + 1]) if "--limit" in sys.argv else None

    print("Fetching listing page 1 (+ nonce) ...")
    first_html, nonce = fetch_nonce()

    rows = []
    seen_ids = set()

    def collect(html):
        soup = BeautifulSoup(html, "html.parser")
        trs = soup.select('tr[data-toggle="collapse"]')
        n = 0
        for tr in trs:
            rec = parse_listing_row(tr)
            if not rec or rec["post_id"] in seen_ids:
                continue
            seen_ids.add(rec["post_id"])
            rows.append(rec)
            n += 1
        return n

    n = collect(first_html)
    print(f"  page 1: {n} companies (running total {len(rows)})")
    page = 2
    while not (limit and len(rows) >= limit):
        url = f"{BASE}?_paged={page}"
        html = get(url)
        n = collect(html)
        print(f"  page {page}: {n} companies (running total {len(rows)})")
        if n == 0:
            break
        page += 1
        time.sleep(SLEEP)

    if limit:
        rows = rows[:limit]

    print(f"\nListing collected: {len(rows)} companies. Fetching per-company detail ...")

    scraped_at = datetime.now(timezone.utc).isoformat()
    out = []
    for i, row in enumerate(rows, 1):
        detail_html = post(AJAX_URL, {
            "action": "load_company_content",
            "post_id": row["post_id"],
            "nonce": nonce,
        })
        detail = parse_detail(detail_html, row) or {}

        description = detail.get("description") or row["short_description"]
        categories = detail.get("categories") or []

        rec = {
            "company_name": row["company_name"],
            "description": description,
            "company_url": detail.get("company_url"),
            "logo_url": detail.get("logo_url"),
            "social_urls": detail.get("social_urls") or {},
            "categories": categories,
            "founders": detail.get("founders") or [],
            "sequoia_partners": detail.get("partners") or [],
            "why_we_partnered": detail.get("why_we_partnered"),
            "founded_year": detail.get("founded_year"),
            "first_partnered_year": detail.get("partnered_year") or row["first_partnered_year"],
            "first_partnered_stage": row["first_partnered_stage"],
            "status": detail.get("status"),
            "acquirer": detail.get("acquirer"),
            "exit_year": detail.get("exit_year"),
            "ticker_symbol": None,   # Sequoia publishes no ticker anywhere (checked names/descriptions/milestones)
            "everywhere_tags": everywhere_tags(row["company_name"], description, categories),
            "source_url": SOURCE_URL,
            "scraped_at": scraped_at,
        }
        out.append(rec)

        if i % 25 == 0 or i == len(rows):
            print(f"  detail {i}/{len(rows)}: {row['company_name']}")
        time.sleep(SLEEP)

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2)

    # ---- summary ----
    from collections import Counter
    n = len(out)
    print(f"\nWrote {n} companies -> {OUT}")
    by_status = Counter(o["status"] for o in out)
    print("By status:", dict(by_status),
          "| with acquirer:", sum(1 for o in out if o["acquirer"]),
          "| with ticker:", sum(1 for o in out if o["ticker_symbol"]))
    for field in ("description", "company_url", "logo_url", "founded_year", "first_partnered_year", "why_we_partnered"):
        miss = sum(1 for o in out if not o[field])
        print(f"  {field:22s} missing: {miss}/{n}")
    print("  founders empty:", sum(1 for o in out if not o["founders"]))
    print("  sequoia_partners empty:", sum(1 for o in out if not o["sequoia_partners"]))
    print("  categories empty:", sum(1 for o in out if not o["categories"]))
    untagged = [o["company_name"] for o in out if not o["everywhere_tags"]]
    print(f"  untagged: {len(untagged)}/{n}" + (f" -> {untagged}" if untagged else ""))
    by_tag = Counter(t for o in out for t in o["everywhere_tags"])
    print("By everywhere_tag:")
    for t, c in by_tag.most_common():
        print(f"  {c:>4}  {t}")
    by_cat = Counter(c for o in out for c in o["categories"])
    print("By Sequoia category:")
    for t, c in by_cat.most_common():
        print(f"  {c:>4}  {t}")


if __name__ == "__main__":
    main()
