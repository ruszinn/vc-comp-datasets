#!/usr/bin/env python3
"""
USV portfolio scraper -> usv_companies.json

Scrapes Union Square Ventures' portfolio (https://www.usv.com/companies/) into a
JSON file. The page is server-rendered WordPress: every company is in the static
HTML, and Sector/Status are exposed via server-side query-param filters. No LLM /
API key needed -- pure HTTP + HTML parsing.

requirements:
    pip install requests beautifulsoup4

usage:
    python3 usv_scraper.py            # writes usv_companies.json next to this file
"""

import json
import os
import re
import sys
import time
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

BASE = "https://www.usv.com/companies/"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "data", "usv_companies.json")
SOURCE_URL = "https://www.usv.com/companies/"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}
TIMEOUT = 30
SLEEP = 0.7          # polite delay between requests
RETRIES = 3

STATUSES = ["current", "acquired", "public", "inactive"]

# USV "industry-cat" values -> human label used in the output `sectors` list.
SECTORS = {
    "adtech": "Adtech", "ai": "AI", "climate": "Climate", "commerce": "Commerce",
    "community": "Community", "consumer": "Consumer", "creator-platform": "Creator Platform",
    "crypto": "Crypto", "data": "Data", "developer-tools": "Developer Tools",
    "education": "Education", "enterprise": "Enterprise", "environment": "Environment",
    "fintech": "Fintech", "food": "Food", "gaming": "Gaming", "hardware": "Hardware",
    "health": "Health", "hr": "HR", "infrastructure": "Infrastructure", "insurance": "Insurance",
    "machine-learning": "Machine Learning", "marketplace": "Marketplace", "media": "Media",
    "music": "Music", "privacy-security": "Privacy & Security", "publishing": "Publishing",
    "real-estate": "Real Estate", "social": "Social", "transportation": "Transportation",
    "workforce": "Workforce",
}

# USV sector slug -> tags from the 17-tag "everywhere" taxonomy.
# ai / machine-learning / enterprise intentionally map to nothing ("AI alone is not
# a category"); those companies fall through to the keyword classifier / co-sectors.
SECTOR_TAG_MAP = {
    "adtech": ["Data & Analytics"],
    "climate": ["Climate / Sustainability"], "environment": ["Climate / Sustainability"],
    "commerce": ["Consumer"], "marketplace": ["Consumer"], "community": ["Consumer"],
    "consumer": ["Consumer"], "social": ["Consumer"], "education": ["Consumer"],
    "creator-platform": ["Gaming / Media / Entertainment"], "media": ["Gaming / Media / Entertainment"],
    "music": ["Gaming / Media / Entertainment"], "publishing": ["Gaming / Media / Entertainment"],
    "gaming": ["Gaming / Media / Entertainment"],
    "crypto": ["Web3 / Crypto"],
    "data": ["Data & Analytics"],
    "developer-tools": ["Dev Tools / Cloud"], "infrastructure": ["Dev Tools / Cloud"],
    "fintech": ["FinTech / Insurance"], "insurance": ["FinTech / Insurance"],
    "food": ["CPG"],
    "hardware": ["Deeptech / Robotics / AR/VR"],
    "health": ["Health"],
    "hr": ["Future of Work"], "workforce": ["Future of Work"],
    "privacy-security": ["Cybersecurity"],
    "real-estate": ["PropTech"],
    "transportation": ["Transportation / Mobility"],
    "ai": [], "machine-learning": [], "enterprise": [],
}

# Keyword fallback for the everywhere_tags (only used when sectors yield nothing).
# Substrings are matched against lowercased "name + description".
KEYWORD_TAGS = [
    ("Health", ["health", "patient", "clinic", "medical", "therap", "care ", "wellness", "disease", "diagnos"]),
    ("BioTech", ["biotech", "genomic", "genome", "drug", "therapeutic", "oncolog", "molecul", "life science"]),
    ("Cybersecurity", ["security", "secure", "privacy", "fraud", "identity", "threat", "authentication"]),
    ("FinTech / Insurance", ["fintech", "payment", "bank", "lending", "loan", "insurance", "credit", "trading", "wallet", "finance", "invest"]),
    ("Web3 / Crypto", ["crypto", "blockchain", "web3", "token", "on-chain", "ethereum", "bitcoin", "decentral"]),
    ("Gaming / Media / Entertainment", ["game", "gaming", "media", "music", "video", "creator", "content", "publish", "entertain", "newsletter", "podcast", "film"]),
    ("Dev Tools / Cloud", ["developer", "api", "infrastructure", "database", "cloud", "open source", "devops", "sdk", "platform for", "deploy"]),
    ("Data & Analytics", ["analytics", "data ", "intelligence", "insight", "search", "measurement"]),
    ("Future of Work", ["workforce", "hiring", "recruit", "hr ", "employee", "productivity", "collaboration", "talent", "workplace"]),
    ("Transportation / Mobility", ["mobility", "vehicle", "transport", "ev ", "fleet", "driving", "logistics network"]),
    ("Logistics / Supply Chain", ["logistics", "supply chain", "freight", "warehouse", "delivery", "procurement", "inventory"]),
    ("PropTech", ["real estate", "property", "housing", "mortgage", "rental", "construction"]),
    ("CPG", ["food", "beverage", "snack", "cpg", "consumer packaged", "beauty", "apparel", "grocery"]),
    ("Climate / Sustainability", ["climate", "carbon", "energy", "renewable", "solar", "battery", "sustainab", "emission"]),
    ("Consumer", ["marketplace", "consumer", "shopping", "social network", "community", "app for"]),
    ("RegTech/Gov/Legal", ["legal", "compliance", "government", "regulat", "law "]),
    ("Deeptech / Robotics / AR/VR", ["robot", "hardware", "semiconductor", "chip", "drone", "aerospace", "augmented reality", "virtual reality", "simulation", "engineering", "cae"]),
]


def get(url):
    """GET with retry/backoff, then a polite sleep."""
    last = None
    for attempt in range(1, RETRIES + 1):
        try:
            r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
            r.raise_for_status()
            time.sleep(SLEEP)
            return r.text
        except requests.RequestException as e:  # noqa
            last = e
            wait = SLEEP * attempt * 2
            print(f"  ! request failed ({e}); retry {attempt}/{RETRIES} in {wait:.1f}s", file=sys.stderr)
            time.sleep(wait)
    raise SystemExit(f"FATAL: could not fetch {url}: {last}")


def norm(name):
    return re.sub(r"\s+", " ", (name or "")).strip().lower()


def text_or_none(node):
    if node is None:
        return None
    t = re.sub(r"\s+", " ", node.get_text(" ", strip=True)).strip()
    return t or None


def parse_rows(html):
    """Return list of dicts (one per desktop company row)."""
    soup = BeautifulSoup(html, "html.parser")
    rows = []
    for row in soup.select(".m__list-row"):
        classes = row.get("class", [])
        if "m__list-row--mobile" in classes:
            continue  # skip the duplicated mobile row

        # logo
        img = row.select_one("img.m__list-row__image")
        logo = img.get("src") if img else None

        # The name lives in the column that also holds span.exit-detail. The name is
        # usually a target=_blank link, but companies with no live site render it as
        # plain text -> handle both so no company is dropped.
        exit_el = row.select_one("span.exit-detail")
        exit_detail = text_or_none(exit_el)
        name_col = exit_el.find_parent(class_="m__list-row__col") if exit_el else None
        name, company_url = None, None
        if name_col is not None:
            name_a = name_col.select_one('a[target="_blank"]') or name_col.select_one("a[href]")
            if name_a is not None:
                name = text_or_none(name_a)
                company_url = name_a.get("href") or None
            else:
                # plain-text name: take the column text minus the exit-detail span
                if exit_el is not None:
                    exit_el.extract()
                name = text_or_none(name_col)
        if not name:  # fall back to any external link in the row
            alt = row.select_one('a[target="_blank"]')
            if alt is not None:
                name, company_url = text_or_none(alt), (alt.get("href") or None)

        # description
        desc = text_or_none(row.select_one(".m__list-row__excerpt"))

        # internal USV "Read the Post" article
        post_a = row.select_one("a.m__list-row__link")
        usv_post_url = post_a.get("href") if post_a else None

        # "Stage, Year" lives in a plain column with no special class -> find it
        stage, year = None, None
        for col in row.select(".m__list-row__col"):
            t = text_or_none(col)
            if not t:
                continue
            m = re.search(r"^(?P<stage>.*?),?\s*(?P<year>(?:19|20)\d{2})$", t)
            if m and m.group("year"):
                stage = (m.group("stage") or "").strip().rstrip(",").strip() or None
                year = int(m.group("year"))
                break

        if not name:
            continue
        rows.append({
            "company_name": name,
            "company_url": company_url,
            "logo_url": logo,
            "exit_detail": exit_detail,
            "description": desc,
            "usv_post_url": usv_post_url,
            "first_investment_stage": stage,
            "first_investment_year": year,
        })
    return rows


def stage_to_type(stage):
    if not stage:
        return None
    s = stage.strip().lower()
    if s == "common":
        return "common"
    if s == "token":
        return "token"
    if s == "seed":
        return "seed"
    m = re.match(r"series\s+([a-z])$", s)
    if m:
        return "series-" + m.group(1)
    return None


def derive_exit(status, exit_detail):
    """Return (exit_type, acquirer, ticker_symbol)."""
    exit_type = acquirer = ticker = None
    st = (status or "").lower()
    if st == "acquired":
        exit_type = "Acquired"
        if exit_detail:
            m = re.match(r"^(?:Acquired by|Merged with)\s+(.*)$", exit_detail, re.I)
            if m:
                acquirer = m.group(1).strip()
    elif st == "public":
        exit_type = "Public"
        if exit_detail and re.match(r"^[A-Z]{2,6}:\s*[A-Za-z.\-]+$", exit_detail.strip()):
            ticker = exit_detail.strip()
    return exit_type, acquirer, ticker


def everywhere_tags(sector_slugs, name, description):
    tags = []
    for slug in sector_slugs:
        for t in SECTOR_TAG_MAP.get(slug, []):
            if t not in tags:
                tags.append(t)
    if not tags:  # keyword fallback only when sectors gave us nothing
        text = f"{name or ''} {description or ''}".lower()
        for tag, kws in KEYWORD_TAGS:
            if any(kw in text for kw in kws) and tag not in tags:
                tags.append(tag)
    return tags[:4]


def main():
    print(f"Fetching base portfolio: {BASE}")
    records = {}            # key -> record dict
    sector_slugs = {}       # key -> list of usv sector slugs
    for r in parse_rows(get(BASE)):
        key = norm(r["company_name"])
        if key in records:
            print(f"  ! duplicate company name '{r['company_name']}' — keeping first", file=sys.stderr)
            continue
        records[key] = r
        sector_slugs[key] = []
    print(f"  parsed {len(records)} companies")

    print("Fetching status filters...")
    for st in STATUSES:
        rows = parse_rows(get(f"{BASE}?status-cat={st}"))
        n = 0
        for r in rows:
            key = norm(r["company_name"])
            if key in records:
                records[key]["status"] = st.title()
                n += 1
        print(f"  status={st}: {n}")

    print("Fetching sector filters...")
    for slug, label in SECTORS.items():
        rows = parse_rows(get(f"{BASE}?industry-cat={slug}"))
        n = 0
        for r in rows:
            key = norm(r["company_name"])
            if key in records:
                sector_slugs[key].append(slug)
                n += 1
        print(f"  sector={slug}: {n}")

    # Any company present on the portfolio but absent from all status filters is, by
    # elimination, a current holding (the 4 statuses are exhaustive on USV's site).
    missing_status = [r["company_name"] for r in records.values() if not r.get("status")]
    if missing_status:
        print(f"  defaulting {len(missing_status)} company(ies) with no status filter -> Current: {missing_status}")
        for r in records.values():
            if not r.get("status"):
                r["status"] = "Current"

    scraped_at = datetime.now(timezone.utc).isoformat()
    out = []
    for key, r in records.items():
        status = r.get("status")
        exit_type, acquirer, ticker = derive_exit(status, r["exit_detail"])
        slugs = sector_slugs[key]
        out.append({
            "company_name": r["company_name"],
            "description": r["description"],
            "company_url": r["company_url"],
            "usv_post_url": r["usv_post_url"],
            "logo_url": r["logo_url"],
            "first_investment_stage": r["first_investment_stage"],
            "first_investment_year": r["first_investment_year"],
            "initial_investment_type": stage_to_type(r["first_investment_stage"]),
            "status": status,
            "exit_type": exit_type,
            "exit_detail": r["exit_detail"],
            "acquirer": acquirer,
            "ticker_symbol": ticker,
            "sectors": [SECTORS[s] for s in slugs],
            "everywhere_tags": everywhere_tags(slugs, r["company_name"], r["description"]),
            "source_url": SOURCE_URL,
            "scraped_at": scraped_at,
        })

    # sort by first investment year desc, then name (mirrors the site default)
    out.sort(key=lambda o: (-(o["first_investment_year"] or 0), o["company_name"].lower()))

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    # ---- summary ----
    from collections import Counter
    by_status = Counter(o["status"] or "Unknown" for o in out)
    by_tag = Counter(t for o in out for t in o["everywhere_tags"])
    multi = sum(1 for o in out if len(o["everywhere_tags"]) >= 2)
    untagged = sum(1 for o in out if not o["everywhere_tags"])
    print(f"\nWrote {len(out)} companies -> {OUT}")
    print("By status:", dict(by_status))
    print("Multi-tag:", multi, "| Untagged:", untagged)
    print("By everywhere_tag:")
    for t, c in by_tag.most_common():
        print(f"  {c:>4}  {t}")


if __name__ == "__main__":
    main()
