#!/usr/bin/env python3
"""
Lerer Hippeau portfolio scraper -> lererhippeau_companies.json

Source: https://www.lererhippeau.com/portfolio (Webflow + Finsweet CMS).

The page renders THREE Webflow collection lists:
  1. a 24-item "featured" grid (top of page) -- a strict subset of the full list,
     and its cards carry NO sector sub-list, so it is ignored;
  2. a 1-item "Full Portfolio" heading list -- decorative;
  3. the full alphabetical portfolio, paginated server-side at 64/page via
     `?c33e6893_page=N` (5 pages -> 305 companies).
The third list is the only one with a `.w-pagination-wrapper`, which is how
`portfolio_list()` identifies it (positional indexing would break if Lerer
re-orders the sections). Verified: every featured company also appears in the
full list, so nothing is lost by ignoring list 1.

Per `.portfolio-collection-item`:
  - name        -> the 2nd `.website-url` div inside `a.link-holder`
                   (the card renders "Visit / <Name> / ->"; there is no separate
                   name node -- the logo `alt` attributes are empty)
  - description -> `p.portfolio-details`
  - company_url -> `a.link-holder[href]` (external site; all 305 have one)
  - logo_url    -> `img.potfolio-logo` (sic -- Lerer's own class typo)
  - first_investment_year -> the "SINCE <year>" `.year-holder` block
  - status      -> "Exited" when the card has an `.exit-tag` chip, else "Active"
  - sectors     -> the nested `.sector-collection-wrapper` list
                   (`[fs-cmsfilter-field="sector"]`), 0-3 per company

"Empty != absent" checked: no acquirer / ticker / exit year is encoded anywhere.
The name suffix is always the bare company name (no "(Acquired)"/"(NYSE: X)"
pattern), and the descriptions are one-line "X is a Y" blurbs -- the exit state
lives only in the structured `.exit-tag` chip, which is captured as `status`.
Lerer publishes no founders, HQ or funding-stage anywhere on the page or a
detail page (there are no per-company detail pages), so those columns are
intentionally absent rather than emitted as always-null.

requirements:
    pip install requests beautifulsoup4

usage:
    python3 lererhippeau_scraper.py              # -> ../data/lererhippeau_companies.json
    python3 lererhippeau_scraper.py --limit 20   # quick test run
"""

import json
import os
import re
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup

HOST = "www.lererhippeau.com"
PORTFOLIO_URL = f"https://{HOST}/portfolio"
SOURCE_URL = PORTFOLIO_URL
PAGE_PARAM = "c33e6893_page"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir,
                   "data", "lererhippeau_companies.json")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}
TIMEOUT = 45
RETRIES = 3
SLEEP = 1.0
MAX_PAGES = 40  # guard rail; the list is 5 pages today
LEGACY_WEBFLOW_IPS = ["75.2.70.75", "99.83.190.102"]

# ---- Lerer's own 15 sectors -> the 17-tag everywhere_tags taxonomy -----------
# "AI/ML" is deliberately unmapped: AI alone is not a category (classify by the
# market served). Two Lerer sectors conflate two of our tags ("FinTech, DeFi, &
# Blockchain", "Data & Security"); each maps to the dominant one only and the
# keyword classifier adds the second when the blurb warrants it.
SECTOR_TAG_MAP = {
    "Media & Entertainment": ["Gaming / Media / Entertainment"],
    # Adtech/martech sits in media. This matches hustlefund_scraper.py's
    # "Advertising / Marketing" mapping and enrich.py, which both file
    # "advertis"/"marketing" under Gaming / Media / Entertainment.
    "Marketing Services": ["Gaming / Media / Entertainment"],
    "Commerce": ["Consumer"],
    "Future of Work & Productivity": ["Future of Work"],
    "Food & Beverage": ["CPG"],
    "FinTech, DeFi, & Blockchain": ["FinTech / Insurance"],
    "Healthcare": ["Health"],
    "Hardware & Robotics": ["Deeptech / Robotics / AR/VR"],
    "PropTech": ["PropTech"],
    "Data & Security": ["Data & Analytics"],
    "Supply Chain & Logistics": ["Logistics / Supply Chain"],
    "Wellness & Longevity": ["Health"],
    "Energy Transition & Climate": ["Climate / Sustainability"],
    "Education": ["Consumer"],
}

# everywhere_tags keyword classifier (substrings, lowercased) -- copied verbatim
# from afore_scraper.py / iconiq_scraper.py so tagging stays consistent repo-wide.
KEYWORD_TAGS = [
    ("BioTech", ["biotech", "drug", "therapeut", "oncolog", "cancer", "tumor", "genomic", "genome",
                 "molecul", "antibod", "protein", "vaccine", "clinical-stage", "medicine", "opioid", "life science",
                 "synthetic biology", "biolog", "biomedical"]),
    ("Health", ["healthcare", "health care", "patient", "clinic", "medical", "mental health", "telehealth",
                "health system", "health record", "diagnos", "surgical", "doctor", "hospital", "pharmac", "therapy",
                "health plan", "prior authorization", "health assistant", "health data"]),
    ("Cybersecurity", ["cybersecurity", "security", "secure", "privacy", "fraud", "phishing", "malware",
                       "ransomware", "endpoint", "zero trust", "vulnerab", "authentication", "threat", "defense system", "identity",
                       "information protection", "kyb", "compliance for ai"]),
    ("FinTech / Insurance", ["fintech", "payment", "bank", "lending", "loan", "insurance", "insurtech", "credit", "trading",
                             "wallet", "financ", "invoic", "accounting", "payroll", "treasury", "billing", "pricing platform",
                             "rebate", " tax", "audit", "money management", "robo-advisor", "brokerage", "spend management",
                             "capital markets", "investing", "claims", "coverage plans", "underwriting"]),
    ("Web3 / Crypto", ["crypto", "blockchain", "web3", "token", "on-chain", "ethereum", "bitcoin", "decentral", "stablecoin", "nft"]),
    ("Gaming / Media / Entertainment", ["game", "gaming", "music", "video", "creator", "content", "publish",
                                        "entertain", "newsletter", "podcast", "film", "streaming", "social media", "media platform",
                                        "sports network", "filmmaker", "motion graphics", "audio file"]),
    ("Dev Tools / Cloud", ["developer", " api ", "apis", "api platform", "infrastructure", "database", "cloud",
                           "open source", "devops", "sdk", "kubernetes", "container", "observability", "deploy", "compute",
                           "storage", "serverless", "inference", "networking", "ethernet", "coding", "codebase", "low-code",
                           "no-code", "source code", "development platform", "incident", " sre", "voicemail", "communications",
                           "llm", "foundation model", "interpretability", "code-automation", "event-driven",
                           "log management", "file sharing", "tech stack", "voice agent", "appliance software",
                           "text to speech", "operationalize ai", "notifications for engineering",
                           "spreadsheet", "data importer"]),
    ("Data & Analytics", ["analytics", "business intelligence", "data platform", "data warehouse", "data lake",
                          "data pipeline", "insights", "dashboard", "experimentation", "decision intelligence", "data quality",
                          "analyz", "data curation", "quality management", "relationship intelligence",
                          "data discovery", "data analysis", "edge-data", "complex data", "real-world data", "analyst",
                          "data intelligence", "data transformation", "data integration", "data management", "buyer intent",
                          "curated coding data"]),
    ("Future of Work", ["workforce", "hiring", "recruit", "employee", "productivity", "collaborat", "talent",
                        "workplace", "human resources", " hr ", "learning platform", "customer success", "customer service",
                        "customer support", "presales", " sales ", "onboarding", "workflow", "saas management", "ai assistant",
                        "project management", "partnerships platform", "partnership", "teamwork", "scheduling", "work assistant",
                        "sales engineer", "sales teams", "for managers", "team wiki", "cleaning companies", "call center",
                        "answering service", "coaching", "well-being benefits", "presentation", "email", "inbox", "your notes"]),
    ("Transportation / Mobility", ["mobility", "vehicle", "transport", "autonomous", "fleet", "driving", "aviation",
                                   "aircraft", "electric vehicle", "scooter", " bike", "boat", "watercraft", "rideshar", "travel",
                                   "automotive"]),
    ("Logistics / Supply Chain", ["logistics", "supply chain", "supply and demand", "freight", "warehouse",
                                  "delivery", "procurement", "inventory", "fulfillment", "shipping", "container trucking",
                                  "last-mile", "distribution", "global supply"]),
    ("PropTech", ["real estate", "property", "housing", "mortgage", "rental", "construction", "tenant", "home construction",
                  "renovation", "rent"]),
    ("CPG", ["beverage", "snack", "consumer packaged", "beauty", "cosmetic", "apparel", "grocery", "skincare",
             "eyewear", "glasses", "footwear", "pet sitter", "pet ", "fashion brand", "secondhand"]),
    ("Climate / Sustainability", ["climate", "carbon", "renewable", "solar", "battery", "sustainab", "emission",
                                  "clean energy", "ev charging", "electrif", "energy", "power is produced", "power grid"]),
    ("RegTech/Gov/Legal", ["legal", "compliance", "government", "regulat", "law firm", "attorney", "risk services",
                           "lawsuit", "lawyer", "legal space", "ip protection", "prior authorization"]),
    ("Deeptech / Robotics / AR/VR", ["robot", "hardware", "semiconductor", "chip", "drone", "aerospace",
                                     "augmented reality", "virtual reality", "satellite", "quantum", "sensor", "rfid", "wifi",
                                     "space", "rocket", "launch vehicle", "optics", "defense", "warehouse automation",
                                     "wireless internet", "vertically integrated home"]),
    ("Consumer", ["marketplace", "consumer", "shopping", "social network", "community", "app for", "app that",
                  "ecommerce", "e-commerce", "subscription", "retailer", "universit", "student", "education",
                  "learning", "fashion", "parents", "creators", "fitness", "gig economy", "discounts", "tutor"]),
]

# Lerer-specific keyword supplement, for stragglers whose only sector is the
# unmapped "AI/ML". Each term appears verbatim in a Lerer description and was
# checked against all 305 for false positives (same precedent as
# orbimed_scraper.py / coatue_scraper.py). Note "physical world" was REJECTED as
# a Deeptech cue -- it also matches DataSnap, which is consumer analytics.
KEYWORD_TAGS_EXTRA = {
    "Future of Work": ["frontline"],
    "Dev Tools / Cloud": ["pre-trained transformer"],   # a frontier-model lab; cf.
                                                        # enrich.py tagging Anthropic Dev Tools / Cloud
    "Deeptech / Robotics / AR/VR": ["engineering agi"],
}
KEYWORD_TAGS = [(tag, kws + KEYWORD_TAGS_EXTRA.get(tag, [])) for tag, kws in KEYWORD_TAGS]


def _mk_session():
    s = requests.Session()
    s.headers.update(HEADERS)
    return s


SESSION = _mk_session()


def fetch(url):
    """Fallback chain (see CLAUDE.md network note): (1) direct HTTPS,
    (2) legacy Webflow IPs pinned via SNI, (3) r.jina.ai read-only relay."""
    for attempt in range(1, RETRIES + 1):
        try:
            r = SESSION.get(url, timeout=TIMEOUT)
            r.raise_for_status()
            return r.text
        except requests.RequestException as e:
            print(f"  ! direct failed ({e}); retry {attempt}/{RETRIES}", file=sys.stderr)
            time.sleep(1.5 * attempt)
    parts = urlsplit(url)
    host = parts.netloc
    for ip in LEGACY_WEBFLOW_IPS:
        try:
            pinned = urlunsplit((parts.scheme, ip, parts.path, parts.query, parts.fragment))
            r = SESSION.get(pinned, headers={**HEADERS, "Host": host}, timeout=TIMEOUT, verify=False)
            r.raise_for_status()
            return r.text
        except requests.RequestException as e:
            print(f"  ! legacy-IP {ip} failed ({e})", file=sys.stderr)
    try:
        r = SESSION.get(f"https://r.jina.ai/{url}",
                        headers={**HEADERS, "x-respond-with": "html"}, timeout=TIMEOUT)
        r.raise_for_status()
        return r.text
    except requests.RequestException as e:
        raise SystemExit(f"FATAL: all fetch routes failed for {url}: {e}")


def clean(s):
    if s is None:
        return None
    # Lerer's CMS uses NBSP inside some sector names ("Energy Transition &<nbsp>Climate")
    s = re.sub(r"\s+", " ", s.replace("\xa0", " ")).strip()
    return s or None


def norm_url(u):
    u = clean(u)
    if not u:
        return None
    u = u.replace("http:///", "http://").replace("https:///", "https://")
    if not u.startswith(("http://", "https://")):
        return u
    parts = urlsplit(u)
    q = "&".join(p for p in parts.query.split("&")
                 if p and not re.match(r"(ref|utm_[a-z]+|utm_souce)=", p, re.I))
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path, q, ""))


def everywhere_tags(name, description, sectors):
    """Lerer's own sectors first (mapped via SECTOR_TAG_MAP), then keyword
    fallback on name + description. Order most->least relevant, cap at 4."""
    tags = []
    for sec in sectors:
        for mapped in SECTOR_TAG_MAP.get(sec, []):
            if mapped not in tags:
                tags.append(mapped)
    text = f"{name or ''} {description or ''}".lower()
    # substring-trap guard (see PLAYBOOK): "machine/deep learning" must NOT trip
    # the education "learning"/"learning platform" keywords -> neutralize to "ai".
    text = text.replace("machine learning", "ai").replace("deep learning", "ai")
    for tag, kws in KEYWORD_TAGS:
        if tag in tags:
            continue
        if any(kw in text for kw in kws):
            tags.append(tag)
    return tags[:4]


def portfolio_list(soup):
    """The full alphabetical list is the only collection wrapper that carries a
    Webflow pagination block; the 24-card featured grid and the 1-item heading
    list do not."""
    for w in soup.select(".portfolio-collection-wrapper"):
        if w.select_one(".w-pagination-wrapper"):
            return w
    return None


def parse_item(it, scraped_at):
    link = it.select_one("a.link-holder[href]")
    # The card renders the name as the 2nd of three `.website-url` divs
    # ("Visit" / "<Name>" / "->").
    name = None
    if link:
        parts = [clean(d.get_text()) for d in link.select(".website-url")]
        parts = [p for p in parts if p and p not in ("Visit", "→", "->")]
        name = parts[0] if parts else None
    if not name:
        return None

    desc_el = it.select_one("p.portfolio-details")
    logo_el = it.select_one("img.potfolio-logo") or it.select_one("img.hover-logo")

    year = None
    yh = it.select_one(".year-holder")
    if yh:
        m = re.search(r"\b(19|20)\d{2}\b", yh.get_text(" ", strip=True))
        if m:
            year = int(m.group(0))

    sectors, seen = [], set()
    for p in it.select('[fs-cmsfilter-field="sector"]'):
        s = clean(p.get_text())
        if s and s not in seen:
            seen.add(s)
            sectors.append(s)

    description = clean(desc_el.get_text()) if desc_el else None
    return {
        "company_name": name,
        "description": description,
        "company_url": norm_url(link.get("href")) if link else None,
        "logo_url": norm_url(logo_el.get("src")) if logo_el else None,
        "first_investment_year": year,
        "status": "Exited" if it.select_one(".exit-tag") else "Active",
        "sectors": sectors,
        "everywhere_tags": everywhere_tags(name, description, sectors),
        "source_url": SOURCE_URL,
        "scraped_at": scraped_at,
    }


def scrape(scraped_at, limit=None):
    out, seen = [], set()
    page = 1
    while page <= MAX_PAGES:
        url = PORTFOLIO_URL if page == 1 else f"{PORTFOLIO_URL}?{PAGE_PARAM}={page}"
        soup = BeautifulSoup(fetch(url), "html.parser")
        lst = portfolio_list(soup)
        items = lst.select(":scope > .w-dyn-items > .w-dyn-item") if lst else []
        if not items:
            break
        for it in items:
            rec = parse_item(it, scraped_at)
            if not rec or rec["company_name"] in seen:
                continue
            seen.add(rec["company_name"])
            out.append(rec)
            if limit and len(out) >= limit:
                print(f"  page {page}: reached --limit {limit}")
                return out
        print(f"  page {page}: {len(items)} items ({len(out)} total)")
        if not soup.select_one("a.w-pagination-next"):
            break
        page += 1
        time.sleep(SLEEP)
    return out


def main():
    argv = sys.argv
    limit = int(argv[argv.index("--limit") + 1]) if "--limit" in argv else None
    scraped_at = datetime.now(timezone.utc).isoformat()

    out = scrape(scraped_at, limit)

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2)

    n = len(out)
    print(f"\nwrote {n} companies -> {OUT}")
    for field in ("description", "company_url", "logo_url", "first_investment_year"):
        present = sum(1 for r in out if r[field])
        print(f"  {field}: {present}/{n} present")
    print(f"  sectors: {sum(1 for r in out if r['sectors'])}/{n} present")
    print(f"  status: {dict(Counter(r['status'] for r in out))}")
    untagged = sum(1 for r in out if not r["everywhere_tags"])
    print(f"  everywhere_tags: {n - untagged}/{n} tagged ({untagged} untagged)")
    for tag, cnt in Counter(t for r in out for t in r["everywhere_tags"]).most_common():
        print(f"    {tag}: {cnt}")


if __name__ == "__main__":
    main()
