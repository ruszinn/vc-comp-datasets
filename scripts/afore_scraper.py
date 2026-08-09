#!/usr/bin/env python3
"""
Afore Capital portfolio scraper -> afore_companies.json

Scrapes Afore Capital's portfolio (https://www.afore.vc/portfolio) into a JSON
file. The site is a Webflow build with a client-side-filtered Finsweet CMS list;
the full company grid is server-rendered in the one page (filters just hide/show
DOM items, so every company is present in the static HTML). Each company card
exposes: name, a one-line description, the external company website (the card's
outbound link), and Afore's own single sector/category tag (SaaS, Consumer,
FinTech, AI, Insurtech, Dev Tools, Hardware, Healthcare, Marketplace, Security,
Gaming, B2B, API, Web3, ...). Afore is a dedicated pre-seed fund, so every card
also renders a "Pre-Seed" entry-stage chip plus follow-on investor logos; the
entry stage is therefore uniform ("Pre-Seed" for all) and is NOT emitted as a
per-company column (it is a fund-level constant, not company-specific data), and
the follow-on logos are third-party marks, not Afore's data, so they are omitted
too. Afore does not publish founders, founded year, or HQ on the card or a
detail page, so those fields are intentionally absent rather than emitted as
always-null columns.

"Empty != absent" checked: no status/exit/acquirer/ticker is encoded in the
company names or descriptions (a few descriptions mention "acquisition" in the
customer-acquisition sense -- false positives), so the schema omits those too.

*** NETWORK CAVEAT (2026-07) ***
www.afore.vc is a Webflow site whose current CDN IP (198.202.211.1) is
UNROUTABLE from the machine this scraper was built on -- the same Webflow-CDN
condition documented in CLAUDE.md, and on this build host the legacy-IP pin
(75.2.70.75) and the r.jina.ai relay were unreachable too (tight egress
allowlist). This scraper's fetch() therefore tries, in order: (1) direct HTTPS,
(2) the legacy Webflow IP pinned via SNI, (3) the read-only relay r.jina.ai.
On a healthy network it uses the direct route unchanged and re-derives the
dataset from the live HTML via parse_cards(). Because the build host could reach
NONE of those routes, the shipped afore_companies.json for this initial build
was transcribed from afore.vc's own portfolio page via a read-only fetch relay
(the firm's own content, one hop removed -- spot-checked, no third-party
enrichment) and written through this module's tagging logic (build_from_rows()).
Re-run on a healthy network to refresh straight from the source HTML; if the
Finsweet card class names below differ from the live markup, adjust the
selectors in parse_cards() (they follow the standard Finsweet/Webflow pattern
used by the 8vc/iconiq/rre scrapers but could not be verified against live HTML
from this build host).

requirements:
    pip install requests beautifulsoup4

usage:
    python3 afore_scraper.py                 # writes ../data/afore_companies.json
    python3 afore_scraper.py --limit 15       # only the first ~15 for a test run
    python3 afore_scraper.py --from-rows F     # build from a pipe-delimited cache
                                               # (NAME | DESC | URL | SECTOR), used
                                               # for the relay-transcribed build
"""

import json
import requests
import os
import re
import sys
import time
from urllib.parse import urlsplit, urlunsplit
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

HOST = "www.afore.vc"
PORTFOLIO_URL = f"https://{HOST}/portfolio"
SOURCE_URL = PORTFOLIO_URL
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "data", "afore_companies.json")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}
TIMEOUT = 45
RETRIES = 3
LEGACY_WEBFLOW_IPS = ["75.2.70.75", "99.83.190.102"]

# ---- Afore's own sector tags -> the 17-tag everywhere_tags taxonomy ----------
# Only the unambiguous ones are mapped. "AI", "SaaS", "B2B" and "Marketplace" are
# intentionally left to the keyword classifier: AI alone is not a category
# (classify by the market served); SaaS/B2B span every vertical; a marketplace's
# tag depends on what it trades. This mirrors the iconiq/rre handling.
SECTOR_TAG_MAP = {
    "FinTech": ["FinTech / Insurance"],
    "Insurtech": ["FinTech / Insurance"],
    "Healthcare": ["Health"],
    "Security": ["Cybersecurity"],
    "Dev Tools": ["Dev Tools / Cloud"],
    "API": ["Dev Tools / Cloud"],
    "Web3": ["Web3 / Crypto"],
    "Gaming": ["Gaming / Media / Entertainment"],
    "Hardware": ["Deeptech / Robotics / AR/VR"],
    "Consumer": ["Consumer"],
}

# everywhere_tags keyword classifier (substrings, lowercased) -- copied verbatim
# from iconiq_scraper.py / menlo_scraper.py so tagging stays consistent repo-wide.
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


def fetch(url):
    """Fallback chain: (1) direct HTTPS, (2) legacy Webflow IPs pinned via SNI,
    (3) r.jina.ai read-only relay. Returns HTML text or raises SystemExit."""
    sess = _mk_session()
    # (1) direct
    for attempt in range(1, RETRIES + 1):
        try:
            r = sess.get(url, timeout=TIMEOUT)
            r.raise_for_status()
            return r.text
        except requests.RequestException as e:
            print(f"  ! direct failed ({e}); retry {attempt}/{RETRIES}", file=sys.stderr)
            time.sleep(1.5 * attempt)
    # (2) legacy IP pins (force the connection IP, keep SNI/Host = real host)
    parts = urlsplit(url)
    host = parts.netloc
    for ip in LEGACY_WEBFLOW_IPS:
        try:
            from requests.adapters import HTTPAdapter
            from urllib3.util import connection as urllib3_conn  # noqa
            pinned = urlunsplit((parts.scheme, ip, parts.path, parts.query, parts.fragment))
            r = sess.get(pinned, headers={**HEADERS, "Host": host}, timeout=TIMEOUT, verify=False)
            r.raise_for_status()
            return r.text
        except requests.RequestException as e:
            print(f"  ! legacy-IP {ip} failed ({e})", file=sys.stderr)
    # (3) r.jina.ai relay (returns raw HTML with the x-respond-with header)
    try:
        relay = f"https://r.jina.ai/{url}"
        r = sess.get(relay, headers={**HEADERS, "x-respond-with": "html"}, timeout=TIMEOUT)
        r.raise_for_status()
        return r.text
    except requests.RequestException as e:
        raise SystemExit(f"FATAL: all fetch routes failed for {url}: {e}")


def clean(s):
    if s is None:
        return None
    s = re.sub(r"\s+", " ", s).strip()
    return s or None


def norm_url(u):
    u = clean(u)
    if not u:
        return None
    u = u.replace("http:///", "http://").replace("https:///", "https://")
    if u.startswith("http://") or u.startswith("https://"):
        # lowercase the scheme+host only; strip afore-ref / utm tracking params
        parts = urlsplit(u)
        netloc = parts.netloc.lower()
        q = "&".join(p for p in parts.query.split("&")
                     if p and not re.match(r"(ref|utm_[a-z]+|utm_souce)=", p, re.I))
        return urlunsplit((parts.scheme.lower(), netloc, parts.path, q, ""))
    return u


def everywhere_tags(name, description, sector):
    """Afore's own sector first (mapped via SECTOR_TAG_MAP), then keyword fallback
    on name + description. Order most->least relevant, cap at 4."""
    tags = []
    for mapped in SECTOR_TAG_MAP.get(sector or "", []):
        if mapped not in tags:
            tags.append(mapped)
    text = f"{name or ''} {description or ''}".lower()
    # substring-trap guard (see PLAYBOOK): "machine/deep learning" must NOT trip the
    # education "learning"/"learning platform" keywords -> neutralize to "ai".
    text = text.replace("machine learning", "ai").replace("deep learning", "ai")
    for tag, kws in KEYWORD_TAGS:
        if tag in tags:
            continue
        if any(kw in text for kw in kws):
            tags.append(tag)
    return tags[:4]


def _record(name, description, company_url, sector, scraped_at):
    name = clean(name)
    description = clean(description)
    company_url = norm_url(company_url)
    sector = clean(sector)
    return {
        "company_name": name,
        "description": description,
        "company_url": company_url,
        "sectors": [sector] if sector else [],
        "everywhere_tags": everywhere_tags(name, description, sector),
        "source_url": SOURCE_URL,
        "scraped_at": scraped_at,
    }


def parse_cards(html, scraped_at):
    """Healthy-network path: parse the Finsweet CMS grid straight from live HTML.
    Selectors follow the standard Finsweet/Webflow `.w-dyn-item` card pattern
    (see NETWORK CAVEAT -- not verifiable against live markup from the build host,
    so tuned defensively: name = card heading, url = first outbound <a>, sector =
    a filter-field chip, description = the remaining text line)."""
    soup = BeautifulSoup(html, "html.parser")
    items = soup.select(".w-dyn-item")
    out, seen = [], set()
    for it in items:
        heading = it.select_one("h1, h2, h3, h4, .portfolio_name, [fs-cmsfilter-field='name']")
        name = clean(heading.get_text()) if heading else None
        if not name or name in seen:
            continue
        a = None
        for cand in it.select("a[href]"):
            href = cand.get("href", "")
            if href.startswith("http") and HOST not in href and "afore.vc" not in href:
                a = cand
                break
        company_url = a.get("href") if a else None
        sec_el = it.select_one("[fs-cmsfilter-field='sector'], [fs-cmsfilter-field='category'], .portfolio_tag")
        sector = clean(sec_el.get_text()) if sec_el else None
        desc_el = it.select_one(".portfolio_description, .text-size-medium, p")
        description = clean(desc_el.get_text()) if desc_el else None
        seen.add(name)
        out.append(_record(name, description, company_url, sector, scraped_at))
    return out


def build_from_rows(path, scraped_at):
    """Relay-transcribed build path: read a pipe-delimited cache of the firm's own
    page rows (NAME | DESCRIPTION | URL | SECTOR) and emit identical schema."""
    out, seen = [], set()
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            cells = [c.strip() for c in line.split("|")]
            cells += [""] * (4 - len(cells))
            name, desc, url, sector = cells[0], cells[1], cells[2], cells[3]
            if not name or name in seen:
                continue
            seen.add(name)
            out.append(_record(name, desc, url, sector, scraped_at))
    return out


def main():
    argv = sys.argv
    limit = int(argv[argv.index("--limit") + 1]) if "--limit" in argv else None
    scraped_at = datetime.now(timezone.utc).isoformat()

    if "--from-rows" in argv:
        rows_path = argv[argv.index("--from-rows") + 1]
        out = build_from_rows(rows_path, scraped_at)
    else:
        out = parse_cards(fetch(PORTFOLIO_URL), scraped_at)

    if limit:
        out = out[:limit]

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2)

    # ---- summary ----
    n = len(out)
    print(f"wrote {n} companies -> {OUT}")
    for field in ("description", "company_url"):
        miss = sum(1 for r in out if not r[field])
        print(f"  {field}: {n - miss}/{n} present")
    nosector = sum(1 for r in out if not r["sectors"])
    print(f"  sectors: {n - nosector}/{n} present")
    untagged = sum(1 for r in out if not r["everywhere_tags"])
    print(f"  everywhere_tags: {n - untagged}/{n} tagged ({untagged} untagged)")
    from collections import Counter
    c = Counter(t for r in out for t in r["everywhere_tags"])
    for tag, cnt in c.most_common():
        print(f"    {tag}: {cnt}")


if __name__ == "__main__":
    main()
