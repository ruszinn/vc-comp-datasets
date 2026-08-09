#!/usr/bin/env python3
"""
Paradigm portfolio scraper -> paradigm_companies.json

Scrapes Paradigm's investments page (https://www.paradigm.xyz/investments) into
a JSON file. The site is a SvelteKit app (server-rendered) with Contentful as
the CMS for images; the investments page itself is **fully server-rendered
static HTML** -- no API, no pagination, no per-company detail page (every
company name links straight out to the company's own external site).

Two sections exist on the page:
  1. The main `<ul class="list">` of `<li class="item is-investment">` --
     104 entries (some CSS-hidden behind an `is-hidden` class used by a
     client-side "show featured only" toggle, but the raw HTML/data is
     identical for hidden vs visible rows). Each row has: name (`h2.title`),
     description (`.col-secondary`, duplicated in `.subtitle`), category
     (`.col-tertiary`, comma-separated, e.g. "DeFi, Infrastructure"), and the
     external company URL directly on the row's `<a href>`.
  2. A curated "featured" grid of `.investment-card` items (12) with a cover
     image (Contentful `images.ctfassets.net` URL) plus name/description/url.
     11 of the 12 overlap by name with the main list; ONE (`Hyperliquid
     (HYPE)`) is NOT in the main list at all, so it's unioned in separately
     (with description + logo from the card, but no `categories` -- the cards
     don't expose that field, so it's legitimately `[]` for that one record).

Two companies are both legitimately named "Harmonic" (different domains,
different descriptions/categories -- a math-reasoning-engine AI startup vs. a
Solana block-builder) -- kept as two separate records, not deduped.

Empty ≠ absent check performed: grepped every description for
acquired/IPO/public/shutdown/merger language -- zero hits. This matches the
firm's own disclosure ("Investment Disclosures" page at
paradigm.xyz/investment-disclosures) stating the list excludes shut-down/
zeroed-out positions and is investor-relations curated, not a full cap-table
history -- so Paradigm genuinely does not publish status/acquirer/exit_year
anywhere (name or prose). The one thing that IS denormalized into the name
suffix is an occasional **crypto token ticker** (`Cosmos (ATOM)`, `Maker
(MKR)`, `Synthetix (SNX)`, `Hyperliquid (HYPE)`) and one former-name note
(`Ventuals (formerly Shadow)`) -- both parsed out of the name into
`token_ticker` / `former_name`, while `company_name` keeps the raw suffix
verbatim (matches the RRE precedent for name-suffix parsing).

requirements:
    pip install requests beautifulsoup4

usage:
    python3 paradigm_scraper.py            # writes ../data/paradigm_companies.json
    python3 paradigm_scraper.py --limit 15 # only the first ~15 for a test run
"""

import json
import os
import re
import socket
import sys
import time
from collections import Counter
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

URL = "https://www.paradigm.xyz/investments"
SOURCE_URL = "https://www.paradigm.xyz/investments"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "data", "paradigm_companies.json")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}
TIMEOUT = 45
RETRIES = 3

# --- Webflow/Contentful-CDN IP-routing workaround --------------------------
# This machine cannot resolve/route some CDN hosts via current DNS-returned
# IPs. If normal DNS resolution fails for a host, retry via the known-good
# legacy IP. This is a fallback only -- normal getaddrinfo is tried first.
_LEGACY_IP_FALLBACKS = {
    "cdn.webflow.com": "75.2.70.75",
}
_real_getaddrinfo = socket.getaddrinfo


def _patched_getaddrinfo(host, *args, **kwargs):
    try:
        return _real_getaddrinfo(host, *args, **kwargs)
    except OSError:
        ip = _LEGACY_IP_FALLBACKS.get(host)
        if ip:
            return _real_getaddrinfo(ip, *args, **kwargs)
        raise


socket.getaddrinfo = _patched_getaddrinfo
# ---------------------------------------------------------------------------

# Paradigm's own 7 portfolio categories -> the 17-tag everywhere_tags taxonomy.
# "AI" is intentionally NOT mapped (AI alone is not a category -- classify by
# the market it serves; left to the keyword fallback below).
SECTOR_TAG_MAP = {
    "DeFi": ["Web3 / Crypto"],
    "Infrastructure": ["Dev Tools / Cloud"],
    "Payments": ["FinTech / Insurance"],
    "Trading & Markets": ["FinTech / Insurance"],
    "Hardware & Defense": ["Deeptech / Robotics / AR/VR"],
    "Consumer": ["Consumer"],
}

# everywhere_tags keyword classifier (substrings, lowercased) -- copied from
# rre_scraper.py / iconiq_scraper.py, with crypto terms weighted first since
# Paradigm is a crypto-native fund.
KEYWORD_TAGS = [
    ("Web3 / Crypto", ["crypto", "blockchain", "web3", "token", "on-chain", "onchain", "ethereum", "bitcoin",
                       "decentral", "stablecoin", "nft", "defi", "solana", "rollup", "layer 2", "layer-2",
                       "smart contract", "dao ", "wallet", "staking", "validator", "l1 ", "l2 ", "protocol"]),
    ("BioTech", ["biotech", "drug", "therapeut", "oncolog", "cancer", "tumor", "genomic", "genome",
                 "molecul", "antibod", "protein", "vaccine", "clinical-stage", "medicine", "life science",
                 "synthetic biology", "biolog"]),
    ("Health", ["healthcare", "health care", "patient", "clinic", "medical", "mental health", "telehealth",
                "health system", "health record", "diagnos", "surgical", "doctor", "hospital", "pharmac", "therapy"]),
    ("Cybersecurity", ["cybersecurity", "security platform", "secure", "privacy", "fraud", "phishing", "malware",
                       "ransomware", "endpoint", "zero trust", "vulnerab", "authentication", "threat", "identity"]),
    ("FinTech / Insurance", ["fintech", "payment", "bank", "lending", "loan", "insurance", "credit", "trading",
                             "financ", "invoic", "accounting", "payroll", "treasury", "billing",
                             "capital markets", "brokerage", "exchange", "asset manage", "hedge fund", "prediction market",
                             "derivatives"]),
    ("Gaming / Media / Entertainment", ["game", "gaming", "music", "video", "creator", "content", "publish",
                                        "entertain", "newsletter", "podcast", "film", "streaming", "social media", "media platform"]),
    ("Dev Tools / Cloud", ["developer", " api ", "apis", "api platform", "infrastructure", "database", "cloud",
                           "open source", "devops", "sdk", "kubernetes", "container", "observability", "deploy", "compute",
                           "storage", "serverless", "inference", "networking", "node ", "rpc ", "indexer",
                           "block builder", "block explorer", "llm", "foundation model", "reasoning engine",
                           "developer tool", "tooling"]),
    ("Data & Analytics", ["analytics", "business intelligence", "data platform", "data warehouse", "data lake",
                          "data pipeline", "insights", "dashboard", "risk engine", "data quality", "analyz",
                          "data intelligence", "on-chain data", "onchain data", "market data"]),
    ("Future of Work", ["workforce", "hiring", "recruit", "employee", "productivity", "collaborat", "talent",
                        "workplace", "human resources", "customer success", "customer service", "customer support",
                        "workflow", "project management", "coordination"]),
    ("Transportation / Mobility", ["mobility", "vehicle", "transport", "autonomous", "fleet", "driving", "aviation",
                                   "aircraft", "electric vehicle", "scooter", "rideshar", "travel"]),
    ("Logistics / Supply Chain", ["logistics", "supply chain", "freight", "warehouse", "delivery", "procurement",
                                  "inventory", "fulfillment", "shipping", "manufactur"]),
    ("PropTech", ["real estate", "property", "housing", "mortgage", "rental", "construction", "tenant"]),
    ("CPG", ["beverage", "snack", "consumer packaged", "beauty", "cosmetic", "apparel", "grocery", "skincare",
             "eyewear", "footwear"]),
    ("Climate / Sustainability", ["climate", "carbon", "renewable", "solar", "battery", "sustainab", "emission",
                                  "clean energy", "energy grid", "power grid"]),
    ("RegTech/Gov/Legal", ["legal", "compliance", "government", "regulat", "law firm", "attorney", "tax filing"]),
    ("Deeptech / Robotics / AR/VR", ["robot", "hardware", "semiconductor", "chip", "drone", "aerospace", "defense",
                                     "augmented reality", "virtual reality", "satellite", "quantum", "sensor",
                                     "space", "rocket", "launch vehicle", "manufactur", "3d print", "laser cutting",
                                     "national security"]),
    ("Consumer", ["marketplace", "consumer app", "shopping", "social network", "community", "app for", "app that",
                  "ecommerce", "e-commerce", "subscription", "social app"]),
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


NAME_SUFFIX_RE = re.compile(r"\s*\(([^()]*)\)\s*$")


def parse_name_suffix(raw_name):
    """Paradigm occasionally encodes a crypto token ticker or a former-name
    note in a trailing "(...)" suffix on the display name, e.g. "Cosmos
    (ATOM)", "Maker (MKR)", "Ventuals (formerly Shadow)". Returns
    (token_ticker, former_name); company_name itself keeps the suffix verbatim
    (matches the RRE precedent -- structured fields are parsed OUT, not
    stripped from the display name)."""
    m = NAME_SUFFIX_RE.search(raw_name or "")
    if not m:
        return None, None
    inner = m.group(1).strip()
    fm = re.match(r"formerly\s+(.+)", inner, re.I)
    if fm:
        return None, clean(fm.group(1))
    # token ticker: short all-caps alnum token, e.g. ATOM, MKR, SNX, HYPE
    if re.fullmatch(r"[A-Z][A-Z0-9]{1,9}", inner):
        return inner, None
    return None, None


def everywhere_tags(name, description, categories):
    """Paradigm categories first (mapped via SECTOR_TAG_MAP), then keyword
    fallback on name + description to add/refine. Order most->least relevant,
    cap at 4."""
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


def parse_list_item(li):
    h2 = li.select_one("h2.title")
    name = clean(h2.get_text()) if h2 else None
    if not name:
        return None

    a = li.select_one("a.item-link")
    company_url = clean(a.get("href")) if a and a.get("href") else None

    desc_el = li.select_one(".col-secondary")
    description = clean(desc_el.get_text()) if desc_el else None

    cat_el = li.select_one(".col-tertiary")
    categories = []
    if cat_el:
        for part in clean(cat_el.get_text()).split(","):
            v = part.strip()
            if v and v not in categories:
                categories.append(v)

    token_ticker, former_name = parse_name_suffix(name)

    return {
        "company_name": name,
        "description": description,
        "company_url": company_url,
        "logo_url": None,  # main list rows carry no image; only the featured cards do
        "categories": categories,
        "token_ticker": token_ticker,
        "former_name": former_name,
        "everywhere_tags": everywhere_tags(name, description, categories),
        "source_url": SOURCE_URL,
    }


def parse_featured_card(card):
    name_el = card.select_one(".investment-name")
    name = clean(name_el.get_text()) if name_el else None
    if not name:
        return None

    a = card.select_one("a.investment-link")
    company_url = clean(a.get("href")) if a and a.get("href") else None

    desc_el = card.select_one(".investment-description")
    description = clean(desc_el.get_text()) if desc_el else None

    img = card.select_one("img")
    logo_url = clean(img.get("src")) if img and img.get("src") else None

    token_ticker, former_name = parse_name_suffix(name)

    return {
        "company_name": name,
        "description": description,
        "company_url": company_url,
        "logo_url": logo_url,
        "categories": [],  # featured cards expose no category field
        "token_ticker": token_ticker,
        "former_name": former_name,
        "everywhere_tags": everywhere_tags(name, description, []),
        "source_url": SOURCE_URL,
    }


def main():
    limit = int(sys.argv[sys.argv.index("--limit") + 1]) if "--limit" in sys.argv else None

    soup = BeautifulSoup(get(URL), "html.parser")

    list_items = soup.select("li.item.is-investment")
    seen_names = set()
    out = []
    for li in list_items:
        rec = parse_list_item(li)
        if not rec:
            continue
        # "Harmonic" appears twice legitimately (two different companies with
        # different URLs/descriptions) -- dedupe on (name, company_url), not
        # name alone, so both are kept.
        key = (rec["company_name"].strip().lower(), rec["company_url"])
        if key in seen_names:
            continue
        seen_names.add(key)
        out.append(rec)

    # Union in featured cards that aren't already present by name (case-
    # insensitive on the base name before any "(...)" suffix isn't needed --
    # Paradigm keeps the same "(TICKER)" spelling in both places).
    list_names_lower = {r["company_name"].strip().lower() for r in out}
    cards = soup.select(".investment-card")
    for c in cards:
        rec = parse_featured_card(c)
        if not rec:
            continue
        if rec["company_name"].strip().lower() in list_names_lower:
            continue
        out.append(rec)
        list_names_lower.add(rec["company_name"].strip().lower())

    if limit:
        out = out[:limit]

    scraped_at = datetime.now(timezone.utc).isoformat()
    for r in out:
        r["scraped_at"] = scraped_at

    out.sort(key=lambda r: r["company_name"].lower())

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2)

    # ---- summary ----
    n = len(out)
    print(f"\nWrote {n} companies -> {OUT}")
    for field in ("description", "company_url", "logo_url", "token_ticker", "former_name"):
        present = sum(1 for r in out if r[field])
        print(f"  {field:14s} present: {present}/{n}")
    print(f"  categories empty: {sum(1 for r in out if not r['categories'])}/{n}")
    untagged = [r["company_name"] for r in out if not r["everywhere_tags"]]
    print(f"  untagged: {len(untagged)}/{n}" + (f" -> {untagged}" if untagged else ""))

    by_cat = Counter(c for r in out for c in r["categories"])
    print("By Paradigm category:")
    for t, c in by_cat.most_common():
        print(f"  {c:>4}  {t}")

    by_tag = Counter(t for r in out for t in r["everywhere_tags"])
    print("By everywhere_tag:")
    for t, c in by_tag.most_common():
        print(f"  {c:>4}  {t}")


if __name__ == "__main__":
    main()
