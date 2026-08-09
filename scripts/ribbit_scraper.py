#!/usr/bin/env python3
"""
Ribbit Capital portfolio scraper -> ribbit_companies.json

Scrapes Ribbit Capital's portfolio page (https://www.ribbitcap.com/rebels) into
a JSON file. Ribbit calls its portfolio "rebels" (nav item "Rebels"; no
"Portfolio"/"Companies" route exists -- both 404). The bare apex domain
`ribbitcap.com` is unreachable from this environment (TCP connect times out /
refused); `www.ribbitcap.com` works fine and 200s normally, so the scraper uses
the www host.

The site is Next.js (App Router) and the `/rebels` page is fully server-
rendered static HTML -- no client-side fetch, no JSON API, no pagination. Each
row is a `<div data-type="scrollable-list-item" data-index="N">` with exactly
two spans: the company name and a comma-separated list of founder names
(abbreviated as "First L." -- Ribbit only ever publishes first name + last
initial, e.g. "Max L., Nathan G."; a few rows show a full name because that
founder is publicly using it, e.g. "Caesar Sengupta", "Bobby Lee" -- kept
verbatim, not expanded/invented). 148 rows total, no pagination markers.

What Ribbit does NOT publish anywhere on ribbitcap.com (checked homepage,
/rebels, /team, /mantra, /perspective, robots.txt [404], sitemap.xml [404], and
per the "Empty != absent" rule, the company names themselves for encoded exit
state): no per-company detail page or slug route (/rebels/<name> and
/company/<name> both 404), no description/blurb, no external company website
link, no logo, no sector/category/industry tag, no investment stage, no
founded/invested year, no status/exit/acquirer/ticker (no name carries a
suffix like "(Acquired)" or "(NYSE: X)" -- none of the 148 names contain a
parenthesis, colon, or exit-related keyword). These fields are therefore
intentionally omitted from the schema rather than shipped as always-null
columns per the site-tailored-schema principle -- the two-column schema below
is genuinely everything the source exposes.

requirements:
    pip install requests beautifulsoup4

usage:
    python3 ribbit_scraper.py             # writes ../data/ribbit_companies.json
    python3 ribbit_scraper.py --limit 20  # only the first ~20 for a test run
"""

import json
import os
import re
import sys
import time
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

BASE = "https://www.ribbitcap.com/rebels"
SOURCE_URL = "https://www.ribbitcap.com/rebels"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "data", "ribbit_companies.json")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}
TIMEOUT = 45
RETRIES = 3

# everywhere_tags keyword classifier (substrings, lowercased) -- copied from
# menlo_scraper.py / rre_scraper.py / foundersfund_scraper.py. Ribbit exposes no
# sectors at all, so this is the ONLY signal available, applied to the company
# name alone (no description exists). Expect low coverage: most fintech/crypto
# names (Ribbit's well-known focus) are proper nouns with no matchable keyword
# (e.g. "Affirm", "Nubank", "Revolut") -- that's a real, reported limitation of
# the source, not a bug in the classifier.
KEYWORD_TAGS = [
    ("BioTech", ["biotech", "drug", "therapeut", "oncolog", "cancer", "tumor", "genomic", "genome",
                 "molecul", "antibod", "protein", "vaccine", "clinical-stage", "medicine", "biolog"]),
    ("Health", ["health", "patient", "clinic", "medical", "telehealth", "diagnos", "surgical", "doctor",
                "hospital", "pharmac", "therapy"]),
    ("Cybersecurity", ["cybersecurity", "security", "secure", "privacy", "fraud", "phishing", "malware",
                       "ransomware", "endpoint", "zero trust", "vulnerab", "authentication", "threat", "identity"]),
    ("FinTech / Insurance", ["fintech", "payment", "bank", "lending", "loan", "insurance", "credit", "trading",
                             "wallet", "financ", "invoic", "accounting", "payroll", "treasury", "billing",
                             " tax", "audit", "money", "robo-advisor", "brokerage", "capital markets", "invest",
                             "pay ", " pay", "remittance"]),
    ("Web3 / Crypto", ["crypto", "blockchain", "web3", "token", "on-chain", "ethereum", "bitcoin", "decentral",
                       "stablecoin", "nft", "defi"]),
    ("Gaming / Media / Entertainment", ["game", "gaming", "music", "video", "creator", "content", "publish",
                                        "entertain", "newsletter", "podcast", "film", "streaming", "media"]),
    ("Dev Tools / Cloud", ["developer", " api", "infrastructure", "database", "cloud", "open source", "devops",
                           "sdk", "kubernetes", "container", "observability", "compute", "storage", "serverless",
                           "inference", "networking", "coding", "codebase", "llm", "foundation model", "quantum"]),
    ("Data & Analytics", ["analytics", "business intelligence", "data platform", "data warehouse", "insights",
                          "dashboard"]),
    ("Future of Work", ["workforce", "hiring", "recruit", "employee", "productivity", "collaboration", "talent",
                        "workplace", "human resources"]),
    ("Transportation / Mobility", ["mobility", "vehicle", "transport", "autonomous", "fleet", "driving", "aviation",
                                   "aircraft"]),
    ("Logistics / Supply Chain", ["logistics", "supply chain", "freight", "warehouse", "delivery", "procurement",
                                  "inventory", "fulfillment", "shipping"]),
    ("PropTech", ["real estate", "property", "housing", "mortgage", "rental", "construction", "tenant"]),
    ("CPG", ["beverage", "snack", "consumer packaged", "beauty", "cosmetic", "apparel", "grocery", "skincare"]),
    ("Climate / Sustainability", ["climate", "carbon", "renewable", "solar", "battery", "sustainab", "emission",
                                  "clean energy", "energy"]),
    ("RegTech/Gov/Legal", ["legal", "compliance", "government", "regulat", "law firm", "attorney"]),
    ("Deeptech / Robotics / AR/VR", ["robot", "hardware", "semiconductor", "chip", "drone", "aerospace",
                                     "augmented reality", "virtual reality", "satellite", "sensor", "space",
                                     "rocket", "digital assets"]),
    ("Consumer", ["marketplace", "consumer", "shopping", "social network", "community", "ecommerce",
                  "e-commerce", "subscription", "retailer"]),
]


def fetch(url, params=None):
    last = None
    for attempt in range(1, RETRIES + 1):
        try:
            r = requests.get(url, headers=HEADERS, params=params, timeout=TIMEOUT)
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


def parse_founders(raw):
    """'Max L., Nathan G., Jeffrey K.' -> ['Max L.', 'Nathan G.', 'Jeffrey K.']"""
    if not raw:
        return []
    parts = [clean(p) for p in raw.split(",")]
    return [p for p in parts if p]


def everywhere_tags(name):
    text = (name or "").lower()
    tags = []
    for tag, kws in KEYWORD_TAGS:
        if any(kw in text for kw in kws):
            tags.append(tag)
    return tags[:4]


def parse_rows(html):
    soup = BeautifulSoup(html, "html.parser")
    rows = soup.find_all("div", attrs={"data-type": "scrollable-list-item"})
    out = []
    for row in rows:
        spans = row.find_all("span", recursive=False)
        cell_texts = []
        for span in spans:
            inner = span.find("span", class_="truncate")
            cell_texts.append(clean(inner.get_text()) if inner else clean(span.get_text()))
        if len(cell_texts) < 2:
            continue
        name, founders_raw = cell_texts[0], cell_texts[1]
        if not name:
            continue
        out.append((name, founders_raw))
    return out


def main():
    limit = int(sys.argv[sys.argv.index("--limit") + 1]) if "--limit" in sys.argv else None

    html = fetch(BASE)
    rows = parse_rows(html)
    if limit:
        rows = rows[:limit]

    scraped_at = datetime.now(timezone.utc).isoformat()
    out = []
    for name, founders_raw in rows:
        founders = parse_founders(founders_raw)
        out.append({
            "company_name": name,
            "founders": founders,
            "everywhere_tags": everywhere_tags(name),
            "source_url": SOURCE_URL,
            "scraped_at": scraped_at,
        })

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2)

    # ---- summary ----
    n = len(out)
    print(f"wrote {n} companies -> {OUT}")
    print(f"  founders empty:    {sum(1 for r in out if not r['founders'])}/{n}")
    untagged = [r["company_name"] for r in out if not r["everywhere_tags"]]
    print(f"  untagged:          {len(untagged)}/{n}" + (f" -> {untagged}" if untagged else ""))
    by_tag = {}
    for r in out:
        for t in r["everywhere_tags"]:
            by_tag[t] = by_tag.get(t, 0) + 1
    print("  by everywhere_tag:")
    for t, k in sorted(by_tag.items(), key=lambda x: -x[1]):
        print(f"    {k:3d}  {t}")


if __name__ == "__main__":
    main()
