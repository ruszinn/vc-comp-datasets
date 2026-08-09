#!/usr/bin/env python3
"""
2048 Ventures portfolio scraper -> 2048_companies.json

Source: https://www.2048.vc/companies (Webflow + Jetboost filtering).

The whole portfolio is server-rendered in the one static page -- 75
`.card-investment` items in a single Webflow collection list, no pagination and
no API (the Jetboost dropdowns filter client-side over the already-rendered
DOM). `https://www.2048.vc/portfolio` 404s; `/companies` is the real path.

Per `.card-investment`:
  - company_name / company_url -> `a.heading-h3.link-companies` (text / href;
    the href is the company's own site, not a 2048 detail page -- 2048 has none)
  - description   -> the `<p>` inside `.companies-description`
  - team          -> `.partner-content` blocks: `.partner-name` + `.partner-title`
                     + `.partner-avatar` photo. Webflow pads every card to three
                     slots, so blanks are marked `w-condition-invisible` /
                     `w-dyn-bind-empty` and are skipped. All 75 have >=1 person.
                     These are the COMPANY's founders/execs (CEO, Co-Founder,
                     CTO, ...), not 2048's investment team.
  - stage         -> the chip whose icon is `...filter-fund.webp`
  - location      -> the chip whose icon is `...filter-location.webp`
                     (the two chips share the class `.button-outline-invested`,
                     so the icon filename is the only way to tell them apart)
  - latest_news   -> `.companies-body` richtext ("X raises $Y Series Z led by W")
                     plus the article link it wraps; empty on 51/75 cards
                     (`w-dyn-bind-empty`). The linked article is a third-party
                     press URL that 2048 itself publishes on the card, so it is
                     retained as a source-published link (see CLAUDE.md).
  - why_we_invested_url -> `a.button-invested` (a 2048.vc blog post); Webflow
                     renders it as `w-condition-invisible href="#"` on the 34
                     companies that have no post.

Stage is the site's only status signal: its vocabulary is
Pre-Seed / Seed / Series A / Series B / Growth / **Exited**, i.e. "Exited" is a
value of the same field rather than a separate column, so it is kept verbatim in
`stage` rather than being split into a fabricated `status` for the other 74
(the site never asserts they are active). Casing/spacing is inconsistent at
source ("Pre-seed", "Seed+", "Seed +") and is preserved verbatim.

"Empty != absent" checked: 2048 exposes no per-company sector or investment year
in the markup -- the Jetboost sector ("Vertical AI", "Deep Tech", "Health",
"Bio", "Other") and year (2019-2026) dropdowns filter through Jetboost's own
service via the opaque `.jetboost-list-item` slug inputs, which carry only the
company slug and no values. Those columns are therefore omitted rather than
emitted as always-null, and `everywhere_tags` is derived from name + description
alone. No acquirer / ticker / exit year is encoded in the names or descriptions.

requirements:
    pip install requests beautifulsoup4

usage:
    python3 2048_scraper.py              # -> ../data/2048_companies.json
    python3 2048_scraper.py --limit 10   # quick test run
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

HOST = "www.2048.vc"
COMPANIES_URL = f"https://{HOST}/companies"
SOURCE_URL = COMPANIES_URL
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir,
                   "data", "2048_companies.json")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}
TIMEOUT = 45
RETRIES = 3
LEGACY_WEBFLOW_IPS = ["75.2.70.75", "99.83.190.102"]

# everywhere_tags keyword classifier (substrings, lowercased) -- copied verbatim
# from afore_scraper.py / iconiq_scraper.py so tagging stays consistent repo-wide.
# 2048 publishes no per-company sectors, so this is the only tagging signal.
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

# 2048-specific keyword supplement. 2048 publishes no sectors AND its blurbs are
# one terse line each, so the shared list alone left 23/75 companies untagged.
# Every term below was added because it appears verbatim in a 2048 description
# (the precedent for tuning the list per firm is orbimed_scraper.py /
# coatue_scraper.py). Substring-trap care (see PLAYBOOK): " dna" and " health"
# carry a leading space so they cannot fire inside a longer word.
KEYWORD_TAGS_EXTRA = {
    "BioTech": ["proteomic", "immunolog", " dna", "biomarker"],
    "Health": [" health", "dental", "cardiac", "pulmonolog", "hormone", "rehab",
               "mental heath"],  # "heath" = a typo in 2048's own Psyrin blurb
    "Dev Tools / Cloud": ["data center"],
    "PropTech": ["multifamily"],
    "Deeptech / Robotics / AR/VR": ["3d printing", "precision automation"],
    "Future of Work": ["product demo", "system of record", "corporate innovation",
                       "receptionist"],
    "Consumer": ["restaurant", "shoes"],
    # adtech/martech -> media, matching lererhippeau/hustlefund and enrich.py
    "Gaming / Media / Entertainment": ["marketing"],
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
                 if p and not re.match(r"(ref|utm_[a-z]+|utm_souce|guccounter)=", p, re.I))
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path, q, ""))


def everywhere_tags(name, description):
    """2048 publishes no sectors -> keyword classification of name + description
    only. Order most->least relevant, cap at 4."""
    tags = []
    text = f"{name or ''} {description or ''}".lower()
    # substring-trap guard (see PLAYBOOK): "machine/deep learning" must NOT trip
    # the education "learning"/"learning platform" keywords -> neutralize to "ai".
    text = text.replace("machine learning", "ai").replace("deep learning", "ai")
    for tag, kws in KEYWORD_TAGS:
        if any(kw in text for kw in kws):
            tags.append(tag)
    return tags[:4]


def _visible(el):
    cls = el.get("class") or []
    return "w-condition-invisible" not in cls and "w-dyn-bind-empty" not in cls


def parse_chips(card):
    """The stage and location chips share `.button-outline-invested`; only the
    icon filename distinguishes them."""
    stage = location = None
    for chip in card.select(".button-outline-invested"):
        img = chip.select_one("img")
        src = (img.get("src") or "") if img else ""
        value = clean(chip.get_text(" ", strip=True))
        if "filter-fund" in src:
            stage = stage or value
        elif "filter-location" in src:
            location = location or value
    return stage, location


def parse_team(card):
    people = []
    for block in card.select(".partner-content"):
        if not _visible(block):
            continue
        name_el = block.select_one(".partner-name")
        if not name_el or not _visible(name_el):
            continue
        name = clean(name_el.get_text())
        if not name:
            continue
        title_el = block.select_one(".partner-title")
        photo_el = block.select_one(".partner-avatar")
        people.append({
            "name": name,
            "title": clean(title_el.get_text()) if title_el and _visible(title_el) else None,
            "photo_url": norm_url(photo_el.get("src")) if photo_el and _visible(photo_el) else None,
        })
    return people


def parse_news(card):
    body = card.select_one(".companies-body")
    if not body or not _visible(body):
        return None
    text = clean(body.get_text(" ", strip=True))
    if not text:
        return None
    a = body.select_one("a[href]")
    return {"headline": text, "url": norm_url(a.get("href")) if a else None}


def parse_card(card, scraped_at):
    link = card.select_one("a.heading-h3.link-companies")
    name = clean(link.get_text()) if link else None
    if not name:
        return None

    desc_el = card.select_one(".companies-description p")
    description = clean(desc_el.get_text()) if desc_el else None
    stage, location = parse_chips(card)
    why = card.select_one("a.button-invested")
    why_url = norm_url(why.get("href")) if why and _visible(why) else None

    return {
        "company_name": name,
        "description": description,
        "company_url": norm_url(link.get("href")) if link else None,
        "stage": stage,
        "location": location,
        "team": parse_team(card),
        "latest_news": parse_news(card),
        "why_we_invested_url": why_url,
        "everywhere_tags": everywhere_tags(name, description),
        "source_url": SOURCE_URL,
        "scraped_at": scraped_at,
    }


def scrape(scraped_at, limit=None):
    soup = BeautifulSoup(fetch(COMPANIES_URL), "html.parser")
    cards = soup.select(".card-investment")
    print(f"  found {len(cards)} cards")
    out, seen = [], set()
    for card in cards:
        rec = parse_card(card, scraped_at)
        if not rec or rec["company_name"] in seen:
            continue
        seen.add(rec["company_name"])
        out.append(rec)
        if limit and len(out) >= limit:
            break
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
    for field in ("description", "company_url", "stage", "location",
                  "latest_news", "why_we_invested_url"):
        present = sum(1 for r in out if r[field])
        print(f"  {field}: {present}/{n} present")
    print(f"  team: {sum(1 for r in out if r['team'])}/{n} present "
          f"({sum(len(r['team']) for r in out)} people)")
    print(f"  stage values: {dict(Counter(r['stage'] for r in out))}")
    untagged = sum(1 for r in out if not r["everywhere_tags"])
    print(f"  everywhere_tags: {n - untagged}/{n} tagged ({untagged} untagged)")
    for tag, cnt in Counter(t for r in out for t in r["everywhere_tags"]).most_common():
        print(f"    {tag}: {cnt}")


if __name__ == "__main__":
    main()
