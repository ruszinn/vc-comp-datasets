#!/usr/bin/env python3
"""
Hustle Fund portfolio scraper -> hustlefund_companies.json

Source: https://www.hustlefund.vc/founders (Webflow + Finsweet CMS).

Hustle Fund has NO /portfolio path (it 404s) and the portfolio page is absent
from sitemap.xml -- the company list lives on the "founders" page, inside a
`.founder-list-wrapper` Finsweet collection list that is paginated server-side
at 9 items/page via `?5d649f4d_page=N` (~40 pages). The big logo collages higher
up that page (`.portfolio-image*`) are decorative marketing images, not the data.

Per `.company-info`:
  - company_name -> `.company-title`
  - company_url  -> `a.company-link-with-arrow[href]` (the company's own site;
                    Hustle Fund publishes no per-company detail page)
  - region       -> the desktop `.text-block-25[fs-cmsfilter-field="location"]`
                    chip: the COARSE bucket the filter dropdown offers
                    (US / Asia / Africa / Europe / Canada / Australia /
                    Latin America / Remote)
  - location     -> the unattributed `.filter-label` next to it: the FINER value
                    ("US - Bay Area", "Asia - Philippines"). The mobile duplicate
                    block confusingly tags this finer value as
                    `fs-cmsfilter-field="location"` while the desktop block tags
                    the coarse one, so both are captured under distinct names and
                    the desktop block is parsed to avoid double-counting.
  - sectors      -> `[fs-cmsfilter-field="sectors"]` (one per company)
  - funds        -> `[fs-cmsfilter-field="funds"]` ("fund I" / "fund II")

Every card is rendered twice per company (a desktop layout plus a
`.company-info-mobile` duplicate); this parser reads only the non-mobile nodes.

"Empty != absent" checked: Hustle Fund publishes NO description, founder names,
founded year, funding stage or exit/status anywhere on the card, and there is no
detail page to crawl -- so those columns are omitted rather than emitted as
always-null. Nothing is denormalized into the company names (no "(Acquired)" /
"(NASDAQ: X)" suffixes). Because there is no description, `everywhere_tags` is
derived from the company's single sector plus its name only, which is coarser
than for firms that publish blurbs.

requirements:
    pip install requests beautifulsoup4

usage:
    python3 hustlefund_scraper.py              # -> ../data/hustlefund_companies.json
    python3 hustlefund_scraper.py --limit 20   # quick test run
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

HOST = "www.hustlefund.vc"
FOUNDERS_URL = f"https://{HOST}/founders"
SOURCE_URL = FOUNDERS_URL
PAGE_PARAM = "5d649f4d_page"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir,
                   "data", "hustlefund_companies.json")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}
TIMEOUT = 45
RETRIES = 3
SLEEP = 0.8
MAX_PAGES = 120  # guard rail; the list is ~40 pages at 9/page today
LEGACY_WEBFLOW_IPS = ["75.2.70.75", "99.83.190.102"]

# ---- Hustle Fund's own 31 sectors -> the 17-tag everywhere_tags set ----------
# Harvested from the live list (see the module docstring); the labels are
# slash-joined multi-concept buckets and a handful of companies carry two,
# comma-joined, in the one cell -- `split_sectors()` splits on the comma (no
# individual label contains one).
# Deliberately unmapped, left to the keyword classifier: "Data / Analytics / AI /
# ML" maps to Data & Analytics only (AI alone is not a category); "Manufacturing"
# has no clean equivalent among the 17; "General / Industry Agnostic" and
# "Personal & Professional Services" are explicitly not sectors.
SECTOR_TAG_MAP = {
    "Advertising / Marketing": ["Gaming / Media / Entertainment"],  # adtech sits in media
    "AR / VR / Machine Vision": ["Deeptech / Robotics / AR/VR"],
    "Arts / Entertainment / Media / Sports & Gaming": ["Gaming / Media / Entertainment"],
    "Blockchain / Crypto / NFT / Web 3.0": ["Web3 / Crypto"],
    "Communication / Collaboration / Productivity": ["Future of Work"],
    "Construction / Materials": ["PropTech"],
    "Data / Analytics / AI / ML": ["Data & Analytics"],
    "Development Tools & Infrastructure": ["Dev Tools / Cloud"],
    "Education / Personal & Professional Development": ["Consumer"],
    "Finance - Banking / Payments / Lending": ["FinTech / Insurance"],
    "Finance - Insurance": ["FinTech / Insurance"],
    "Finance - Investing": ["FinTech / Insurance"],
    "Finance - Other": ["FinTech / Insurance"],
    "Food / Agriculture": ["CPG"],
    "HR / Hiring / Employment": ["Future of Work"],
    "Health & Wellness": ["Health"],
    "Health / Fitness / Wellness": ["Health"],
    "Legal / Government / Regulation": ["RegTech/Gov/Legal"],
    "Logistics / Shipping": ["Logistics / Supply Chain"],
    "Mobility / Transportation": ["Transportation / Mobility"],
    "Pets / Animals": ["Consumer"],
    "Real Estate / Housing": ["PropTech"],
    "Retail / E-commerce": ["Consumer"],
    "Robotics": ["Deeptech / Robotics / AR/VR"],
    "Sales / Operations / Customer Service": ["Future of Work"],
    "Security / Cybersecurity": ["Cybersecurity"],
    "Social Media / Networking": ["Gaming / Media / Entertainment", "Consumer"],
    "Travel / Hospitality": ["Consumer"],
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
    s = re.sub(r"\s+", " ", s.replace("\xa0", " ")).strip()
    return s or None


def norm_url(u):
    u = clean(u)
    if not u or u == "#":
        return None
    u = u.replace("http:///", "http://").replace("https:///", "https://")
    if not u.startswith(("http://", "https://")):
        return u
    parts = urlsplit(u)
    q = "&".join(p for p in parts.query.split("&")
                 if p and not re.match(r"(ref|utm_[a-z]+|utm_souce)=", p, re.I))
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path, q, ""))


def split_sectors(raw):
    """A few companies carry two sectors comma-joined in the one CMS cell
    ("Finance - Other,Blockchain / Crypto / NFT / Web 3.0"). No individual
    Hustle Fund sector label contains a comma, so the split is unambiguous."""
    out = []
    for part in (clean(raw) or "").split(","):
        part = clean(part)
        if part:
            out.append(part)
    return out


def everywhere_tags(name, sectors):
    """Hustle Fund's own sector first (mapped via SECTOR_TAG_MAP), then a keyword
    fallback over the name + the raw sector label (there is no description on the
    card). Order most->least relevant, cap at 4."""
    tags = []
    for sec in sectors:
        for mapped in SECTOR_TAG_MAP.get(sec, []):
            if mapped not in tags:
                tags.append(mapped)
    text = f"{name or ''} {' '.join(sectors)}".lower()
    # substring-trap guard (see PLAYBOOK): "machine/deep learning" must NOT trip
    # the education "learning"/"learning platform" keywords -> neutralize to "ai".
    text = text.replace("machine learning", "ai").replace("deep learning", "ai")
    for tag, kws in KEYWORD_TAGS:
        if tag in tags:
            continue
        if any(kw in text for kw in kws):
            tags.append(tag)
    return tags[:4]


def parse_item(it, scraped_at):
    name_el = it.select_one(".company-title")
    name = clean(name_el.get_text()) if name_el else None
    if not name:
        return None

    # Read the DESKTOP block only -- `.company-info-mobile` repeats every value.
    mobile = it.select_one(".company-info-mobile")
    if mobile:
        mobile.decompose()

    region_el = it.select_one('[fs-cmsfilter-field="location"]')
    region = clean(region_el.get_text()) if region_el else None

    # The finer location sits in the one `.filter-label` that carries no
    # fs-cmsfilter-field attribute (Hustle Fund never tagged it for filtering).
    location = None
    for lab in it.select(".filter-label"):
        if not lab.has_attr("fs-cmsfilter-field"):
            location = clean(lab.get_text())
            break

    sectors, seen = [], set()
    for el in it.select('[fs-cmsfilter-field="sectors"]'):
        for s in split_sectors(el.get_text()):
            if s not in seen:
                seen.add(s)
                sectors.append(s)

    funds, seenf = [], set()
    for el in it.select('[fs-cmsfilter-field="funds"]'):
        f = clean(el.get_text())
        if f and f not in seenf:
            seenf.add(f)
            funds.append(f)

    link = it.select_one("a.company-link-with-arrow[href]")

    return {
        "company_name": name,
        "company_url": norm_url(link.get("href")) if link else None,
        "region": region,
        "location": location,
        "sectors": sectors,
        "funds": funds,
        "everywhere_tags": everywhere_tags(name, sectors),
        "source_url": SOURCE_URL,
        "scraped_at": scraped_at,
    }


def scrape(scraped_at, limit=None):
    out, seen = [], set()
    page = 1
    while page <= MAX_PAGES:
        url = FOUNDERS_URL if page == 1 else f"{FOUNDERS_URL}?{PAGE_PARAM}={page}"
        soup = BeautifulSoup(fetch(url), "html.parser")
        items = soup.select(".company-info")
        if not items:
            print(f"  page {page}: empty -> stop")
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
        if page % 5 == 0 or page == 1:
            print(f"  page {page}: {len(items)} items ({len(out)} total)")
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
    for field in ("company_url", "region", "location"):
        present = sum(1 for r in out if r[field])
        print(f"  {field}: {present}/{n} present")
    print(f"  sectors: {sum(1 for r in out if r['sectors'])}/{n} present")
    print(f"  funds: {dict(Counter(f for r in out for f in r['funds']))}")
    print(f"  regions: {dict(Counter(r['region'] for r in out))}")
    untagged = sum(1 for r in out if not r["everywhere_tags"])
    print(f"  everywhere_tags: {n - untagged}/{n} tagged ({untagged} untagged)")
    for tag, cnt in Counter(t for r in out for t in r["everywhere_tags"]).most_common():
        print(f"    {tag}: {cnt}")
    unmapped = Counter(s for r in out for s in r["sectors"] if s not in SECTOR_TAG_MAP)
    if unmapped:
        print(f"  sectors NOT in SECTOR_TAG_MAP: {dict(unmapped)}")


if __name__ == "__main__":
    main()
