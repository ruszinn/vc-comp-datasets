#!/usr/bin/env python3
"""
Accel portfolio scraper -> accel_companies.json

Scrapes Accel's portfolio (https://www.accel.com/companies) into a JSON file.

Data source: the page is a Next.js (App Router) site whose company grid is
populated from **Accel's own public Sanity CMS dataset**. The company data is
embedded server-side in the page's React Server Component "flight" payload
(`self.__next_f.push(...)` script tags) as raw Sanity documents (`_type:
"company"`), but that payload only carries the first ~198 unique companies
(the featured/carousel sections) -- the full grid (`totalCount: 765` shown in
the same payload's `filterCategories` block) is paginated client-side.

Rather than reimplement the client-side pagination, we call Sanity's public
GROQ query API directly -- the exact same read-only endpoint the Next.js app
itself calls to render the page (project id `458oembh`, dataset `production`,
found in the embedded `cdn.sanity.io/images/458oembh/...` asset URLs):

    GET https://458oembh.api.sanity.io/v2021-10-21/data/query/production
        ?query=*[_type=="company" && archived != true] | order(name asc) {...}

This is a single request that returns every non-archived company (766 at
scrape time) with resolved references (region/stage/sector names, partner
names) in one shot -- no bot-blocking, no per-company crawl needed. This is
the firm's own published data (same one rendered into the page a visitor's
browser fetches), not a third-party enrichment source.

Schema fields (only what Accel's CMS actually exposes):
  - company_name, description (shortDescription portable-text, joined)
  - company_url (websiteUrl), company_profile_url (accel.com/companies/<slug>)
  - twitter_url, logo_url
  - headquarters (Accel's own HQ string; `location`/`locationArray`/
    `originCity`/`sfdcHeadquarters` are redundant or CRM-internal duplicates
    that occasionally disagree with `headquarters` -- dropped)
  - region (Accel's own 4-region taxonomy: Americas / Europe & Israel /
    India & SEA / Oceania)
  - sectors (Accel's own 46-term "Focus" taxonomy, e.g. "AI", "Fintech")
  - founders (portable-text blocks, each block's text split on "\n" since
    ~36 blocks join multiple founders with newlines into one span)
  - partners (Accel deal partners, resolved from `person` references)
  - first_invest_year, initial_investment_stage ("Early Stage"/"Late Stage"),
    initial_investment_type ("seed"/"series-a" -- populated for a minority)
  - is_current_investment (Accel's own `currentStatus` boolean)
  - exit_type / exit_detail (Accel's own `firstExitType` + `firstExitDescription`
    -- "IPO" + "NASDAQ: DOCU"-style ticker string, or "Acquired" + "by X")
  - second_exit_type / second_exit_detail (a second/later exit event, e.g. a
    company IPO'd then was later taken private -- Squarespace, Trulia, etc.)
  - everywhere_tags, source_url, scraped_at

Empty != absent check: exit/ticker/acquirer data is NOT denormalized into the
name suffix or description here (names have no "(Acquired)"/"(NYSE: X)"
suffixes; descriptions are pure one-line company blurbs) -- it lives in the
first-class `firstExitType`/`firstExitDescription`/`secondaryExitType`/
`secondExitDescription` fields instead, so those are read directly.

requirements:
    pip install requests

usage:
    python3 accel_scraper.py            # writes ../data/accel_companies.json
    python3 accel_scraper.py --limit 10 # only the first ~10 for a test run
"""

import json
import os
import re
import sys
import time
from datetime import datetime, timezone

import requests

SANITY_API = "https://458oembh.api.sanity.io/v2021-10-21/data/query/production"
SOURCE_URL = "https://www.accel.com/companies"
PROFILE_BASE = "https://www.accel.com/companies/"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "data", "accel_companies.json")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}
TIMEOUT = 30
SLEEP = 0.5
RETRIES = 3

# GROQ projection: resolve all references (region/stage/sector names, partner
# names) server-side so we get plain strings back, not Sanity _ref ids.
GROQ_QUERY = (
    '*[_type=="company" && archived != true] | order(name asc) {'
    'name,'
    '"slug": slug.current,'
    'websiteUrl,'
    'twitter,'
    'headquarters,'
    '"region": region->name,'
    '"stage": initialInvestment->name,'
    'initialInvestmentType,'
    '"sectors": sectors[]->name,'
    'founders,'
    '"partners": partners[]->name,'
    'firstInvestDate,'
    'firstExitType,'
    'firstExitDescription,'
    'secondaryExitType,'
    'secondExitDescription,'
    'currentStatus,'
    'shortDescription,'
    '"logo": logo.asset->url'
    '}'
)

# Accel's own 46 "Focus" sector labels -> the 17-tag everywhere_tags taxonomy.
# "AI", "APIs", "B2B", "Cloud / SaaS", "Enterprise", "Intelligent Apps",
# "Productivity", "Services" are intentionally left unmapped (too generic /
# AI-alone-is-not-a-category) and fall through to the keyword classifier.
SECTOR_TAG_MAP = {
    "Cybersecurity": ["Cybersecurity"],
    "Network Security": ["Cybersecurity"],
    "Security": ["Cybersecurity"],
    "Data Privacy": ["Cybersecurity"],
    "Health": ["Health"],
    "Healthcare": ["Health"],
    "Fintech": ["FinTech / Insurance"],
    "Insurance Tech": ["FinTech / Insurance"],
    "Payments": ["FinTech / Insurance"],
    "Crypto": ["Web3 / Crypto"],
    "Web3": ["Web3 / Crypto"],
    "Gaming": ["Gaming / Media / Entertainment"],
    "eSports": ["Gaming / Media / Entertainment"],
    "Media": ["Gaming / Media / Entertainment"],
    "Developer Tools": ["Dev Tools / Cloud"],
    "Infrastructure": ["Dev Tools / Cloud"],
    "Open Source": ["Dev Tools / Cloud"],
    "Low Code / No Code": ["Dev Tools / Cloud"],
    "Mobile": ["Dev Tools / Cloud"],
    "Big Data": ["Data & Analytics"],
    "HR Tech": ["Future of Work"],
    "Collaboration": ["Future of Work"],
    "Transportation": ["Transportation / Mobility"],
    "Drones": ["Transportation / Mobility"],
    "Logistics": ["Logistics / Supply Chain"],
    "Marketplaces": ["Consumer"],
    "Consumer": ["Consumer"],
    "Social": ["Consumer"],
    "eCommerce": ["Consumer"],
    "Travel": ["Consumer"],
    "EdTech": ["Consumer"],
    "Design": ["Dev Tools / Cloud"],
    "Robotics": ["Deeptech / Robotics / AR/VR"],
    "Hardware": ["Deeptech / Robotics / AR/VR"],
    "Defense": ["Deeptech / Robotics / AR/VR"],
    "Manufacturing": ["Deeptech / Robotics / AR/VR"],
    "Energy": ["Climate / Sustainability"],
}

# Keyword fallback (substrings, lowercased "name + description") -- copied
# from menlo_scraper.py / iconiq_scraper.py.
KEYWORD_TAGS = [
    ("BioTech", ["biotech", "drug", "therapeut", "oncolog", "cancer", "tumor", "genomic", "genome",
                 "molecul", "antibod", "protein", "vaccine", "clinical-stage", "medicine", "life science",
                 "synthetic biology", "biolog"]),
    ("Health", ["healthcare", "health care", "patient", "clinic", "medical", "mental health", "telehealth",
                "health system", "health record", "diagnos", "surgical", "doctor", "hospital", "pharmac", "therapy"]),
    ("Cybersecurity", ["cybersecurity", "security", "secure", "privacy", "fraud", "phishing", "malware",
                       "ransomware", "endpoint", "zero trust", "vulnerab", "authentication", "threat", "identity"]),
    ("FinTech / Insurance", ["fintech", "payment", "bank", "lending", "loan", "insurance", "credit", "trading",
                             "wallet", "financ", "invoic", "accounting", "payroll", "treasury", "billing",
                             "capital markets", "investing", "claims", "brokerage"]),
    ("Web3 / Crypto", ["crypto", "blockchain", "web3", "token", "on-chain", "ethereum", "bitcoin", "decentral",
                       "stablecoin", "nft"]),
    ("Gaming / Media / Entertainment", ["game", "gaming", "music", "video", "creator", "content", "publish",
                                        "entertain", "newsletter", "podcast", "film", "streaming", "social media"]),
    ("Dev Tools / Cloud", ["developer", " api ", "apis", "api platform", "infrastructure", "database", "cloud",
                           "open source", "devops", "sdk", "kubernetes", "container", "observability", "deploy",
                           "compute", "storage", "serverless", "inference", "networking", "coding", "codebase",
                           "low-code", "no-code", "source code", "llm", "foundation model"]),
    ("Data & Analytics", ["analytics", "business intelligence", "data platform", "data warehouse", "data lake",
                          "data pipeline", "insights", "dashboard", "data quality", "analyz", "data intelligence"]),
    ("Future of Work", ["workforce", "hiring", "recruit", "employee", "productivity", "collaborat", "talent",
                        "workplace", "human resources", " hr ", "customer success", "customer service",
                        "customer support", "onboarding", "workflow", "project management", "scheduling"]),
    ("Transportation / Mobility", ["mobility", "vehicle", "transport", "autonomous", "fleet", "driving", "aviation",
                                   "aircraft", "electric vehicle", "scooter", " bike", "rideshar", "travel"]),
    ("Logistics / Supply Chain", ["logistics", "supply chain", "freight", "warehouse", "delivery", "procurement",
                                  "inventory", "fulfillment", "shipping"]),
    ("PropTech", ["real estate", "property", "housing", "mortgage", "rental", "construction", "tenant"]),
    ("CPG", ["beverage", "snack", "consumer packaged", "beauty", "cosmetic", "apparel", "grocery", "skincare",
             "eyewear", "footwear"]),
    ("Climate / Sustainability", ["climate", "carbon", "renewable", "solar", "battery", "sustainab", "emission",
                                  "clean energy", "electrif", "energy", "power grid"]),
    ("RegTech/Gov/Legal", ["legal", "compliance", "government", "regulat", "law firm", "attorney"]),
    ("Deeptech / Robotics / AR/VR", ["robot", "hardware", "semiconductor", "chip", "drone", "aerospace",
                                     "augmented reality", "virtual reality", "satellite", "quantum", "sensor",
                                     "space", "rocket", "defense"]),
    ("Consumer", ["marketplace", "consumer", "shopping", "social network", "community", "app for", "app that",
                  "ecommerce", "e-commerce", "subscription", "retailer", "student", "education", "learning",
                  "fashion"]),
]


def get(url, params):
    """GET with retry/backoff, then a polite sleep."""
    last = None
    for attempt in range(1, RETRIES + 1):
        try:
            r = requests.get(url, headers=HEADERS, params=params, timeout=TIMEOUT)
            r.raise_for_status()
            time.sleep(SLEEP)
            return r.json()
        except requests.RequestException as e:  # noqa
            last = e
            wait = SLEEP * attempt * 2
            print(f"  ! request failed ({e}); retry {attempt}/{RETRIES} in {wait:.1f}s", file=sys.stderr)
            time.sleep(wait)
    raise SystemExit(f"FATAL: could not fetch {url}: {last}")


ZERO_WIDTH_RE = re.compile(r"[​‌‍﻿]")


def clean(s):
    if s is None:
        return None
    s = ZERO_WIDTH_RE.sub("", str(s))
    s = re.sub(r"\s+", " ", s).strip()
    return s or None


def portable_text(blocks):
    """Join Sanity portable-text blocks into a single plain-text string."""
    if not blocks:
        return None
    parts = []
    for b in blocks:
        for child in b.get("children", []) or []:
            t = child.get("text")
            if t:
                parts.append(t)
    joined = " ".join(parts)
    return clean(joined)


def parse_founders(blocks):
    """Each portable-text block is usually one founder, but ~36 blocks join
    multiple founders into one span with '\\n' -- split those out too."""
    names = []
    for b in blocks or []:
        for child in b.get("children", []) or []:
            t = child.get("text") or ""
            for line in t.split("\n"):
                line = clean(line)
                if line and line not in names:
                    names.append(line)
    return names


def everywhere_tags(sectors, name, description):
    tags = []
    for sec in sectors or []:
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


def main():
    limit = int(sys.argv[sys.argv.index("--limit") + 1]) if "--limit" in sys.argv else None

    print("Querying Accel's public Sanity dataset (project 458oembh/production)...")
    payload = get(SANITY_API, {"query": GROQ_QUERY})
    rows = payload.get("result") or []
    print(f"  fetched {len(rows)} non-archived companies")

    scraped_at = datetime.now(timezone.utc).isoformat()
    out = []
    for r in (rows[:limit] if limit else rows):
        name = clean(r.get("name"))
        if not name:
            continue
        slug = r.get("slug")
        description = portable_text(r.get("shortDescription"))
        sectors = [clean(s) for s in (r.get("sectors") or []) if clean(s)]

        first_invest_year = None
        m = re.search(r"(19|20)\d{2}", r.get("firstInvestDate") or "")
        if m:
            first_invest_year = int(m.group(0))

        out.append({
            "company_name": name,
            "description": description,
            "company_url": clean(r.get("websiteUrl")),
            "company_profile_url": PROFILE_BASE + slug if slug else None,
            "twitter_url": clean(r.get("twitter")),
            "logo_url": clean(r.get("logo")),
            "headquarters": clean(r.get("headquarters")),
            "region": clean(r.get("region")),
            "sectors": sectors,
            "founders": parse_founders(r.get("founders")),
            "partners": [clean(p) for p in (r.get("partners") or []) if clean(p)],
            "first_invest_year": first_invest_year,
            "initial_investment_stage": clean(r.get("stage")),
            "initial_investment_type": clean(r.get("initialInvestmentType")),
            "is_current_investment": r.get("currentStatus"),
            "exit_type": clean(r.get("firstExitType")),
            "exit_detail": clean(r.get("firstExitDescription")),
            "second_exit_type": clean(r.get("secondaryExitType")),
            "second_exit_detail": clean(r.get("secondExitDescription")),
            "everywhere_tags": everywhere_tags(sectors, name, description),
            "source_url": SOURCE_URL,
            "scraped_at": scraped_at,
        })

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2)

    # ---- summary ----
    n = len(out)
    print(f"\nWrote {n} companies -> {OUT}")
    for field in ("description", "company_url", "twitter_url", "logo_url", "headquarters", "region",
                  "first_invest_year", "initial_investment_stage", "initial_investment_type",
                  "exit_type", "exit_detail"):
        miss = sum(1 for r in out if not r[field])
        print(f"  {field:26s} missing: {miss}/{n}")
    print(f"  sectors empty:              {sum(1 for r in out if not r['sectors'])}/{n}")
    print(f"  founders empty:             {sum(1 for r in out if not r['founders'])}/{n}")
    print(f"  partners empty:             {sum(1 for r in out if not r['partners'])}/{n}")
    from collections import Counter
    by_exit = Counter(r["exit_type"] for r in out if r["exit_type"])
    print("  by exit_type:", dict(by_exit))
    print("  is_current_investment True:", sum(1 for r in out if r["is_current_investment"] is True),
          "| False:", sum(1 for r in out if r["is_current_investment"] is False))
    untagged = [r["company_name"] for r in out if not r["everywhere_tags"]]
    print(f"  untagged:                   {len(untagged)}/{n}" + (f" -> {untagged[:15]}{'...' if len(untagged) > 15 else ''}" if untagged else ""))
    by_tag = Counter(t for r in out for t in r["everywhere_tags"])
    print("  by everywhere_tag:")
    for t, k in by_tag.most_common():
        print(f"    {k:3d}  {t}")


if __name__ == "__main__":
    main()
