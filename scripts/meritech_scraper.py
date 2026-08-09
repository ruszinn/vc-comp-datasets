#!/usr/bin/env python3
"""
Meritech Capital portfolio scraper -> meritech_companies.json

Scrapes Meritech Capital's portfolio (https://www.meritechcapital.com/companies)
into a JSON file. The site is a **Framer** build (not Webflow/WordPress). The
`/companies` page's HTML is mostly header/footer chrome -- the portfolio grid
itself is rendered client-side from a Framer-native CMS collection. Framer
serializes that CMS query's result into a `<script type="framer/handover"
id="__framer__handoverData">` JSON blob baked into the static page, so a plain
GET (no browser/JS execution) is enough to recover the full dataset.

## The handover-data format
The blob is a flat JSON array acting as a reference table: any bare integer
found while walking the structure is an index into this same array (a
JS-structured-clone-style encoding), so values are deduplicated/shared. The
root element (`data[0]`) points at a `["Map", queryStringIdx, rowIndexListIdx]`
pair; `rowIndexListIdx` resolves to a list of per-company row-object indices.
`decode_handover()` below walks the array and recursively resolves every
integer reference into a plain nested dict/list.

## What the CMS query actually selects (verified in the embedded query string)
Only 4 fields are selected for this collection -- confirmed by reading the
query's own `"select": [...]` field-id list in the payload -- so there is no
description/sector/founder/stage/status data hiding elsewhere in the page to
mine (checked: the only visible text on `/companies` is nav chrome, "Our
Companies", and the tagline "Market leaders in markets that matter"; no
`__NEXT_DATA__`/wp-json; no per-company detail pages -- `sitemap.xml` lists
only `/`, `/team`, `/companies`, `/legal`, plus `/team/<person>` bios):
  - `SouvbGaCw` -> `{type: "responsiveimage", value: {src, srcSet, alt, ...}}` = logo
  - `hnufjAm17` -> `{type: "link", value: <url>}` = external company website
    (null for 2 companies -- Fortinet, Kalshi -- a legit empty, not a parse bug)
  - `HWeRpXE_0` -> `{type: "string", value: <slug>}` = the company identifier,
    e.g. "true-anomaly", "david-ai" -- lowercase/hyphenated, this is the ONLY
    name Meritech's own site publishes in structured form
  - `id`       -> Framer's internal CMS row id (opaque, not a Meritech URL)

`company_name` prefers the logo's `alt` text over a naive title-cased slug
**only when the alt is a same-company recasing of the slug** (i.e.
alphanumerics match after lowercasing: `david-ai` == `David AI`) -- this
recovers a couple of correctly-branded names (David AI, UiPath) straight from
Meritech's own markup without guessing. The `alt` is blank for about half the
rows and, critically, for one row ("flock-safety") it carries a clearly
stale/wrong value ("floqast", a different company's name) -- caught precisely
because it does NOT match the slug, so it's rejected and we fall back to a
plain title-cased slug there. This is a formatting transform of published
data, not an invented value; the raw `slug` is kept verbatim alongside
`company_name` so nothing is lost, and a few brand stylizations a generic
title-case can't infer (e.g. "n8n", "jfrog" -> would want "JFrog") are left
as the title-cased slug rather than guessed from outside knowledge.

No pagination: the query has no `limit`/`offset` and returns all rows matching
its `where` filter (a boolean CMS field, presumably "show on companies page")
in one response -- 48 companies as of this scrape.

requirements:
    pip install requests   (no beautifulsoup4 needed -- data comes from an
    embedded JSON blob, not HTML tag soup)

usage:
    python3 meritech_scraper.py            # writes ../data/meritech_companies.json
    python3 meritech_scraper.py --limit 10 # only the first ~10 for a test run
"""

import json
import os
import re
import sys
import time
from datetime import datetime, timezone

import requests

URL = "https://www.meritechcapital.com/companies"
SOURCE_URL = "https://www.meritechcapital.com/companies"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "data", "meritech_companies.json")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}
TIMEOUT = 45
RETRIES = 3
SLEEP = 1.0

# everywhere_tags keyword classifier -- copied from menlo_scraper.py / iconiq_scraper.py.
# Meritech's site publishes no sector data at all, so tags are keyword-derived
# from name + (empty) description only; AI alone is never a category.
KEYWORD_TAGS = [
    ("BioTech", ["biotech", "drug", "therapeut", "oncolog", "cancer", "tumor", "genomic", "genome",
                 "molecul", "antibod", "protein", "vaccine", "clinical-stage", "medicine", "opioid", "life science",
                 "synthetic biology", "biolog"]),
    ("Health", ["healthcare", "health care", "patient", "clinic", "medical", "mental health", "telehealth",
                "health system", "health record", "diagnos", "surgical", "doctor", "hospital", "pharmac", "therapy",
                "evidence-based medicine", "clinical evidence"]),
    ("Cybersecurity", ["cybersecurity", "security", "secure", "privacy", "fraud", "phishing", "malware",
                       "ransomware", "endpoint", "zero trust", "vulnerab", "authentication", "threat", "defense system",
                       "identity", "information protection", "surveillance", "safety platform"]),
    ("FinTech / Insurance", ["fintech", "payment", "bank", "lending", "loan", "insurance", "credit", "trading",
                             "wallet", "financ", "invoic", "accounting", "payroll", "treasury", "billing", "pricing platform",
                             "rebate", " tax", "audit", "money management", "robo-advisor", "brokerage", "spend management",
                             "capital markets", "investing", "claims", "prediction market", "expense management"]),
    ("Web3 / Crypto", ["crypto", "blockchain", "web3", "token", "on-chain", "ethereum", "bitcoin", "decentral", "stablecoin", "nft"]),
    ("Gaming / Media / Entertainment", ["game", "gaming", "music", "video", "creator", "content", "publish",
                                        "entertain", "newsletter", "podcast", "film", "streaming", "social media", "media platform"]),
    ("Dev Tools / Cloud", ["developer", " api ", "apis", "api platform", "infrastructure", "database", "cloud",
                           "open source", "devops", "sdk", "kubernetes", "container", "observability", "deploy", "compute",
                           "storage", "serverless", "inference", "networking", "ethernet", "coding", "codebase", "low-code",
                           "no-code", "source code", "development platform", "incident", " sre", "voicemail", "communications",
                           "llm", "foundation model", "interpretability", "workflow automation", "automation platform",
                           "integration platform", "software delivery"]),
    ("Data & Analytics", ["analytics", "business intelligence", "data platform", "data warehouse", "data lake",
                          "data pipeline", "insights", "dashboard", "experimentation", "decision intelligence", "data quality",
                          "analyz", "data curation", "quality management", "relationship intelligence", "planning platform"]),
    ("Future of Work", ["workforce", "hiring", "recruit", "employee", "productivity", "collaborat", "talent",
                        "workplace", "human resources", " hr ", "learning platform", "customer success", "customer service",
                        "customer support", "presales", " sales ", "onboarding", "workflow", "saas management", "ai assistant",
                        "project management", "helpdesk", "support platform", "legal workflow"]),
    ("Transportation / Mobility", ["mobility", "vehicle", "transport", "autonomous", "fleet", "driving", "aviation",
                                   "aircraft", "electric vehicle", "scooter", " bike", "boat", "watercraft", "rideshar", "travel"]),
    ("Logistics / Supply Chain", ["logistics", "supply chain", "supply and demand", "freight", "warehouse",
                                  "delivery", "procurement", "inventory", "fulfillment", "shipping"]),
    ("PropTech", ["real estate", "property", "housing", "mortgage", "rental", "construction", "tenant"]),
    ("CPG", ["beverage", "snack", "consumer packaged", "beauty", "cosmetic", "apparel", "grocery", "skincare",
             "eyewear", "glasses", "footwear", "pet sitter", "pet ", "restaurant"]),
    ("Climate / Sustainability", ["climate", "carbon", "renewable", "solar", "battery", "sustainab", "emission",
                                  "clean energy", "ev charging", "electrif", "energy"]),
    ("RegTech/Gov/Legal", ["legal", "compliance", "government", "regulat", "law firm", "attorney", "risk services",
                           "lawsuit", "lawyer"]),
    ("Deeptech / Robotics / AR/VR", ["robot", "hardware", "semiconductor", "chip", "drone", "aerospace",
                                     "augmented reality", "virtual reality", "satellite", "quantum", "sensor", "rfid", "wifi",
                                     "space", "rocket", "launch vehicle", "optics", "defense", "spacecraft", "orbital"]),
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
    s = re.sub(r"\s+", " ", str(s)).strip()
    return s or None


def decode_handover(html):
    """Extract & recursively resolve the `__framer__handoverData` reference-table
    JSON blob. Returns the raw `data` array plus a `deep_resolve(i)` closure."""
    marker = 'id="__framer__handoverData">'
    start = html.find(marker)
    if start == -1:
        return None, None
    start += len(marker)
    end = html.find("</script>", start)
    if end == -1:
        return None, None
    payload = html[start:end]
    data = json.loads(payload)

    def deep_resolve(i, depth=0, seen=None):
        if seen is None:
            seen = set()
        if depth > 25 or i in seen or not isinstance(i, int) or i >= len(data):
            return None
        val = data[i]
        seen = seen | {i}
        if isinstance(val, list):
            if val and val[0] == "Map":
                return {"__map__": [deep_resolve(v, depth + 1, seen) for v in val[1:]]}
            return [deep_resolve(v, depth + 1, seen) if isinstance(v, int) else v for v in val]
        if isinstance(val, dict):
            return {k: (deep_resolve(v, depth + 1, seen) if isinstance(v, int) else v) for k, v in val.items()}
        return val

    return data, deep_resolve


def slug_to_title(slug):
    """Title-case a hyphenated slug ('true-anomaly' -> 'True Anomaly'). Formatting
    transform of the site's own published slug -- not an invented value."""
    if not slug:
        return None
    parts = slug.split("-")
    # keep short all-caps-ish acronym parts as-is is not knowable generically;
    # simple title-case per word is the non-fabricating default.
    return " ".join(p[:1].upper() + p[1:] if p else p for p in parts)


def alphanum(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def best_name(slug, alt):
    """Prefer the logo `alt` text as the display name ONLY when it's a same-company
    recasing of the slug (alphanumerics match once lowercased) -- this recovers
    correctly-branded names (e.g. "UiPath") without trusting stale/mismatched alt
    text (e.g. "flock-safety" logo alt is "floqast", a different company)."""
    title = slug_to_title(slug)
    if alt and alphanum(alt) == alphanum(slug) and alt != slug:
        return alt
    return title


def everywhere_tags(name, description):
    text = f"{name or ''} {description or ''}".lower()
    tags = []
    for tag, kws in KEYWORD_TAGS:
        if any(kw in text for kw in kws) and tag not in tags:
            tags.append(tag)
    return tags[:4]


def parse(html):
    data, deep_resolve = decode_handover(html)
    if data is None:
        raise SystemExit("FATAL: could not find __framer__handoverData in the page")

    root = data[0]
    root_map = deep_resolve(root["0"])
    if not (isinstance(root_map, dict) and root_map.get("__map__")):
        raise SystemExit("FATAL: unexpected handover-data shape at root")
    row_index_list = root_map["__map__"][1]
    if not isinstance(row_index_list, list):
        raise SystemExit("FATAL: unexpected row-index-list shape")

    companies = []
    for row in row_index_list:
        if not isinstance(row, dict):
            continue
        img = (row.get("SouvbGaCw") or {}).get("value") or {}
        logo_url = clean(img.get("src"))
        alt = clean(img.get("alt"))

        link = row.get("hnufjAm17") or {}
        company_url = clean(link.get("value")) if link.get("type") == "link" else None

        name_field = row.get("HWeRpXE_0") or {}
        slug = clean(name_field.get("value"))
        if not slug:
            continue

        row_id = clean((row.get("id") or {}).get("value"))
        name = best_name(slug, alt)

        companies.append({
            "company_name": name,
            "slug": slug,
            "description": None,   # not exposed anywhere on the site (checked)
            "company_url": company_url,
            "logo_url": logo_url,
            "sectors": [],         # not exposed anywhere on the site (checked)
            "everywhere_tags": everywhere_tags(name, None),
            "source_url": SOURCE_URL,
            "_row_id": row_id,     # Framer's internal CMS row id, kept for traceability
            "_logo_alt": alt,      # raw alt text, kept only for spot-checking (see module docstring)
        })
    return companies


def main():
    limit = int(sys.argv[sys.argv.index("--limit") + 1]) if "--limit" in sys.argv else None

    print(f"Fetching {URL}")
    html = get(URL)
    time.sleep(SLEEP)
    companies = parse(html)

    seen, out = set(), []
    for c in companies:
        k = c["slug"]
        if k in seen:
            print(f"  ! duplicate slug '{k}' -- keeping first", file=sys.stderr)
            continue
        seen.add(k)
        out.append(c)
        if limit and len(out) >= limit:
            break

    out.sort(key=lambda o: o["company_name"].lower())

    scraped_at = datetime.now(timezone.utc).isoformat()
    for o in out:
        o["scraped_at"] = scraped_at
        # drop internal-only debugging fields from the shipped schema
        o.pop("_row_id", None)
        o.pop("_logo_alt", None)

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    from collections import Counter
    n = len(out)
    by_tag = Counter(t for o in out for t in o["everywhere_tags"])
    print(f"\nWrote {n} companies -> {OUT}")
    print(f"  with website: {sum(1 for o in out if o['company_url'])}/{n}")
    print(f"  with logo:    {sum(1 for o in out if o['logo_url'])}/{n}")
    untagged = [o["company_name"] for o in out if not o["everywhere_tags"]]
    print(f"  untagged:     {len(untagged)}/{n}" + (f" -> {untagged}" if untagged else ""))
    print("By everywhere_tag:")
    for t, c in by_tag.most_common():
        print(f"  {c:>4}  {t}")


if __name__ == "__main__":
    main()
