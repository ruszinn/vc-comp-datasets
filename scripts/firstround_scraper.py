#!/usr/bin/env python3
"""
First Round Capital portfolio scraper -> firstround_companies.json

Scrapes First Round's companies page (https://firstround.com/companies) into a
JSON file. The site is a Next.js (App Router) app backed by Sanity CMS; the
page is server-rendered but ships its data as a React Server Components
("RSC") streaming payload embedded in `<script>self.__next_f.push([1,"..."])`
tags rather than a classic `__NEXT_DATA__` blob or a REST API. One of those
chunks contains a `companyList` CMS section with the FULL company array (190
companies, one request) plus the section's own lookup tables for
`companyCategories` (7 sector tags: AI, Consumer, DevTools & Infra, Enterprise,
Fintech, Hardware, Healthcare) and `companyLocations` (10 HQ regions) — no
pagination, no separate API call needed.

Per-company detail pages (firstround.com/companies/<slug>) exist for some
companies but 404 unpredictably (many acquired companies have no live detail
page), and the pages that do resolve don't carry any structured field beyond
what's already in the listing payload (no acquirer/ticker/exit-year anywhere
on the site — checked both the listing prose and detail-page body sections).
So this scraper is single-request: fetch the listing page once, parse the
embedded RSC chunk.

requirements:
    pip install requests

usage:
    python3 firstround_scraper.py               # writes firstround_companies.json
    python3 firstround_scraper.py --limit 20     # quick test run
"""

import json
import os
import re
import sys
import time
from datetime import datetime, timezone

import requests

URL = "https://firstround.com/companies"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "data", "firstround_companies.json")
SOURCE_URL = "https://firstround.com/companies"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}
TIMEOUT = 45
RETRIES = 3

# First Round's own 7 category tags -> everywhere_tags taxonomy. "AI" and
# "Enterprise" have no single clean mapping (AI alone isn't a category per
# CLAUDE.md; "Enterprise" spans dev-tools/work/data/security) so they're left
# to the keyword classifier below.
SECTOR_TAG_MAP = {
    "consumer": "Consumer",
    "devtools-and-infra": "Dev Tools / Cloud",
    "fintech": "FinTech / Insurance",
    "healthcare": "Health",
    "hardware": "Deeptech / Robotics / AR/VR",
}

# everywhere_tags keyword classifier (substrings, lowercased) -- copied from
# menlo_scraper.py / rre_scraper.py. Used as a fallback / refinement on top of
# First Round's own category tags (esp. for "AI" and "Enterprise", which don't
# map to a single tag).
KEYWORD_TAGS = [
    ("BioTech", ["biotech", "drug", "therapeut", "oncolog", "cancer", "tumor", "genomic", "genome",
                 "molecul", "antibod", "protein", "vaccine", "clinical-stage", "medicine", "opioid", "life science",
                 "synthetic biology", "biolog"]),
    ("Health", ["healthcare", "health care", "patient", "clinic", "medical", "mental health", "telehealth",
                "health system", "health record", "diagnos", "surgical", "doctor", "hospital", "pharmac", "therapy",
                "disability insurance"]),
    ("Cybersecurity", ["cybersecurity", "security", "secure", "privacy", "fraud", "phishing", "malware",
                       "ransomware", "endpoint", "zero trust", "vulnerab", "authentication", "threat", "identity"]),
    ("FinTech / Insurance", ["fintech", "payment", "bank", "lending", "loan", "insurance", "credit", "trading",
                             "wallet", "financ", "invoic", "accounting", "payroll", "treasury", "billing",
                             "rebate", " tax", "audit", "money management", "robo-advisor", "brokerage",
                             "spend management", "credit card"]),
    ("Web3 / Crypto", ["crypto", "blockchain", "web3", "token", "on-chain", "ethereum", "bitcoin", "decentral", "stablecoin", "nft"]),
    ("Gaming / Media / Entertainment", ["game", "gaming", "music", "video", "creator", "content", "publish",
                                        "entertain", "newsletter", "podcast", "film", "streaming", "social media", "media platform",
                                        "code while playing"]),
    ("Dev Tools / Cloud", ["developer", " api ", "apis", "api platform", "infrastructure", "database", "cloud",
                           "open source", "devops", "sdk", "kubernetes", "container", "observability", "deploy", "compute",
                           "storage", "serverless", "inference", "networking", "coding", "codebase", "low-code",
                           "no-code", "source code", "development platform", "incident", " sre",
                           "llm", "foundation model", "interpretability", "real-time media buying", "ad platform",
                           "advertisers"]),
    ("Data & Analytics", ["analytics", "business intelligence", "data platform", "data warehouse", "data lake",
                          "data pipeline", "insights", "dashboard", "experimentation", "decision intelligence", "data quality",
                          "analyz", "total intelligence", "contact and company"]),
    ("Future of Work", ["workforce", "hiring", "recruit", "employee", "productivity", "collaboration", "talent",
                        "workplace", "human resources", " hr ", "learning platform", "customer success", "customer service",
                        "customer support", "presales", " sales ", "onboarding", "workflow", "saas management",
                        "small business"]),
    ("Transportation / Mobility", ["mobility", "vehicle", "transport", "autonomous", "fleet", "driving", "aviation",
                                   "aircraft", "electric vehicle", "scooter", " bike", "boat", "watercraft", "rideshar",
                                   "get a ride"]),
    ("Logistics / Supply Chain", ["logistics", "supply chain", "supply and demand", "freight", "warehouse",
                                  "delivery", "procurement", "inventory", "fulfillment", "shipping"]),
    ("PropTech", ["real estate", "property", "housing", "mortgage", "rental", "construction", "tenant", "wifi at home", "enterprise-grade wifi"]),
    ("CPG", ["beverage", "snack", "consumer packaged", "beauty", "cosmetic", "apparel", "grocery", "skincare",
             "eyewear", "glasses", "footwear", "pet sitter", "pet "]),
    ("Climate / Sustainability", ["climate", "carbon", "renewable", "solar", "battery", "sustainab", "emission",
                                  "clean energy", "ev charging", "electrif", "image the entire globe", "space to help life"]),
    ("RegTech/Gov/Legal", ["legal", "compliance", "government", "regulat", "law firm", "attorney"]),
    ("Deeptech / Robotics / AR/VR", ["robot", "hardware", "semiconductor", "chip", "drone", "aerospace",
                                     "augmented reality", "virtual reality", "satellite", "quantum", "sensor", "rfid"]),
    ("Consumer", ["marketplace", "consumer", "shopping", "social network", "community", "app for", "app that",
                  "ecommerce", "e-commerce", "subscription", "retailer", "universit", "student", "education",
                  "learning", "fashion", "style companion"]),
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


def read_balanced(s, i):
    """s[i] is '[' or '{'; return the index just past its matching close bracket,
    correctly skipping over bracket-look-alike characters inside JSON strings."""
    open_ch = s[i]
    close_ch = "]" if open_ch == "[" else "}"
    depth = 0
    j = i
    in_str = False
    esc = False
    while True:
        ch = s[j]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        else:
            if ch == '"':
                in_str = True
            elif ch == open_ch:
                depth += 1
            elif ch == close_ch:
                depth -= 1
                if depth == 0:
                    return j + 1
        j += 1


def extract_rsc_chunks(html):
    """Return the list of RSC payload strings from self.__next_f.push([1,"..."])
    tags, with the JS string-escaping (\\n, \\", \\uXXXX, etc.) undone."""
    raw_chunks = re.findall(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)</script>', html, re.S)
    return [c.encode("utf-8").decode("unicode_escape") for c in raw_chunks]


def find_company_list_section(chunks):
    """Locate the RSC chunk holding the companyList CMS section and parse out
    its `companies`, `companyCategories`, `companyLocations` arrays."""
    for c in chunks:
        start = c.find('"companies":[')
        if start == -1:
            continue
        i = start + len('"companies":')
        end = read_balanced(c, i)
        try:
            companies = json.loads(c[i:end])
        except json.JSONDecodeError:
            continue
        if not isinstance(companies, list) or not companies:
            continue
        # Sanity-check this is really the company list (has the fields we expect)
        if not all(isinstance(x, dict) and "slug" in x and "title" in x for x in companies[:3]):
            continue

        tail = c[end:end + 6000]
        categories, locations = [], []
        cat_i = tail.find('"companyCategories":[')
        if cat_i != -1:
            j = cat_i + len('"companyCategories":')
            categories = json.loads(tail[j:read_balanced(tail, j)])
        loc_i = tail.find('"companyLocations":[')
        if loc_i != -1:
            j = loc_i + len('"companyLocations":')
            locations = json.loads(tail[j:read_balanced(tail, j)])
        return companies, categories, locations
    return None, None, None


def plain_text(portable_text):
    """Flatten a Sanity portable-text block array into plain text."""
    if not portable_text:
        return None
    out = []
    for block in portable_text:
        if not isinstance(block, dict):
            continue
        for child in block.get("children", []) or []:
            t = child.get("text", "")
            if t:
                out.append(t)
    return clean("".join(out))


def everywhere_tags(name, description, sector_slugs):
    tags = []
    for slug in sector_slugs:
        mapped = SECTOR_TAG_MAP.get(slug)
        if mapped and mapped not in tags:
            tags.append(mapped)
    text = f"{name or ''} {description or ''}".lower()
    for tag, kws in KEYWORD_TAGS:
        if tag in tags:
            continue
        if any(kw in text for kw in kws):
            tags.append(tag)
        if len(tags) >= 4:
            break
    return tags[:4]


STATUS_MAP = {
    "acquired": "Acquired",
    "ipo": "Public",
    None: "Active",
}


def parse(html):
    chunks = extract_rsc_chunks(html)
    companies_raw, categories_raw, locations_raw = find_company_list_section(chunks)
    if not companies_raw:
        raise SystemExit("FATAL: could not locate the companyList RSC payload on the page "
                          "(site structure may have changed)")

    cat_lookup = {c["id"]: c for c in categories_raw or []}
    loc_lookup = {l["id"]: l for l in locations_raw or []}

    out = []
    for comp in companies_raw:
        name = clean(comp.get("title"))
        if not name:
            continue
        slug = comp.get("slug")
        website = clean(comp.get("website"))
        description = plain_text(comp.get("statement"))
        founders = [clean(f) for f in (comp.get("founders") or []) if clean(f)]
        partners = [clean(p) for p in (comp.get("partners") or []) if clean(p)]
        stage = clean(comp.get("initialPartnership"))
        status = STATUS_MAP.get(comp.get("status"), comp.get("status"))

        sector_ids = comp.get("companyCategories") or []
        sector_slugs = [cat_lookup[i]["slug"] for i in sector_ids if i in cat_lookup]
        sectors = [cat_lookup[i]["title"] for i in sector_ids if i in cat_lookup]

        loc_ids = comp.get("companyLocations") or []
        locations = [loc_lookup[i]["title"] for i in loc_ids if i in loc_lookup]

        logo = comp.get("logo") or {}
        logo_url = logo.get("url") if isinstance(logo, dict) else None

        out.append({
            "company_name": name,
            "description": description,
            "company_url": website,
            "company_profile_url": f"https://firstround.com/companies/{slug}" if slug else None,
            "logo_url": logo_url,
            "founders": founders,
            "partners": partners,
            "initial_investment_stage": stage,
            "status": status,
            "sectors": sectors,
            "locations": locations,
            "everywhere_tags": everywhere_tags(name, description, sector_slugs),
            "source_url": SOURCE_URL,
            "scraped_at": None,  # filled in by caller with a single shared timestamp
        })
    return out


def main():
    limit = None
    if "--limit" in sys.argv:
        limit = int(sys.argv[sys.argv.index("--limit") + 1])

    print(f"Fetching {URL}")
    html = get(URL)
    companies = parse(html)

    scraped_at = datetime.now(timezone.utc).isoformat()
    for c in companies:
        c["scraped_at"] = scraped_at

    # de-dupe by normalized name, keep first
    seen, out = set(), []
    for c in companies:
        k = c["company_name"].strip().lower()
        if k in seen:
            print(f"  ! duplicate '{c['company_name']}' — keeping first", file=sys.stderr)
            continue
        seen.add(k)
        out.append(c)
    out.sort(key=lambda o: o["company_name"].lower())

    if limit:
        out = out[:limit]

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    from collections import Counter
    by_status = Counter(o["status"] for o in out)
    by_tag = Counter(t for o in out for t in o["everywhere_tags"])
    print(f"\nWrote {len(out)} companies -> {OUT}")
    print("By status:", dict(by_status))
    print("With founders:", sum(1 for o in out if o["founders"]),
          "| with partners:", sum(1 for o in out if o["partners"]),
          "| with website:", sum(1 for o in out if o["company_url"]),
          "| with logo:", sum(1 for o in out if o["logo_url"]),
          "| with sectors:", sum(1 for o in out if o["sectors"]),
          "| with locations:", sum(1 for o in out if o["locations"]),
          "| untagged:", sum(1 for o in out if not o["everywhere_tags"]))
    print("By everywhere_tag:")
    for t, c in by_tag.most_common():
        print(f"  {c:>4}  {t}")


if __name__ == "__main__":
    main()
