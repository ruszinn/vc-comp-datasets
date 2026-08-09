#!/usr/bin/env python3
"""
OrbiMed portfolio scraper -> orbimed_companies.json

Scrapes OrbiMed's portfolio (https://www.orbimed.com/portfolio/) into a JSON
file. WordPress site; the visible `.portfolio-boxes` grid is populated client
side, but the page also embeds a full **inline JS array** with the same data
that feeds the fancybox modals:

    var data_portfolio = [ {...}, {...}, ... ];   // 200 objects, one per company

This is the real data source -- no API call, no pagination, just fetch the one
static page and regex out the JS array literal, then `json.loads` it.

Per record (`data_portfolio[i]`):
  - `title`                  -> company name
  - `description`            -> description (0 empty)
  - `website_url`            -> external site (7 empty)
  - `linkedin_url`, `x_url`  -> socials (71 / 129 empty respectively)
  - `first_invested`         -> "MM/DD/YYYY" first-investment date (0 empty)
  - `sector_companies_post.arr` -> OrbiMed's own sector list (always exactly 1
    of 5: Biopharmaceutical, Medical Device, Diagnostics/Tools, Healthcare
    Services, Healthcare IT)
  - `region_companies_post.arr` -> region list (usually 1, Verdiva Bio has 2:
    Europe / MENA + North America) of 3: North America, Asia, Europe / MENA
  - `logo.url`                -> logo image
  - `slug`                    -> OrbiMed's internal slug (used to build the
    `/portfolio/#<slug>` in-page anchor; OrbiMed publishes standalone
    `/companies/<slug>/` pages too, but those aren't derivable from the JSON
    slug 1:1 for every record -- see below -- so we link to the portfolio
    anchor, which is guaranteed correct for all 200)
  - `acquired`                -> present in the schema but **empty for all 200
    records** (checked -- see "Empty != absent" below)
  - `post_id`                 -> OrbiMed's internal WP post id (kept, harmless)

"Empty != absent" check: `acquired` is blank for every record, so names +
descriptions were grepped for exit language before treating it as N/A. Result:
no company encodes an acquisition/exit in its name (only two names have
parens, both former-name aliases: "BMTfemme (fka MobileODT)", "Harvest
Integrated Research Organization (HiRO)" -- not exits). Exactly one company,
TELA Bio, publishes its own ticker inline at the very start of its description
("TELA Bio (Nasdaq: TELA) is a commercial stage...") -- parsed into
`ticker_symbol`/`exchange`. A second ticker-shaped match (Zentera Therapeutics
mentioning "Zentalis (NASDAQ: ZNTL)") is a false positive -- that ticker
belongs to a licensing partner named later in the sentence, not to Zentera
itself, so it is correctly left unparsed (`ticker_symbol` stays null). No
other structured or prose "acquired by X in YYYY" pattern appears anywhere, so
`status`/`acquirer`/`exit_year` are not fabricated -- OrbiMed simply doesn't
publish exit status on this page.

requirements:
    pip install requests beautifulsoup4

usage:
    python3 orbimed_scraper.py            # writes ../data/orbimed_companies.json
    python3 orbimed_scraper.py --limit 10 # only the first ~10 for a test run
"""

import json
import os
import re
import sys
import time
from datetime import datetime, timezone

import requests

URL = "https://www.orbimed.com/portfolio/"
SOURCE_URL = "https://www.orbimed.com/portfolio/"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "data", "orbimed_companies.json")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}
TIMEOUT = 45
RETRIES = 3

# OrbiMed's 5 own sectors -> the 17-tag everywhere_tags taxonomy.
SECTOR_TAG_MAP = {
    "Biopharmaceutical": ["BioTech", "Health"],
    "Medical Device": ["Health", "Deeptech / Robotics / AR/VR"],
    "Diagnostics/Tools": ["Health", "Data & Analytics"],
    "Healthcare Services": ["Health"],
    "Healthcare IT": ["Health", "Data & Analytics"],
}

# everywhere_tags keyword classifier (substrings, lowercased) -- copied from
# menlo_scraper.py / iconiq_scraper.py, healthcare-heavy site so Health/BioTech
# keywords do most of the work; kept for stragglers / mistagged sectors.
KEYWORD_TAGS = [
    ("BioTech", ["biotech", "drug", "therapeut", "oncolog", "cancer", "tumor", "genomic", "genome",
                 "molecul", "antibod", "protein", "vaccine", "clinical-stage", "clinical stage", "medicine",
                 "synthetic biology", "biolog", "biopharma", "gene therapy", "cell therapy", "rna", "adc",
                 "immunotherapy", "peptide"]),
    ("Health", ["healthcare", "health care", "patient", "clinic", "medical", "mental health", "telehealth",
                "health system", "health record", "diagnos", "surgical", "doctor", "hospital", "pharmac", "therapy",
                "device", "implant", "catheter", "orthoped", "cardio", "wound care", "dental", "ophthal", "imaging"]),
    ("Cybersecurity", ["cybersecurity", "security", "secure messaging", "secure data", "data privacy", "fraud",
                       "phishing", "malware", "ransomware", "zero trust", "cyber threat", "authentication"]),
    ("FinTech / Insurance", ["fintech", "payment", "lending", "insurance", "credit score", "credit card",
                             "trading platform", "wallet", "financ", "invoic", "accounting", "payroll",
                             "treasury", "billing", "insurance benefits", "benefits provider"]),
    ("Web3 / Crypto", ["crypto", "blockchain", "web3", "token", "on-chain", "ethereum", "bitcoin", "decentral"]),
    ("Gaming / Media / Entertainment", ["game", "gaming", "video", "creator", "content", "publish",
                                        "entertain", "podcast", "film", "streaming", "social media"]),
    ("Dev Tools / Cloud", ["developer", " api ", "apis", "infrastructure", "database", "cloud", "open source",
                           "devops", "sdk", "kubernetes", "container", "software platform", "saas"]),
    ("Data & Analytics", ["analytics", "business intelligence", "data platform", "data warehouse", "data lake",
                          "data pipeline", "insights", "dashboard", "real-world data", "artificial intelligence",
                          "machine learning", " ai "]),
    ("Future of Work", ["workforce", "hiring", "recruit", "employee", "productivity", "collaborat", "talent",
                        "workplace"]),
    ("Transportation / Mobility", ["mobility", "vehicle", "transport", "autonomous", "fleet", "driving", "aviation"]),
    ("Logistics / Supply Chain", ["logistics", "supply chain", "freight", "warehouse", "procurement"]),
    ("PropTech", ["real estate", "property", "housing", "mortgage", "rental", " construction"]),
    ("CPG", ["beverage", "snack", "consumer packaged", "beauty", "cosmetic", "apparel", "grocery", "skincare"]),
    ("Climate / Sustainability", ["climate", "carbon capture", "carbon emission", "renewable energy", "solar",
                                  "battery", "clean energy", "greenhouse gas"]),
    ("RegTech/Gov/Legal", ["legal", "compliance", "government", "regulatory affairs", "regulatory approval",
                           "law firm", "attorney"]),
    ("Deeptech / Robotics / AR/VR", ["robot", "hardware", "semiconductor", "chip", "drone", "aerospace",
                                     "augmented reality", "virtual reality", "satellite", "quantum", "sensor",
                                     "3d printing"]),
    ("Consumer", ["marketplace", "consumer", "shopping", "social network", "community"]),
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


TICKER_RE = re.compile(r"\((?:NASDAQ|NYSE(?: American)?|Nasdaq)\s*:\s*([A-Z]{1,6})\)")


def parse_own_ticker(name, description):
    """Only trust a ticker mention if it appears essentially at the start of
    the description (i.e. describing THIS company), not a licensing/partner
    mention later in the text (e.g. Zentera -> Zentalis (NASDAQ: ZNTL))."""
    if not description:
        return None, None
    m = TICKER_RE.search(description)
    if not m:
        return None, None
    if m.start() > 40:  # own-company mentions land right after the name, e.g. "TELA Bio (Nasdaq: TELA) is..."
        return None, None
    exchange = "NASDAQ" if "nasdaq" in m.group(0).lower() else "NYSE"
    return m.group(1), exchange


def everywhere_tags(name, description, sectors):
    """OrbiMed sectors first (mapped via SECTOR_TAG_MAP), then keyword
    fallback on name + description to add/refine. Order most->least
    relevant, cap at 4, no duplicates."""
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


def parse_record(d):
    name = clean(d.get("title"))
    if not name:
        return None

    description = clean(d.get("description"))
    website_url = clean(d.get("website_url"))
    linkedin_url = clean(d.get("linkedin_url"))
    x_url = clean(d.get("x_url"))
    slug = clean(d.get("slug"))
    first_invested = clean(d.get("first_invested"))

    sectors = list((d.get("sector_companies_post") or {}).get("arr") or [])
    sectors = [clean(s) for s in sectors if clean(s)]

    regions = list((d.get("region_companies_post") or {}).get("arr") or [])
    regions = [clean(r).replace(" ", " ") if clean(r) else None for r in regions]
    regions = [r for r in regions if r]

    logo_url = clean((d.get("logo") or {}).get("url"))

    ticker_symbol, exchange = parse_own_ticker(name, description)

    portfolio_anchor = f"https://www.orbimed.com/portfolio/#{slug}" if slug else None

    return {
        "company_name": name,
        "description": description,
        "company_url": website_url,
        "logo_url": logo_url,
        "sectors": sectors,
        "regions": regions,
        "first_invested": first_invested,
        "ticker_symbol": ticker_symbol,
        "exchange": exchange,
        "social_urls": {
            "linkedin": linkedin_url,
            "twitter": x_url,
        },
        "everywhere_tags": everywhere_tags(name, description, sectors),
        "orbimed_profile_url": portfolio_anchor,
        "source_url": SOURCE_URL,
    }


def main():
    limit = int(sys.argv[sys.argv.index("--limit") + 1]) if "--limit" in sys.argv else None

    html = get(URL)
    m = re.search(r"var data_portfolio\s*=\s*(\[.*?\]);", html, re.S)
    if not m:
        raise SystemExit("FATAL: could not find `var data_portfolio = [...]` in portfolio page")
    data = json.loads(m.group(1))

    scraped_at = datetime.now(timezone.utc).isoformat()
    out = []
    for d in data[:limit] if limit else data:
        rec = parse_record(d)
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
    for field in ("description", "company_url", "logo_url", "first_invested"):
        miss = sum(1 for r in out if not r[field])
        print(f"  {field:15s} missing: {miss}/{n}")
    print(f"  sectors empty: {sum(1 for r in out if not r['sectors'])}/{n}")
    print(f"  regions empty: {sum(1 for r in out if not r['regions'])}/{n}")
    print(f"  linkedin empty: {sum(1 for r in out if not r['social_urls']['linkedin'])}/{n}")
    print(f"  twitter empty:  {sum(1 for r in out if not r['social_urls']['twitter'])}/{n}")
    tickers = [(r["company_name"], r["ticker_symbol"], r["exchange"]) for r in out if r["ticker_symbol"]]
    print(f"  tickers found: {tickers}")
    untagged = [r["company_name"] for r in out if not r["everywhere_tags"]]
    print(f"  untagged:      {len(untagged)}/{n}" + (f" -> {untagged}" if untagged else ""))
    by_tag = {}
    for r in out:
        for t in r["everywhere_tags"]:
            by_tag[t] = by_tag.get(t, 0) + 1
    print("  by everywhere_tag:")
    for t, k in sorted(by_tag.items(), key=lambda x: -x[1]):
        print(f"    {k:3d}  {t}")
    by_sector = {}
    for r in out:
        for s in r["sectors"]:
            by_sector[s] = by_sector.get(s, 0) + 1
    print("  by OrbiMed sector:")
    for s, k in sorted(by_sector.items(), key=lambda x: -x[1]):
        print(f"    {k:3d}  {s}")


if __name__ == "__main__":
    main()
