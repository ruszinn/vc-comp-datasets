#!/usr/bin/env python3
"""
Battery Ventures portfolio scraper -> battery_companies.json

Scrapes Battery Ventures' portfolio (https://www.battery.com/company/) into a
JSON file. The site is WordPress + jQuery (not a JS framework): the `/company/`
grid page renders a shell with filter dropdowns (sector/location/stage/status)
whose results are loaded via a WordPress admin-ajax action:

    POST https://www.battery.com/wp-admin/admin-ajax.php
         action=loadbatterycompanieswithFilter&allCompany=1

`allCompany=1` returns ALL matching cards in one response (343 total, no
filters applied) as an HTML fragment: name, logo, location, and a short
status line ("Active" / "Acquired by X" / "NASDAQ: TICK") truncated with
"&hellip;" for long values, plus the `/company/<slug>/` detail-page link.

Each `/company/<slug>/` detail page is then fetched for the fields the grid
truncates or omits: external website + socials (from the hero `<a>` + `.cp-social`
links), description (`.comp-writeup`), Battery's own SECTORS tags, INVESTED
year, full untruncated STATUS text, STAGE, and the Battery deal-team member
names (`.comp-team-row .part-name`).

Battery ALSO publishes a separate, plainer static page
(`/list-of-all-companies/`, 679 legal-entity names with an
"* Denotes an exited investment" footnote) that is a superset going back
decades, but it carries no logo/website/description/sector/stage -- so this
scraper uses the `/company/` grid + detail pages as the primary (richer)
source. `status` here is Battery's own raw STATUS field text (not derived from
a name suffix): "Active", "Acquired by <X>", "<Exchange>: <TICKER>", "Merged
with <X>", etc. -- `acquirer`/`ticker_symbol` are parsed out of it, matching
the "empty != absent" mining approach used for RRE's name-suffix status.

Politeness workaround for this machine: cdn.webflow.com's current IP is
unreachable from here, but battery.com is *not* Webflow (WordPress on its own
infra) and connects fine over normal DNS -- no IP pin needed for this scraper.

requirements:
    pip install requests

usage:
    python3 battery_scraper.py            # writes ../data/battery_companies.json
    python3 battery_scraper.py --limit 20 # only the first ~20 for a test run
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

BASE = "https://www.battery.com"
AJAX_URL = f"{BASE}/wp-admin/admin-ajax.php"
GRID_SOURCE_URL = f"{BASE}/company/"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "data", "battery_companies.json")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}
TIMEOUT = 45
RETRIES = 5
SLEEP = 1.5  # between detail-page fetches

# Battery's own SECTOR taxonomy (from the /company/ filter dropdown) -> the
# 17-tag everywhere_tags taxonomy. "AI-Powered Apps" and "Data/AI" are
# intentionally NOT mapped (AI alone is not a category); "Industry Specific"
# and "Tech-Enabled Services" are too vague to map -- all four are left to the
# keyword classifier to place by the market the company actually serves.
SECTOR_TAG_MAP = {
    "Healthcare IT": "Health",
    "Financial Tech": "FinTech / Insurance",
    "Security": "Cybersecurity",
    "DevOps and Dev Tools": "Dev Tools / Cloud",
    "Infrastructure Software": "Dev Tools / Cloud",
    "Consumer": "Consumer",
    "Sales / Marketing": "Future of Work",
    "Collaboration & Productivity": "Future of Work",
    "Industrial Tech + Life Science Tools": "Deeptech / Robotics / AR/VR",
}

# everywhere_tags keyword classifier (substrings, lowercased) -- copied from
# rre_scraper.py / foundersfund_scraper.py. Refines Battery's coarse sectors
# (esp. "Application Software", "AI-Powered Apps", "Industry Specific") from
# name + description.
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
                           "log management", "file sharing", "tech stack", "voice agent", "appliance software", "semiconductor",
                           "chip"]),
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
    ("Deeptech / Robotics / AR/VR", ["robot", "hardware", "drone", "aerospace",
                                     "augmented reality", "virtual reality", "satellite", "quantum", "sensor", "rfid", "wifi",
                                     "space", "rocket", "launch vehicle"]),
    ("Consumer", ["marketplace", "consumer", "shopping", "social network", "community", "app for", "app that",
                  "ecommerce", "e-commerce", "subscription", "retailer", "universit", "student", "education",
                  "learning", "fashion"]),
]


def request_with_retries(method, url, **kwargs):
    last = None
    for attempt in range(1, RETRIES + 1):
        try:
            r = requests.request(method, url, headers=HEADERS, timeout=TIMEOUT, **kwargs)
            if r.status_code == 429:
                raise requests.HTTPError(f"429 Too Many Requests for url: {url}", response=r)
            r.raise_for_status()
            return r
        except requests.RequestException as e:  # noqa
            last = e
            wait = 5 * attempt
            print(f"  ! request failed ({e}); retry {attempt}/{RETRIES} in {wait:.1f}s", file=sys.stderr)
            time.sleep(wait)
    raise SystemExit(f"FATAL: could not fetch {url}: {last}")


def clean(s):
    if s is None:
        return None
    s = unescape(re.sub(r"\s+", " ", s)).strip()
    return s or None


def to_year(s):
    s = clean(s)
    if s and re.fullmatch(r"\d{4}", s) and 1900 <= int(s) <= 2100:
        return int(s)
    return None


def fetch_grid():
    """Single admin-ajax call with allCompany=1 returns every portfolio card in
    one HTML fragment (343 companies, no pagination needed)."""
    data = {
        "action": "loadbatterycompanieswithFilter",
        "pagination": 0,
        "sector": 0,
        "location": 0,
        "stage": 0,
        "status": 0,
        "docpage": 1,
        "allCompany": 1,
        "companySearch": "",
    }
    r = request_with_retries("POST", AJAX_URL, data=data)
    payload = r.json()
    html = payload.get("htmls", "")
    cards = re.findall(
        r'<a href="([^"]+)" class="inv-logo-card comp w-inline-block">\s*'
        r'<img src="([^"]+)" alt="([^"]*)" title="([^"]*)" class="inv-logo">\s*'
        r'<div class="logo-meta">\s*<div class="text-block-16">([^<]*)<br/>([^<]*)</div>',
        html,
    )
    rows = []
    seen = set()
    for profile_url, logo_url, alt, title, location, status_line in cards:
        name = clean(title) or clean(alt)
        if not name or profile_url in seen:
            continue
        seen.add(profile_url)
        rows.append({
            "company_profile_url": clean(profile_url),
            "logo_url": clean(logo_url),
            "company_name": name,
            "location_grid": clean(location),
            "status_grid": clean(status_line),
        })
    return rows, payload.get("cntpgs")


STATUS_ROW_RE = re.compile(
    r'<div class="comp-det-sub">([^<]+)</div>\s*<div>\s*(.*?)\s*</div>', re.S
)


def parse_detail(html, profile_url):
    out = {
        "description": None,
        "company_url": None,
        "social_urls": [],
        "sectors": [],
        "invested_year": None,
        "status": None,
        "location": None,
        "stage": None,
        "partners": [],
    }

    hero = re.search(r'<div class="comp-hero">(.*?)<div id="Insights"', html, re.S)
    hero_html = hero.group(1) if hero else ""

    web = re.search(r'<div class="company-detail-logo alt">\s*<a href="([^"]+)"', hero_html)
    if web:
        out["company_url"] = clean(web.group(1))

    writeup = re.search(r'<div class="comp-writeup">(.*?)</div>', hero_html, re.S)
    if writeup:
        text = re.sub(r"<[^>]+>", "", writeup.group(1))
        out["description"] = clean(text)

    socials = re.findall(r'class="cp-social">(.*?)</div>', hero_html, re.S)
    if socials:
        out["social_urls"] = sorted(set(re.findall(r'href="([^"]+)"', socials[0])))

    info = re.search(r'<div class="comp-info">(.*?)<footer class="hp3-footer">', html, re.S)
    info_html = info.group(1) if info else ""

    for label, value in STATUS_ROW_RE.findall(info_html):
        label = clean(label)
        value = clean(re.sub(r"<[^>]+>", "", value))
        if not label:
            continue
        key = label.lower()
        if key == "sectors":
            out["sectors"] = [clean(s) for s in re.split(r",\s*", value or "") if clean(s)]
        elif key == "invested":
            out["invested_year"] = to_year(value)
        elif key == "status":
            out["status"] = value
        elif key == "location":
            out["location"] = value
        elif key == "stage":
            out["stage"] = value

    out["partners"] = [clean(m) for m in re.findall(r'<div class="part-name">([^<]+)</div>', html)]
    out["partners"] = [p for p in out["partners"] if p]

    return out


ACQUIRER_RE = re.compile(r"(?:Acquired|Aquired)\s+by\s+(.+?)(?:;.*)?$", re.I)
MERGED_RE = re.compile(r"Merged with\s+(.+?)(?:;.*)?$", re.I)
SOLD_RE = re.compile(r"Sold to\s+(.+?)(?:;.*)?$", re.I)
STAKE_RE = re.compile(r"(?:Majority )?stake sold to\s+(.+?)(?:;.*)?$", re.I)
TICKER_RE = re.compile(r"\b([A-Z]{2,6}(?:/[A-Z]{2,6})?)\s*:\s*([A-Z][A-Z0-9.]{0,9})\b")
TRAILING_YEAR_RE = re.compile(r"^(.*?)\s+in\s+(\d{4})$", re.I)


def derive_exit_fields(status):
    """Battery's own STATUS field (not a name suffix) already states the exit
    fact in prose -- parse acquirer/ticker/exit_year straight out of it, same
    spirit as the RRE name-suffix mining but applied to a structured field here."""
    acquirer = None
    ticker_symbol = None
    exit_year = None
    if not status:
        return acquirer, ticker_symbol, exit_year
    m = TICKER_RE.search(status)
    if m:
        ticker_symbol = f"{m.group(1)}: {m.group(2)}"
    for rx in (ACQUIRER_RE, MERGED_RE, SOLD_RE, STAKE_RE):
        m = rx.search(status)
        if m:
            acquirer = clean(m.group(1))
            break
    if acquirer:
        ym = TRAILING_YEAR_RE.match(acquirer)
        if ym:
            acquirer = clean(ym.group(1))
            exit_year = int(ym.group(2))
    return acquirer, ticker_symbol, exit_year


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


def main():
    limit = None
    if "--limit" in sys.argv:
        limit = int(sys.argv[sys.argv.index("--limit") + 1])

    print("Fetching portfolio grid (admin-ajax, allCompany=1) ...")
    rows, reported_total = fetch_grid()
    print(f"  grid returned {len(rows)} companies (site reports cntpgs={reported_total})")

    if limit:
        rows = rows[:limit]

    scraped_at = datetime.now(timezone.utc).isoformat()
    companies = []
    for i, row in enumerate(rows, 1):
        url = row["company_profile_url"]
        print(f"[{i}/{len(rows)}] {row['company_name']} -> {url}")
        r = request_with_retries("GET", url)
        detail = parse_detail(r.text, url)

        status = detail["status"] or row["status_grid"]
        acquirer, ticker_symbol, exit_year = derive_exit_fields(status)
        location = detail["location"] or row["location_grid"]
        sectors = detail["sectors"]

        companies.append({
            "company_name": row["company_name"],
            "description": detail["description"],
            "company_url": detail["company_url"],
            "company_profile_url": url,
            "logo_url": row["logo_url"],
            "location": location,
            "sectors": sectors,
            "stage": detail["stage"],
            "invested_year": detail["invested_year"],
            "status": status,
            "acquirer": acquirer,
            "ticker_symbol": ticker_symbol,
            "exit_year": exit_year,
            "partners": detail["partners"],
            "social_urls": detail["social_urls"],
            "everywhere_tags": everywhere_tags(row["company_name"], detail["description"], sectors),
            "source_url": GRID_SOURCE_URL,
            "scraped_at": scraped_at,
        })
        time.sleep(SLEEP)

    companies.sort(key=lambda o: o["company_name"].lower())

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(companies, f, ensure_ascii=False, indent=2)

    by_sector = Counter(s for o in companies for s in o["sectors"])
    by_tag = Counter(t for o in companies for t in o["everywhere_tags"])
    by_stage = Counter(o["stage"] for o in companies if o["stage"])
    n = len(companies)
    print(f"\nWrote {n} companies -> {OUT}")
    print("With website:", sum(1 for o in companies if o["company_url"]),
          "| with description:", sum(1 for o in companies if o["description"]),
          "| with sectors:", sum(1 for o in companies if o["sectors"]),
          "| with location:", sum(1 for o in companies if o["location"]),
          "| with invested_year:", sum(1 for o in companies if o["invested_year"]),
          "| with stage:", sum(1 for o in companies if o["stage"]))
    print("With acquirer:", sum(1 for o in companies if o["acquirer"]),
          "| with ticker:", sum(1 for o in companies if o["ticker_symbol"]),
          "| with exit_year:", sum(1 for o in companies if o["exit_year"]),
          "| with partners:", sum(1 for o in companies if o["partners"]),
          "| with social_urls:", sum(1 for o in companies if o["social_urls"]),
          "| untagged:", sum(1 for o in companies if not o["everywhere_tags"]))
    print("By stage:", dict(by_stage))
    print("By Battery sector:")
    for t, c in by_sector.most_common():
        print(f"  {c:>4}  {t}")
    print("By everywhere_tag:")
    for t, c in by_tag.most_common():
        print(f"  {c:>4}  {t}")


if __name__ == "__main__":
    main()
