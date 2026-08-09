#!/usr/bin/env python3
"""
Mayfield Fund portfolio scraper -> mayfield_companies.json

Scrapes Mayfield's portfolio from https://www.mayfield.com/meet-our-founders/.
The site is WordPress + a theme that inlines the ENTIRE portfolio grid as a
JS variable in the page source -- no API, no pagination, one GET:

    var data_our_portfolio = [ {...}, {...}, ... ];   // 135 companies

Per record (only fields actually present are used; logo media-library blob is
reduced to just the display URL):
  - `post_title`                       -> company name
  - `slug` / `permalink`               -> Mayfield's own portfolio-detail URL
      (checked: the detail page itself renders almost nothing besides an
      investment-month header and an empty body -- all real data lives in
      this one JS blob, so detail pages are NOT separately fetched)
  - `popup_text`                       -> description (one-liner)
  - `website_url` / `linkedin_url` / `x_url` -> external links (Mayfield's own)
  - `logo.url`                         -> logo image
  - `sector_portfolio.arr`             -> Mayfield's own sector slugs (mapped to
      display names via SECTOR_DISPLAY: ai, enterprise, consumer,
      semiconductors, human-health)
  - `status_portfolio.name`            -> Mayfield's own coarse status filter
      ("Current" vs "Milestone" -- Milestone = had a liquidity event); kept
      raw as `mayfield_status`, but `status`/`ticker_symbol`/`acquirer` are
      DERIVED from the `partnered` field (see below) since that carries the
      actual detail (Empty != absent: `status_portfolio` alone can't tell
      Public from Acquired -- both are "Milestone").
  - `teams`                            -> internal WP post IDs of the Mayfield
      partner(s) on the deal. Resolved to partner names via one GET per
      distinct ID against the site's own `/wp-json/wp/v2/team/<id>` endpoint
      (7 distinct IDs total across the whole portfolio -- cheap, and it's
      Mayfield's own team roster, not a third-party enrichment source).

`partnered` free-text field (Empty != absent mining target) encodes exit
state for every company that has had one, e.g.:
  "NASDAQ: HCP, Acq. by IBM "   -> Public ticker THEN acquired
  "Acq. by Veeam"               -> acquired, no historical ticker
  "NASDAQ: LYFT "               -> currently public, no acquirer
  "Former NASDAQ: SGI"          -> delisted/taken private, no acquirer named
  "Merged with HP"              -> treated as an acquisition
  "IPO India: TEJASNET.NS "     -> public on an Indian exchange
  "Partnered at Inception"      -> NOT an exit signal (investment-stage note
      for one still-current company, Marco Polo) -- explicitly excluded from
      the exit-parsing regex.
Parsed into `status` (Active / Public / Acquired), `ticker_symbol`,
`exchange`, `acquirer`. 3 companies (Brokk, Hang Ten, Retym) have no
`status_portfolio` tag at all and empty `partnered` -> `status="Active"`.

What Mayfield does NOT expose (checked names + descriptions per "Empty !=
absent" -- no further hits): founders (people), founded year, HQ/location,
investment stage/round, funding amount. Left absent, not invented.

requirements:
    pip install requests beautifulsoup4

usage:
    python3 mayfield_scraper.py            # writes ../data/mayfield_companies.json
    python3 mayfield_scraper.py --limit 10 # only the first ~10 for a test run
"""

import json
import os
import re
import sys
import time
from datetime import datetime, timezone

import requests

PORTFOLIO_URL = "https://www.mayfield.com/meet-our-founders/"
SOURCE_URL = "https://www.mayfield.com/meet-our-founders/"
TEAM_API = "https://www.mayfield.com/wp-json/wp/v2/team/{id}?_fields=id,slug,link,title"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "data", "mayfield_companies.json")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}
TIMEOUT = 45
RETRIES = 3
SLEEP_BETWEEN = 0.4

# Mayfield's own sector filter slugs -> display names.
SECTOR_DISPLAY = {
    "ai": "AI",
    "enterprise": "Enterprise",
    "consumer": "Consumer",
    "semiconductors": "Semiconductors",
    "human-health": "Human Health",
}
CANON_ORDER = ["AI", "Enterprise", "Consumer", "Semiconductors", "Human Health"]

# Mayfield sectors -> the 17-tag everywhere_tags taxonomy. "AI" and
# "Enterprise" are intentionally NOT mapped here: AI alone is not a category
# (classify by market served) and "Enterprise" spans dev-tools / work / data /
# security with no single tag -- both left to the keyword classifier.
SECTOR_TAG_MAP = {
    "Human Health": ["Health"],
    "Consumer": ["Consumer"],
    "Semiconductors": ["Deeptech / Robotics / AR/VR"],
}

# everywhere_tags keyword classifier (substrings, lowercased) -- copied from
# menlo_scraper.py / iconiq_scraper.py.
KEYWORD_TAGS = [
    ("BioTech", ["biotech", "drug", "therapeut", "oncolog", "cancer", "tumor", "genomic", "genome",
                 "molecul", "antibod", "protein", "vaccine", "clinical-stage", "medicine", "opioid", "life science",
                 "synthetic biology", "biolog", "pharma"]),
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
                                        "entertain", "newsletter", "podcast", "film", "streaming", "social media", "media platform",
                                        "advertising", "cable network", "radio station", "television station", "ad exchange"]),
    ("Dev Tools / Cloud", ["developer", " api ", "apis", "api platform", "infrastructure", "database", "cloud",
                           "open source", "devops", "sdk", "kubernetes", "container", "observability", "deploy", "compute",
                           "storage", "serverless", "inference", "networking", "ethernet", "coding", "codebase", "low-code",
                           "no-code", "source code", "development platform", "incident", " sre", "voicemail", "communications",
                           "llm", "foundation model", "interpretability", "voice keyboard", "voice recognition", "frame relay",
                           "log management", "file sharing", "tech stack", "voice agent", "appliance software",
                           "text to speech", "software suite", "applications infrastructure", "virtualization",
                           "integration platform", "integration software", "business integration", "wan acceleration",
                           "mcp gateway", "gateway", "software testing", "fault tolerant", "portable computing",
              "visualization computing", "interconnect", "photonics", "datacenter", "data center", "coherent dsp",
                           "diffusion language model", "language model", "speech software", "smart 4g", "telecoms",
                           "mobile sms", "sase", "digital twin"]),
    ("Data & Analytics", ["analytics", "business intelligence", "data platform", "data warehouse", "data lake",
                          "data pipeline", "insights", "dashboard", "experimentation", "decision intelligence", "data quality",
                          "analyz", "data curation", "quality management", "relationship intelligence",
                          "data discovery", "data analysis", "edge-data", "complex data", "real-world data", "analyst",
                          "data intelligence", "data transformation", "data integration", "company intelligence",
                          "data engineer", "decisioning platform", "fp&a", "spreadsheet native"]),
    ("Future of Work", ["workforce", "hiring", "recruit", "employee", "productivity", "collaborat", "talent",
                        "workplace", "human resources", " hr ", "learning platform", "customer success", "customer service",
                        "customer support", "presales", " sales ", "onboarding", "workflow", "saas management", "ai assistant",
                        "project management", "partnerships platform", "partnership", "teamwork", "scheduling", "work assistant",
                        "agentic teammate", "marketing automation", "marketing team", "expense management", "call center",
                        "field service", "business team", "revenue team"]),
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
                                     "space", "rocket", "launch vehicle", "optics", "defense", "silicon"]),
    ("Consumer", ["marketplace", "consumer", "shopping", "social network", "community", "app for", "app that",
                  "ecommerce", "e-commerce", "subscription", "retailer", "universit", "student", "education",
                  "learning", "fashion", "ridesharing"]),
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
    """Mayfield sectors first (mapped via SECTOR_TAG_MAP), then keyword
    fallback on name + description to add/refine. Order most->least relevant,
    cap at 4."""
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


# Regex parsing of the free-text `partnered` field into structured exit data.
# NOTE: "Partnered at Inception" is an investment-stage note, not an exit
# signal, and must be excluded before the ticker/acquirer regexes run.
_ACQ_RE = re.compile(r"Acq\.?\s*by\s+(.+?)\s*$", re.I)
_MERGE_RE = re.compile(r"Merged with\s+(.+?)\s*$", re.I)
_TICKER_RE = re.compile(r"(NASDAQ|NYSE|IPO India)\s*:\s*([A-Za-z0-9.]+)")
_FORMER_RE = re.compile(r"^Former\s", re.I)


def parse_partnered(raw):
    """Returns (status, ticker_symbol, exchange, acquirer) derived from the
    `partnered` free-text field. status is one of Active/Public/Acquired."""
    s = clean(raw)
    if not s or s.lower() == "partnered at inception":
        return "Active", None, None, None

    acquirer = None
    am = _ACQ_RE.search(s)
    if am:
        acquirer = clean(am.group(1).rstrip("."))
    mm = _MERGE_RE.search(s)
    if mm and not acquirer:
        acquirer = clean(mm.group(1))

    ticker = None
    exchange = None
    tm = _TICKER_RE.search(s)
    if tm:
        exchange = tm.group(1)
        ticker = clean(tm.group(2))

    if acquirer:
        status = "Acquired"
    elif ticker:
        status = "Public"
    else:
        # e.g. an unrecognized free-text exit note with no ticker/acquirer
        # captured -- still signals an exit event per Mayfield's own
        # "Milestone" status, but nothing structured to attach -> Acquired
        # is the closest of the 3-state model when a value is present at all.
        status = "Acquired"
    return status, ticker, exchange, acquirer


def fetch_team_names(team_ids):
    """Resolve distinct Mayfield partner post IDs -> names via the site's own
    WP REST API for the `team` post type. Cheap: only a handful of distinct
    IDs across the whole portfolio. Invalid/deleted IDs are skipped."""
    names = {}
    for tid in sorted(team_ids):
        url = TEAM_API.format(id=tid)
        try:
            r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
            if r.status_code == 200:
                data = r.json()
                title = clean((data.get("title") or {}).get("rendered"))
                if title:
                    names[tid] = title
            else:
                print(f"  ! team id {tid}: HTTP {r.status_code} (skipped)", file=sys.stderr)
        except requests.RequestException as e:  # noqa
            print(f"  ! team id {tid}: {e} (skipped)", file=sys.stderr)
        time.sleep(SLEEP_BETWEEN)
    return names


def parse_record(d, team_names):
    name = clean(d.get("post_title"))
    if not name:
        return None

    description = clean(d.get("popup_text"))
    company_url = clean(d.get("website_url"))
    linkedin_url = clean(d.get("linkedin_url"))
    x_url = clean(d.get("x_url"))
    logo_url = clean((d.get("logo") or {}).get("url"))
    profile_url = clean(d.get("permalink"))

    sector_slugs = (d.get("sector_portfolio") or {}).get("arr") or []
    sectors = [SECTOR_DISPLAY.get(s, s) for s in sector_slugs if s]
    sectors = [s for s in CANON_ORDER if s in sectors] + [s for s in sectors if s not in CANON_ORDER]

    mayfield_status = clean((d.get("status_portfolio") or {}).get("name"))
    status, ticker_symbol, exchange, acquirer = parse_partnered(d.get("partnered"))

    partner_ids = d.get("teams") or []
    partners = [team_names[i] for i in partner_ids if i in team_names]

    return {
        "company_name": name,
        "description": description,
        "company_url": company_url,
        "company_profile_url": profile_url,
        "logo_url": logo_url,
        "sectors": sectors,
        "mayfield_status": mayfield_status,
        "status": status,
        "ticker_symbol": ticker_symbol,
        "exchange": exchange,
        "acquirer": acquirer,
        "partners": partners,
        "linkedin_url": linkedin_url,
        "x_url": x_url,
        "everywhere_tags": everywhere_tags(name, description, sectors),
        "source_url": SOURCE_URL,
    }


def main():
    limit = int(sys.argv[sys.argv.index("--limit") + 1]) if "--limit" in sys.argv else None

    html = get(PORTFOLIO_URL)
    m = re.search(r"var data_our_portfolio = (\[.*?\]);\s*\n", html, re.S)
    if not m:
        raise SystemExit("FATAL: could not find data_our_portfolio JS blob in page HTML")
    raw = json.loads(m.group(1))

    team_ids = set()
    for d in raw:
        team_ids.update(d.get("teams") or [])
    team_names = fetch_team_names(team_ids)

    scraped_at = datetime.now(timezone.utc).isoformat()
    out = []
    for d in raw[:limit] if limit else raw:
        rec = parse_record(d, team_names)
        if not rec:
            continue
        rec["scraped_at"] = scraped_at
        out.append(rec)

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2)

    # ---- summary ----
    n = len(out)
    print(f"wrote {n} companies -> {OUT}")
    for field in ("description", "company_url", "logo_url", "linkedin_url", "x_url",
                  "ticker_symbol", "acquirer"):
        miss = sum(1 for r in out if not r[field])
        print(f"  {field:18s} missing: {miss}/{n}")
    print(f"  sectors empty:      {sum(1 for r in out if not r['sectors'])}/{n}")
    print(f"  partners empty:     {sum(1 for r in out if not r['partners'])}/{n}")
    from collections import Counter
    status_counts = Counter(r["status"] for r in out)
    print(f"  status breakdown:   {dict(status_counts)}")
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
