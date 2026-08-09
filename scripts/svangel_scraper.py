#!/usr/bin/env python3
"""
SV Angel portfolio scraper -> svangel_companies.json

Scrapes SV Angel's portfolio (https://svangel.com/portfolio) into a JSON file.

Data source: the site is Next.js (App/Pages Router hybrid) backed by Contentful
CMS, but the portfolio list is fully **server-side rendered** into the page's
`__NEXT_DATA__` blob -- no client-side API calls, no pagination. The cleanest
fetch is the Next.js data route, which returns the same payload as a small JSON
document instead of a full HTML page:

    GET https://svangel.com/_next/data/<buildId>/portfolio.json

`buildId` changes on every SV Angel deploy, so the scraper first fetches the
HTML page to read the current buildId out of the embedded `__NEXT_DATA__`
script tag, then hits the JSON data route (falling back to parsing the HTML
`__NEXT_DATA__` directly if the data route ever 404s post-deploy).

`props.pageProps.data.investmentsListCollection.items` is the full portfolio
grid (151 companies at recon time): each item has `internalTitle` (name),
`investimentStage` (["Seed"] and/or ["Growth"] -- SV Angel's own sic typo,
kept verbatim as the JSON key name only, our field is spelled correctly),
`sector` (0-2 of: AI, Enterprise, Consumer, Fintech, "Healthcare + Bio",
Crypto, Marketplaces), `url` (external company website, null for 3 companies
that just don't list one -- Figma, Reflection, SSI), and `logo` (Contentful
asset with a direct image URL).

Empty != absent check: names are plain (no "(Acquired)" / "(NYSE: X)" suffixes
anywhere in the 151), and there is no description/prose field on this page at
all to mine -- so status/exit/acquirer/ticker/founders/location/founded-year
are genuinely not published here, not denormalized elsewhere. Left off the
schema entirely rather than emitting all-null columns.

One exact duplicate ("Lightning AI" appears twice, identical fields except a
different logo asset id -- a content-source dupe) is deduped, keeping the
first occurrence.

requirements:
    pip install requests beautifulsoup4

usage:
    python3 svangel_scraper.py            # writes ../data/svangel_companies.json
    python3 svangel_scraper.py --limit 10 # only the first ~10 for a test run
"""

import json
import os
import re
import socket
import sys
import time
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

HOME_URL = "https://svangel.com/portfolio"
SOURCE_URL = "https://svangel.com/portfolio"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "data", "svangel_companies.json")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}
TIMEOUT = 45
RETRIES = 3

# ---------------------------------------------------------------------------
# Webflow/legacy-CDN IP workaround (per orchestrator note): this machine
# cannot route to cdn.webflow.com's current IP. svangel.com itself resolved
# fine in recon, but we keep the same defensive fallback used elsewhere in
# this repo in case DNS/routing to svangel.com or images.ctfassets.net (its
# Contentful asset CDN) hiccups the same way -- try normal DNS first, only
# fall back to a pinned IP on a connection failure.
# ---------------------------------------------------------------------------
_LEGACY_IP_PINS = {
    "cdn.webflow.com": "75.2.70.75",
}
_orig_getaddrinfo = socket.getaddrinfo


def _patched_getaddrinfo(host, *args, **kwargs):
    try:
        return _orig_getaddrinfo(host, *args, **kwargs)
    except socket.gaierror:
        pin = _LEGACY_IP_PINS.get(host)
        if pin:
            return _orig_getaddrinfo(pin, *args, **kwargs)
        raise


socket.getaddrinfo = _patched_getaddrinfo

# SV Angel's own sector tags -> the 17-tag everywhere_tags taxonomy. "AI" and
# "Enterprise" are intentionally NOT mapped here: AI alone is not a category
# (classify by the market it serves) and "Enterprise" is too broad for a single
# tag (spans dev-tools / work / data / security) -- both fall through to the
# keyword classifier below. "Marketplaces" is likewise generic (spans Consumer,
# PropTech, Logistics) and left to keywords.
SECTOR_TAG_MAP = {
    "Healthcare + Bio": ["Health"],
    "Fintech": ["FinTech / Insurance"],
    "Crypto": ["Web3 / Crypto"],
    "Consumer": ["Consumer"],
}

# everywhere_tags keyword classifier (substrings, lowercased) -- copied from
# menlo_scraper.py / iconiq_scraper.py for consistency across the repo.
KEYWORD_TAGS = [
    ("BioTech", ["biotech", "drug", "therapeut", "oncolog", "cancer", "tumor", "genomic", "genome",
                 "molecul", "antibod", "protein", "vaccine", "clinical-stage", "medicine", "opioid", "life science",
                 "synthetic biology", "biolog", "biosciences"]),
    ("Health", ["healthcare", "health care", "patient", "clinic", "medical", "mental health", "telehealth",
                "health system", "health record", "diagnos", "surgical", "doctor", "hospital", "pharmac", "therapy"]),
    ("Cybersecurity", ["cybersecurity", "security", "secure", "privacy", "fraud", "phishing", "malware",
                       "ransomware", "endpoint", "zero trust", "vulnerab", "authentication", "threat", "defense system",
                       "identity", "safety"]),
    ("FinTech / Insurance", ["fintech", "payment", "bank", "lending", "loan", "insurance", "credit", "trading",
                             "wallet", "financ", "invoic", "accounting", "payroll", "treasury", "billing",
                             "pricing platform", "rebate", " tax", "audit", "money management", "robo-advisor",
                             "brokerage", "spend management", "capital markets", "investing", "claims"]),
    ("Web3 / Crypto", ["crypto", "blockchain", "web3", "token", "on-chain", "ethereum", "bitcoin", "decentral",
                       "stablecoin", "nft", "digital asset"]),
    ("Gaming / Media / Entertainment", ["game", "gaming", "music", "video", "creator", "content", "publish",
                                        "entertain", "newsletter", "podcast", "film", "streaming", "social media",
                                        "media platform"]),
    ("Dev Tools / Cloud", ["developer", " api ", "apis", "api platform", "infrastructure", "database", "cloud",
                           "open source", "devops", "sdk", "kubernetes", "container", "observability", "deploy",
                           "compute", "storage", "serverless", "inference", "networking", "ethernet", "coding",
                           "codebase", "low-code", "no-code", "source code", "development platform", "incident",
                           " sre", "communications", "llm", "foundation model", "interpretability", "browser",
                           "code assist", "agent"]),
    ("Data & Analytics", ["analytics", "business intelligence", "data platform", "data warehouse", "data lake",
                          "data pipeline", "insights", "dashboard", "experimentation", "decision intelligence",
                          "data quality", "analyz", "data curation", "quality management",
                          "relationship intelligence", "data discovery", "data analysis"]),
    ("Future of Work", ["workforce", "hiring", "recruit", "employee", "productivity", "collaborat", "talent",
                        "workplace", "human resources", " hr ", "learning platform", "customer success",
                        "customer service", "customer support", "presales", " sales ", "onboarding", "workflow",
                        "saas management", "ai assistant", "project management", "scheduling"]),
    ("Transportation / Mobility", ["mobility", "vehicle", "transport", "autonomous", "fleet", "driving", "aviation",
                                   "aircraft", "electric vehicle", "scooter", " bike", "boat", "watercraft",
                                   "rideshar", "travel"]),
    ("Logistics / Supply Chain", ["logistics", "supply chain", "supply and demand", "freight", "warehouse",
                                  "delivery", "procurement", "inventory", "fulfillment", "shipping"]),
    ("PropTech", ["real estate", "property", "housing", "mortgage", "rental", "construction", "tenant", "roofstock"]),
    ("CPG", ["beverage", "snack", "consumer packaged", "beauty", "cosmetic", "apparel", "grocery", "skincare",
             "eyewear", "glasses", "footwear", "pet sitter", "pet "]),
    ("Climate / Sustainability", ["climate", "carbon", "renewable", "solar", "battery", "sustainab", "emission",
                                  "clean energy", "ev charging", "electrif"]),
    ("RegTech/Gov/Legal", ["legal", "compliance", "government", "regulat", "law firm", "attorney"]),
    ("Deeptech / Robotics / AR/VR", ["robot", "hardware", "semiconductor", "chip", "drone", "aerospace",
                                     "augmented reality", "virtual reality", "satellite", "quantum", "sensor", "rfid",
                                     "wifi", "space", "rocket", "launch vehicle"]),
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
            return r
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


def fetch_next_data():
    """Fetch the portfolio page HTML, extract __NEXT_DATA__ (for buildId + as a
    fallback payload), then prefer the smaller /_next/data/<buildId>/portfolio.json
    route if it's reachable. Returns the parsed `pageProps.data` dict."""
    html = get(HOME_URL).text
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if not m:
        raise SystemExit("FATAL: __NEXT_DATA__ script tag not found on portfolio page")
    next_data = json.loads(m.group(1))
    build_id = next_data.get("buildId")
    html_page_props = next_data.get("props", {}).get("pageProps", {})

    if build_id:
        data_url = f"https://svangel.com/_next/data/{build_id}/portfolio.json"
        time.sleep(0.5)
        try:
            r = get(data_url)
            page_props = r.json().get("pageProps", {})
            if page_props.get("data"):
                print(f"  using _next/data route (buildId={build_id})")
                return page_props["data"]
        except (requests.RequestException, ValueError) as e:
            print(f"  ! _next/data route failed ({e}); falling back to embedded __NEXT_DATA__", file=sys.stderr)

    if html_page_props.get("data"):
        print("  using __NEXT_DATA__ embedded in HTML")
        return html_page_props["data"]

    raise SystemExit("FATAL: could not locate investmentsListCollection in either data source")


def everywhere_tags(name, sectors):
    """SV Angel's own sectors first (mapped via SECTOR_TAG_MAP), then keyword
    fallback on the name (no description field exists on this page) to add/refine.
    Order most->least relevant, cap at 4."""
    tags = []
    for sec in sectors:
        for mapped in SECTOR_TAG_MAP.get(sec, []):
            if mapped not in tags:
                tags.append(mapped)
    text = (name or "").lower()
    for tag, kws in KEYWORD_TAGS:
        if tag in tags:
            continue
        if any(kw in text for kw in kws):
            tags.append(tag)
    return tags[:4]


def parse_item(item):
    name = clean(item.get("internalTitle"))
    if not name:
        return None

    stages = [clean(s) for s in (item.get("investimentStage") or []) if clean(s)]
    sectors = [clean(s) for s in (item.get("sector") or []) if clean(s)]
    company_url = clean(item.get("url"))
    logo = item.get("logo") or {}
    logo_url = clean(logo.get("url"))

    return {
        "company_name": name,
        "investment_stage": stages,
        "sectors": sectors,
        "company_url": company_url,
        "logo_url": logo_url,
        "everywhere_tags": everywhere_tags(name, sectors),
        "source_url": SOURCE_URL,
    }


def main():
    limit = int(sys.argv[sys.argv.index("--limit") + 1]) if "--limit" in sys.argv else None

    print(f"Fetching {HOME_URL}")
    data = fetch_next_data()
    items = data.get("investmentsListCollection", {}).get("items", [])
    print(f"  found {len(items)} raw investment items")

    scraped_at = datetime.now(timezone.utc).isoformat()
    out, seen = [], set()
    for item in items:
        rec = parse_item(item)
        if not rec:
            continue
        key = rec["company_name"].strip().lower()
        if key in seen:
            print(f"  ! duplicate '{rec['company_name']}' — keeping first", file=sys.stderr)
            continue
        seen.add(key)
        rec["scraped_at"] = scraped_at
        out.append(rec)
        if limit and len(out) >= limit:
            break

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2)

    # ---- summary ----
    n = len(out)
    print(f"\nWrote {n} companies -> {OUT}")
    for field in ("company_url", "logo_url"):
        miss = sum(1 for r in out if not r[field])
        print(f"  {field:16s} missing: {miss}/{n}")
    print(f"  sectors empty:   {sum(1 for r in out if not r['sectors'])}/{n}")
    print(f"  stage empty:     {sum(1 for r in out if not r['investment_stage'])}/{n}")

    from collections import Counter
    by_sector = Counter(s for r in out for s in r["sectors"])
    by_stage = Counter(s for r in out for s in r["investment_stage"])
    print("  by sector:", dict(by_sector.most_common()))
    print("  by stage:", dict(by_stage.most_common()))

    untagged = [r["company_name"] for r in out if not r["everywhere_tags"]]
    print(f"  untagged:        {len(untagged)}/{n}" + (f" -> {untagged}" if untagged else ""))
    by_tag = Counter(t for r in out for t in r["everywhere_tags"])
    print("  by everywhere_tag:")
    for t, c in by_tag.most_common():
        print(f"    {c:>4}  {t}")


if __name__ == "__main__":
    main()
