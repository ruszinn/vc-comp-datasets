#!/usr/bin/env python3
"""
General Atlantic portfolio scraper -> generalatlantic_companies.json

generalatlantic.com/portfolio/ redirects to https://www.generalatlantic.com/investments/,
a WordPress page whose grid (`.investments-item`) is server-rendered, 24 companies per
page, paginated via a `?pg=N` query param (a "Load More" button reads `data-page`).
A page's own inner HTML also honors THREE server-side filters, combinable with `pg`:

    ?type=<sector-slug>        (consumer, energy-transition, financial-services,
                                 healthcare, life-sciences, technology, infrastructure)
    ?region=<Region Name>       (United States, EMEA, India, Southeast Asia,
                                 Latin America, China)
    ?status=Current|Past

Each `.investments-item` card has: company name (only in the logo `<img alt>`), a
description paragraph (optionally with a "View Site" link to the external company
site, an inline `<button class="tooltip-btn">` "Disclaimer" tooltip with extra GA
investment-lifecycle prose, and a leading "Acquired by X, ..." sentence for realized
M&A exits), a `practice` (sector), `region`, `years` (year invested), and an `exit`
(year GA exited the position -- rendered as the Unix-epoch year "1970" as a null
placeholder for companies GA still holds).

The unfiltered/default listing is the UNION of status=Current + status=Past, but
does NOT expose status per item (no visible label), so this script crawls the
`status=Current` and `status=Past` filtered listings separately (paginating each
with `?pg=N`) and tags every record with its status from filter membership --
verified to partition cleanly (0 name overlap, counts sum to the unfiltered total:
236 Current + 170 Past = 406).

"Infrastructure" is a special case: selecting that sector client-side swaps the grid
for a single "View All Actis Investments" link to https://www.act.is/ (General
Atlantic's separate infrastructure-investing affiliate/site) -- so no infrastructure
investments are actually listed here; that's a distinct portfolio GA itself doesn't
publish on this domain, not a scraper gap.

requirements:
    pip install requests beautifulsoup4

usage:
    python3 generalatlantic_scraper.py                # full portfolio (~15 requests)
    python3 generalatlantic_scraper.py --limit 20      # only the first 20 companies (testing)
"""

import json
import os
import re
import sys
import time
from collections import Counter
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

BASE = "https://www.generalatlantic.com"
PORTFOLIO_URL = BASE + "/portfolio/"          # redirects to /investments/
SOURCE_URL = BASE + "/investments/"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "data", "generalatlantic_companies.json")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}
TIMEOUT = 30
RETRIES = 3
SLEEP = 0.4

EPOCH_YEAR = "1970"   # GA's null placeholder for "no exit year"

# everywhere_tags keyword classifier (fallback for when GA's own `practice` sector
# doesn't map cleanly, e.g. "Technology" spans many of the 17 tags on its own).
KEYWORD_TAGS = [
    ("BioTech", ["biotech", "drug", "therapeut", "oncolog", "cancer", "tumor", "genomic", "genome",
                 "molecul", "antibod", "protein", "vaccine", "clinical-stage", "clinical stage",
                 "biopharma", "life science", "biolog", "pharma"]),
    ("Health", ["healthcare", "health care", "patient", "clinic", "medical", "mental health", "telehealth",
                "health system", "health record", "diagnos", "surgical", "doctor", "hospital", "medicare",
                "therapy"]),
    ("Cybersecurity", ["cybersecurity", "security", "secure", "privacy", "fraud", "phishing", "malware",
                       "ransomware", "endpoint", "zero trust", "vulnerab", "authentication", "threat", "identity"]),
    ("FinTech / Insurance", ["fintech", "payment", "bank", "lending", "loan", "insurance", "credit", "trading",
                             "wallet", "financ", "invoic", "accounting", "payroll", "treasury", "billing",
                             "brokerage", "asset management", "wealth management", "tax ", "retirement plan"]),
    ("Web3 / Crypto", ["crypto", "blockchain", "web3", "token", "on-chain", "ethereum", "bitcoin", "decentral", "stablecoin", "nft"]),
    ("Gaming / Media / Entertainment", ["game", "gaming", "music", "video", "creator", "content", "publish",
                                        "entertain", "newsletter", "podcast", "film", "streaming", "social media",
                                        "media platform", "digital media"]),
    ("Dev Tools / Cloud", ["developer", " api ", "apis", "api platform", "infrastructure", "database", "cloud",
                           "open source", "devops", "sdk", "kubernetes", "container", "observability", "deploy",
                           "compute", "storage", "serverless", "networking", "software firm", "software company",
                           "software provider", "saas", "enterprise software", "it services", "observability"]),
    ("Data & Analytics", ["analytics", "business intelligence", "data platform", "data warehouse", "data lake",
                          "data pipeline", "insights", "dashboard", "data complexity", "machine learning",
                          "predictive", "artificial intelligence"]),
    ("Future of Work", ["workforce", "hiring", "recruit", "employee", "productivity", "collaboration", "talent",
                        "workplace", "human resources", "learning platform", "customer success", "customer service",
                        "customer support", "onboarding", "workflow", "crm", "background screening"]),
    ("Transportation / Mobility", ["mobility", "vehicle", "transport", "autonomous", "fleet", "driving", "aviation",
                                   "aircraft", "electric vehicle", "scooter", " bike", "rideshar"]),
    ("Logistics / Supply Chain", ["logistics", "supply chain", "freight", "warehouse", "last-mile", "delivery",
                                  "procurement", "inventory", "fulfillment", "shipping"]),
    ("PropTech", ["real estate", "property", "housing", "mortgage", "rental", "construction", "tenant"]),
    ("CPG", ["beverage", "snack", "consumer packaged", "beauty", "cosmetic", "apparel", "grocery", "skincare",
             "eyewear", "footwear", "luxury brand", "handbags"]),
    ("Climate / Sustainability", ["climate", "carbon", "renewable", "solar", "battery", "sustainab", "emission",
                                  "clean energy", "ev charging", "electrif", "energy transition", "energy management"]),
    ("RegTech/Gov/Legal", ["legal", "compliance", "government", "regulat", "law firm", "attorney", "govtech",
                           "public sector", "national security"]),
    ("Deeptech / Robotics / AR/VR", ["robot", "hardware", "semiconductor", "chip", "drone", "aerospace",
                                     "augmented reality", "virtual reality", "satellite", "quantum", "sensor"]),
    ("Consumer", ["marketplace", "consumer", "shopping", "social network", "community", "ecommerce", "e-commerce",
                  "retailer", "online sales", "education company", "e-payment", "travel platform"]),
]

# General Atlantic's own `practice` sector labels -> everywhere_tags (used first).
# "Technology" is intentionally left unmapped -- it spans Dev Tools/Cloud, Data &
# Analytics, Consumer, Cybersecurity, etc.; the keyword classifier disambiguates it.
SECTOR_TAG_MAP = {
    "healthcare": ["Health"],
    "life sciences": ["BioTech"],
    "financial services": ["FinTech / Insurance"],
    "consumer": ["Consumer"],
    "energy transition": ["Climate / Sustainability"],
    "technology": [],
}


def fetch(url, params=None):
    last = None
    for attempt in range(1, RETRIES + 1):
        try:
            r = requests.get(url, params=params, headers=HEADERS, timeout=TIMEOUT)
            r.raise_for_status()
            return r.text
        except requests.RequestException as e:  # noqa
            last = e
            wait = SLEEP * attempt * 3
            print(f"  ! request failed for {url} ({e}); retry {attempt}/{RETRIES} in {wait:.1f}s", file=sys.stderr)
            time.sleep(wait)
    print(f"  !! giving up on {url}: {last}", file=sys.stderr)
    return None


def clean(s):
    if s is None:
        return None
    s = re.sub(r"\s+", " ", s).strip()
    return s or None


def parse_description(desc_el):
    """Split the description <p> into (description_text, company_url, disclaimer)."""
    if desc_el is None:
        return None, None, None
    frag = BeautifulSoup(str(desc_el), "html.parser")
    company_url = None
    a = frag.select_one("a.view-site")
    if a:
        href = a.get("href")
        if href and BASE not in href:   # skip GA's own self-hosted /investment/ page
            company_url = href
        a.decompose()
    disclaimer = None
    btn = frag.select_one("button.tooltip-btn")
    if btn:
        tip = btn.select_one(".tooltip-text")
        disclaimer = clean(tip.get_text(" ", strip=True)) if tip else None
        btn.decompose()
    description = clean(frag.get_text(" ", strip=True))
    return description, company_url, disclaimer


ACQUIRER_RE = re.compile(r"^Acquired by ([^,]+),\s*(.*)$", re.I)


def parse_acquirer(description):
    """GA denormalizes the acquirer into a leading 'Acquired by X, <rest>.' sentence."""
    if not description:
        return None
    m = ACQUIRER_RE.match(description)
    return clean(m.group(1)) if m else None


def parse_page(html):
    """Return list of item dicts for one grid page (no status attached)."""
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for it in soup.select(".investments-item"):
        img = it.select_one("img")
        if img is None or not img.get("alt"):
            continue   # skip non-company rows (e.g. the act.is redirect card)
        name = clean(img.get("alt"))
        logo_url = img.get("src")

        desc_el = it.select_one(".investments-item__description")
        description, company_url, disclaimer = parse_description(desc_el)

        practice_el = it.select_one(".investments-item__practice")
        practice = clean(practice_el.get_text()) if practice_el else None
        region_el = it.select_one(".investments-item__region")
        region = clean(region_el.get_text()) if region_el else None
        years_el = it.select_one(".investments-item__years")
        year_text = clean(years_el.get_text()) if years_el else None
        year_invested = int(year_text) if year_text and year_text.isdigit() else None
        exit_el = it.select_one(".investments-item__exit")
        exit_text = clean(exit_el.get_text()) if exit_el else None
        year_exited = None
        if exit_text and exit_text.isdigit() and exit_text != EPOCH_YEAR:
            year_exited = int(exit_text)

        out.append({
            "company_name": name,
            "description": description,
            "company_url": company_url,
            "logo_url": logo_url,
            "sector": practice,
            "region": region,
            "year_invested": year_invested,
            "year_exited": year_exited,
            "disclaimer": disclaimer,
        })
    return out


def has_next_page(html):
    return bool(re.search(r'class="pagination"', html or ""))


def fetch_all_pages(status, limit=None):
    """Paginate a status-filtered listing (?status=Current|Past&pg=N) until exhausted."""
    rows, page = [], 1
    while True:
        html = fetch(SOURCE_URL, params={"status": status, "pg": page})
        if html is None:
            break
        batch = parse_page(html)
        if not batch:
            break
        rows.extend(batch)
        print(f"  status={status} page {page}: {len(batch)} items (running total {len(rows)})")
        if limit and len(rows) >= limit:
            rows = rows[:limit]
            break
        if not has_next_page(html):
            break
        page += 1
        if page > 100:   # safety valve
            break
        time.sleep(SLEEP)
    return rows


def everywhere_tags(name, description, sector):
    tags = []
    if sector:
        for t in SECTOR_TAG_MAP.get(sector.strip().lower(), []):
            if t not in tags:
                tags.append(t)
    text = f"{name or ''} {description or ''}".lower()
    for tag, kws in KEYWORD_TAGS:
        if tag in tags:
            continue
        if any(kw in text for kw in kws):
            tags.append(tag)
    return tags[:4]


def main():
    limit = None
    if "--limit" in sys.argv:
        try:
            limit = int(sys.argv[sys.argv.index("--limit") + 1])
        except (IndexError, ValueError):
            sys.exit("usage: python3 generalatlantic_scraper.py [--limit N]")

    print(f"Fetching {SOURCE_URL} (status=Current)...")
    current = fetch_all_pages("Current", limit)
    print(f"Fetching {SOURCE_URL} (status=Past)...")
    past = fetch_all_pages("Past", limit)

    scraped_at = datetime.now(timezone.utc).isoformat()
    out = []
    seen = set()
    for status_label, rows in (("Current", current), ("Past", past)):
        for r in rows:
            key = r["company_name"].strip().lower()
            if key in seen:
                print(f"  ! duplicate '{r['company_name']}' across status filters -- keeping first", file=sys.stderr)
                continue
            seen.add(key)
            acquirer = parse_acquirer(r["description"]) if status_label == "Past" else None
            out.append({
                "company_name": r["company_name"],
                "description": r["description"],
                "company_url": r["company_url"],
                "logo_url": r["logo_url"],
                "sector": r["sector"],
                "region": r["region"],
                "year_invested": r["year_invested"],
                "year_exited": r["year_exited"],
                "status": status_label,
                "acquirer": acquirer,
                "disclaimer": r["disclaimer"],
                "everywhere_tags": everywhere_tags(r["company_name"], r["description"], r["sector"]),
                "source_url": SOURCE_URL,
                "scraped_at": scraped_at,
            })

    out.sort(key=lambda o: (o["company_name"] or "").lower())

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    by_tag = Counter(t for o in out for t in o["everywhere_tags"])
    by_status = Counter(o["status"] for o in out)
    by_sector = Counter(o["sector"] or "Unknown" for o in out)
    print(f"\nWrote {len(out)} companies -> {OUT}")
    print("coverage:",
          "description", sum(1 for o in out if o["description"]),
          "| website", sum(1 for o in out if o["company_url"]),
          "| sector", sum(1 for o in out if o["sector"]),
          "| region", sum(1 for o in out if o["region"]),
          "| year_invested", sum(1 for o in out if o["year_invested"]),
          "| year_exited", sum(1 for o in out if o["year_exited"]),
          "| acquirer", sum(1 for o in out if o["acquirer"]),
          "| disclaimer", sum(1 for o in out if o["disclaimer"]),
          "| untagged", sum(1 for o in out if not o["everywhere_tags"]))
    print("By status:", dict(by_status))
    print("By sector:", dict(by_sector))
    print("By everywhere_tag:")
    for t, c in by_tag.most_common():
        print(f"  {c:>4}  {t}")


if __name__ == "__main__":
    main()
