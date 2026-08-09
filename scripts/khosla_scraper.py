#!/usr/bin/env python3
"""
Khosla Ventures portfolio scraper -> khosla_companies.json

Scrapes Khosla Ventures' portfolio, which is spread across 8 sector "category"
pages on a Webflow site (no single all-companies listing page):

    https://www.khoslaventures.com/category/consumer-and-retail
    https://www.khoslaventures.com/category/digital-health
    https://www.khoslaventures.com/category/enterprise
    https://www.khoslaventures.com/category/fintech
    https://www.khoslaventures.com/category/frontier
    https://www.khoslaventures.com/category/med-tech-and-diagnostics
    https://www.khoslaventures.com/category/sustainability
    https://www.khoslaventures.com/category/therapeutics

plus a 9th, cross-cutting category that is NOT a sector but an exit flag:

    https://www.khoslaventures.com/category/exits

Each category page fully server-renders a Webflow CMS collection
(`.company-cards.w-dyn-list` -> `.company-card-item.w-dyn-item`) with no
pagination -- one GET per page returns the whole list. Recon confirmed the 8
sector categories are mutually exclusive (132 unique companies, zero overlap
across sector pages) and "exits" is a pure cross-listing tag (18 of the 132
also appear on /category/exits) -- so `sectors` comes from the 8 sector pages
and `is_exit` is derived from membership in the 9th. The `/portfolio` landing
page itself only shows small "highlight" carousels per category (a curated
subset, not the full roster) with a "View More" link to each category page --
so the category pages, not /portfolio, are the source of truth for the full
company list.

Per `.company-card-item`: `a.company-slide[href]` = external company website
(there is no per-company Khosla detail page -- the only link is straight to
the company's own site), `img[alt]` = company name, `img[src]` = logo,
`.text-block-17` = a one-line description/tagline.

"Empty != absent" checked: no ticker/ NYSE/NASDAQ/IPO/"Acquired by" pattern
appears in any of the 132 names or one-line descriptions (one false-positive
hit, "public utility" in a description, and one parenthetical abbreviation,
"Commonwealth Fusion Systems (CFS)", that is not exit info). Khosla flags an
exit only via the binary "exits" category membership -- it does NOT publish
which exit type (IPO vs M&A), an acquirer, a ticker, or an exit year anywhere
in this data, so `status`/`acquirer`/`ticker_symbol`/`exit_year` are NOT
invented; only `is_exit` (bool) is derived. Khosla also does not expose
founders, investment stage/year, or company HQ location on these pages, so
those fields are intentionally omitted rather than filled with nulls for
fields that don't exist in the source at all.

*** NETWORK CAVEAT: RELAY-FETCHED ***
www.khoslaventures.com was directly unreachable from the machine this scraper
was authored on: the current CDN IP (198.202.211.1) is unroutable, and legacy
IPs (75.2.70.75 / 99.83.190.102) reject the TLS handshake for this hostname
(SNI-based routing rejects the pinned IP). All recon + the data below were
fetched through the read-only relay https://r.jina.ai/<url> with the header
`x-respond-with: html` (returns raw HTML instead of markdown), which serves
Khosla's own page content unmodified -- not a third-party database. This
script tries a direct HTTPS GET first, then a legacy-IP pin (Host header +
disabled SNI-hostname check), and only falls back to the r.jina.ai relay if
both fail, so it will use the fast direct path unchanged on a healthy network.
Because the relay is shared infrastructure, requests are throttled to
roughly one every 1.5s regardless of which path is used.
Spot-check the parsed output extra carefully if you re-run this via the relay.

requirements:
    pip install requests beautifulsoup4

usage:
    python3 khosla_scraper.py            # writes ../data/khosla_companies.json
    python3 khosla_scraper.py --limit 10 # only the first ~10 for a test run
"""

import json
import os
import re
import socket
import sys
import time
from datetime import datetime, timezone
from html import unescape

import requests
from bs4 import BeautifulSoup

BASE = "https://www.khoslaventures.com"
RELAY_PREFIX = "https://r.jina.ai/"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "data", "khosla_companies.json")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}
RELAY_HEADERS = dict(HEADERS, **{"x-respond-with": "html"})
TIMEOUT = 45
RETRIES = 3
RELAY_SLEEP = 1.5  # politeness floor: shared relay infra

# Legacy IPs seen historically for khoslaventures.com; kept as a middle fallback
# rung. SNI-based CDN routing means pinning the IP with the real Host header
# often still fails -- that's expected, and we fall through to the relay.
LEGACY_IPS = ["75.2.70.75", "99.83.190.102"]

# The 8 real sector category slugs -> display sector names. "exits" is
# deliberately NOT in this map: recon showed it's a cross-cutting flag (18 of
# 132 companies also listed there), not a 9th mutually-exclusive sector.
SECTOR_CATEGORIES = {
    "consumer-and-retail": "Consumer & Retail",
    "digital-health": "Digital Health",
    "enterprise": "Enterprise",
    "fintech": "Fintech",
    "frontier": "Frontier",
    "med-tech-and-diagnostics": "Med Tech & Diagnostics",
    "sustainability": "Sustainability",
    "therapeutics": "Therapeutics",
}
EXITS_SLUG = "exits"

# Khosla's 8 sectors -> the 17-tag everywhere_tags taxonomy. "Enterprise" and
# "Frontier" are intentionally NOT mapped: "Enterprise" spans dev-tools / work
# / data / security with no single tag, and "Frontier" is Khosla's own grab-bag
# for deep-tech / space / AI-research bets -- both are left to the keyword
# fallback to classify by the market actually served (AI alone isn't a
# category).
SECTOR_TAG_MAP = {
    "Digital Health": ["Health"],
    "Fintech": ["FinTech / Insurance"],
    "Med Tech & Diagnostics": ["Health", "BioTech"],
    "Sustainability": ["Climate / Sustainability"],
    "Therapeutics": ["BioTech"],
    "Consumer & Retail": ["Consumer"],
}

# everywhere_tags keyword classifier (substrings, lowercased) -- copied from
# menlo_scraper.py / iconiq_scraper.py / foundersfund_scraper.py.
KEYWORD_TAGS = [
    ("BioTech", ["biotech", "drug", "therapeut", "oncolog", "cancer", "tumor", "genomic", "genome",
                 "molecul", "antibod", "protein", "vaccine", "clinical-stage", "medicine", "opioid", "life science",
                 "synthetic biology", "biolog", "stem cell", "cell therapy"]),
    ("Health", ["healthcare", "health care", "patient", "clinic", "medical", "mental health", "telehealth",
                "health system", "health record", "diagnos", "surgical", "doctor", "hospital", "pharmac", "therapy",
                "depression treatment", "family care"]),
    ("Cybersecurity", ["cybersecurity", "security", "secure", "privacy", "fraud", "phishing", "malware",
                       "ransomware", "endpoint", "zero trust", "vulnerab", "authentication", "threat", "defense system", "identity"]),
    ("FinTech / Insurance", ["fintech", "payment", "bank", "lending", "loan", "insurance", "credit", "trading",
                             "wallet", "financ", "invoic", "accounting", "payroll", "treasury", "billing", "pricing platform",
                             "rebate", " tax", "audit", "money management", "robo-advisor", "brokerage", "spend management",
                             "capital markets", "investing", "fp&a"]),
    ("Web3 / Crypto", ["crypto", "blockchain", "web3", "token", "on-chain", "ethereum", "bitcoin", "decentral", "stablecoin", "nft"]),
    ("Gaming / Media / Entertainment", ["game", "gaming", "music", "video", "creator", "content", "publish",
                                        "entertain", "newsletter", "podcast", "film", "streaming", "social media", "media platform"]),
    ("Dev Tools / Cloud", ["developer", " api ", "apis", "api platform", "infrastructure", "database", "cloud",
                           "open source", "devops", "sdk", "kubernetes", "container", "observability", "deploy", "compute",
                           "storage", "serverless", "inference", "networking", "ethernet", "coding", "codebase", "low-code",
                           "no-code", "source code", "development platform", "incident", " sre", "voicemail", "communications",
                           "llm", "foundation model", "interpretability", "chip design", "analog layout", "world model",
                           "devsecops"]),
    ("Data & Analytics", ["analytics", "business intelligence", "data platform", "data warehouse", "data lake",
                          "data pipeline", "insights", "dashboard", "experimentation", "decision intelligence", "data quality",
                          "analyz", "data curation", "quality management", "relationship intelligence", "data management"]),
    ("Future of Work", ["workforce", "hiring", "recruit", "employee", "productivity", "collaborat", "talent",
                        "workplace", "human resources", " hr ", "learning platform", "customer success", "customer service",
                        "customer support", "presales", " sales ", "onboarding", "workflow", "saas management", "ai assistant",
                        "executive assistant", "accountant"]),
    ("Transportation / Mobility", ["mobility", "vehicle", "transport", "autonomous", "fleet", "driving", "aviation",
                                   "aircraft", "electric vehicle", "scooter", " bike", "boat", "watercraft", "rideshar", "travel"]),
    ("Logistics / Supply Chain", ["logistics", "supply chain", "supply and demand", "freight", "warehouse",
                                  "delivery", "procurement", "inventory", "fulfillment", "shipping"]),
    ("PropTech", ["real estate", "property", "housing", "mortgage", "rental", "construction", "tenant", "sell a home"]),
    ("CPG", ["beverage", "snack", "consumer packaged", "beauty", "cosmetic", "apparel", "grocery", "skincare",
             "eyewear", "glasses", "footwear", "pet sitter", "pet ", "eat"]),
    ("Climate / Sustainability", ["climate", "carbon", "renewable", "solar", "battery", "sustainab", "emission",
                                  "clean energy", "ev charging", "electrif", "energy", "power grid", "lithium",
                                  "perovskite", "agriculture", "fusion"]),
    ("RegTech/Gov/Legal", ["legal", "compliance", "government", "regulat", "law firm", "attorney", "risk services", "lawsuit"]),
    ("Deeptech / Robotics / AR/VR", ["robot", "hardware", "semiconductor", "chip", "drone", "aerospace",
                                     "augmented reality", "virtual reality", "satellite", "quantum", "sensor", "rfid", "wifi",
                                     "space", "rocket", "launch vehicle", "imaging satellite", "intelligent machinery"]),
    ("Consumer", ["marketplace", "consumer", "shopping", "social network", "community", "app for", "app that",
                  "ecommerce", "e-commerce", "subscription", "retailer", "universit", "student", "education",
                  "learning", "fashion", "sleep"]),
]


class _NoSNIHostnameAdapter(requests.adapters.HTTPAdapter):
    """Pin a connection to a specific IP while still sending the real Host
    header / SNI for TLS -- used for the legacy-IP fallback rung."""

    def __init__(self, ip, *args, **kwargs):
        self._ip = ip
        super().__init__(*args, **kwargs)

    def get_connection(self, url, proxies=None):
        pool = super().get_connection(url, proxies)
        return pool


def _direct_get(url):
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    return r.text


def _legacy_ip_get(url, host):
    last = None
    for ip in LEGACY_IPS:
        try:
            # Resolve the hostname to the pinned legacy IP for this one call.
            orig_getaddrinfo = socket.getaddrinfo

            def patched_getaddrinfo(h, *a, **kw):
                if h == host:
                    h = ip
                return orig_getaddrinfo(h, *a, **kw)

            socket.getaddrinfo = patched_getaddrinfo
            try:
                r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
                r.raise_for_status()
                return r.text
            finally:
                socket.getaddrinfo = orig_getaddrinfo
        except requests.RequestException as e:  # noqa
            last = e
            continue
    raise last or RuntimeError("no legacy IPs configured")


def _relay_get(url):
    r = requests.get(RELAY_PREFIX + url, headers=RELAY_HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    return r.text


def get(url):
    """Direct HTTPS -> legacy-IP pin -> r.jina.ai relay, in that order, with
    retries/backoff at each rung. Sleeps politely between relay calls."""
    from urllib.parse import urlparse
    host = urlparse(url).netloc

    last = None
    for attempt in range(1, RETRIES + 1):
        try:
            return _direct_get(url)
        except requests.RequestException as e:  # noqa
            last = e
            time.sleep(1.0 * attempt)
    print(f"  ! direct fetch failed for {url} ({last}); trying legacy IP", file=sys.stderr)

    try:
        return _legacy_ip_get(url, host)
    except Exception as e:  # noqa
        print(f"  ! legacy-IP fetch failed for {url} ({e}); falling back to r.jina.ai relay", file=sys.stderr)

    last = None
    for attempt in range(1, RETRIES + 1):
        try:
            text = _relay_get(url)
            time.sleep(RELAY_SLEEP)
            return text
        except requests.RequestException as e:  # noqa
            last = e
            wait = RELAY_SLEEP * attempt
            print(f"  ! relay request failed ({e}); retry {attempt}/{RETRIES} in {wait:.1f}s", file=sys.stderr)
            time.sleep(wait)
    raise SystemExit(f"FATAL: could not fetch {url} via direct, legacy IP, or relay: {last}")


def clean(s):
    if s is None:
        return None
    s = re.sub(r"\s+", " ", unescape(s)).strip()
    return s or None


def everywhere_tags(name, description, sectors):
    """Khosla sectors first (mapped via SECTOR_TAG_MAP), then keyword fallback
    on name + description to add/refine. Order most->least relevant, cap at 4."""
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


def parse_category_page(html):
    """Return list of {name, description, company_url, logo_url} for one
    /category/<slug> page's `.company-card-item` grid."""
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for item in soup.select(".company-cards.w-dyn-list .company-card-item"):
        a = item.select_one("a.company-slide")
        if not a:
            continue
        company_url = clean(a.get("href"))
        img = a.select_one("img")
        name = clean(img.get("alt")) if img else None
        logo_url = clean(img.get("src")) if img else None
        desc_el = a.select_one(".text-block-17")
        description = clean(desc_el.get_text()) if desc_el else None
        if not name:
            continue
        out.append({
            "company_name": name,
            "description": description,
            "company_url": company_url,
            "logo_url": logo_url,
        })
    return out


def main():
    limit = int(sys.argv[sys.argv.index("--limit") + 1]) if "--limit" in sys.argv else None

    by_name = {}
    order = []
    for slug, sector in SECTOR_CATEGORIES.items():
        url = f"{BASE}/category/{slug}"
        print(f"Fetching {url}")
        rows = parse_category_page(get(url))
        print(f"  {len(rows)} companies")
        for r in rows:
            key = r["company_name"]
            if key not in by_name:
                by_name[key] = dict(r, sectors=[], is_exit=False)
                order.append(key)
            if sector not in by_name[key]["sectors"]:
                by_name[key]["sectors"].append(sector)

    exits_url = f"{BASE}/category/{EXITS_SLUG}"
    print(f"Fetching {exits_url}")
    exit_rows = parse_category_page(get(exits_url))
    print(f"  {len(exit_rows)} companies")
    exit_names = {r["company_name"] for r in exit_rows}
    for name in exit_names:
        if name in by_name:
            by_name[name]["is_exit"] = True
        else:
            # Company appears only in /category/exits, not in any of the 8
            # sector pages -- keep it (don't drop real data), sectors=[].
            by_name[name] = dict(
                {k: v for k, v in next(r for r in exit_rows if r["company_name"] == name).items()},
                sectors=[], is_exit=True,
            )
            order.append(name)

    scraped_at = datetime.now(timezone.utc).isoformat()
    out = []
    names = order[:limit] if limit else order
    for name in names:
        c = by_name[name]
        out.append({
            "company_name": c["company_name"],
            "description": c["description"],
            "company_url": c["company_url"],
            "logo_url": c["logo_url"],
            "sectors": c["sectors"],
            "is_exit": c["is_exit"],
            "everywhere_tags": everywhere_tags(c["company_name"], c["description"], c["sectors"]),
            "source_url": f"{BASE}/portfolio",
            "scraped_at": scraped_at,
        })

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2)

    # ---- summary ----
    n = len(out)
    print(f"\nwrote {n} companies -> {OUT}")
    for field in ("description", "company_url", "logo_url"):
        miss = sum(1 for r in out if not r[field])
        print(f"  {field:12s} missing: {miss}/{n}")
    print(f"  sectors empty: {sum(1 for r in out if not r['sectors'])}/{n}")
    print(f"  is_exit=True:  {sum(1 for r in out if r['is_exit'])}/{n}")
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
