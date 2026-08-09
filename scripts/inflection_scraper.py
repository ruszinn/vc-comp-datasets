#!/usr/bin/env python3
"""
Inflection Ventures portfolio scraper -> inflection_companies.json

Scrapes Inflection Ventures' portfolio (https://inflectionvc.com/portfolio/) --
an AI-infrastructure/defense VC founded 2022, based in Peconic NY (NOT the AI
company "Inflection AI"). The site is WordPress + Elementor (LA Studio Kit
plugin), fully server-rendered, no API:

  1. The portfolio page itself is a static "images layout" grid --
     `.lakit-images-layout__item` -- with 16 entries, each an `<a>` to a
     per-company detail page `inflectionvc.com/portfolio/<slug>/`, a logo
     `<img>`, and a title `<h5>`. All 16 fit on one page, no pagination.
  2. Each detail page is a standalone Elementor page with:
       - two `<h2>`s: company name, then an optional marketing tagline
         (missing for 3/16: Epirus, Exowatt, Radiant Nuclear)
       - a "Leadership" text-editor block: one or more `<br />`-separated
         entries, each usually `<a href="LinkedIn/Twitter">Name</a>, Title`
         (occasionally a name has no link, e.g. George Tenet at CHAOS)
       - a "Visit Client Site" button -- the external company website
       - a "Company Info" text-editor block -- the description paragraph
       - WordPress `category-*` / `tag-*` classes on the outer container --
         Inflection's own (fairly noisy/duplicative) sector taxonomy.

No API key, no per-company crawling beyond the 16 detail pages, no LLM.

Empty != absent check: grepped all 16 descriptions + names for
"acquired"/"IPO"/"(NYSE:...)"/"(NASDAQ:...)" -- the only 2 hits (Exowatt,
Radiant Nuclear) are speculative future-IPO mentions ("potential IPO as early
as 2026", "targeted 2028 IPO"), not completed exits. Inflection publishes no
structured or prose-encoded status/acquirer/ticker/exit-year for any of its
16 companies (consistent with a young, all-active portfolio) -- those fields
are intentionally omitted, not invented.

requirements:
    pip install requests beautifulsoup4

usage:
    python3 inflection_scraper.py            # writes ../data/inflection_companies.json
    python3 inflection_scraper.py --limit 5  # only the first 5 for a test run
"""

import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from html import unescape

import requests
from bs4 import BeautifulSoup

PORTFOLIO_URL = "https://inflectionvc.com/portfolio/"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "data", "inflection_companies.json")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}
TIMEOUT = 45
RETRIES = 3
SLEEP_BETWEEN = 1.0

# everywhere_tags keyword classifier (substrings, lowercased) -- copied from
# rre_scraper.py / foundersfund_scraper.py. Inflection's own category/tag
# taxonomy is noisy and firm-specific (e.g. "ai-high-performance-computing",
# "defense-tech"), so it is kept as a raw `categories` field and NOT mapped to
# the 17-tag taxonomy; everywhere_tags is derived purely by keyword-classifying
# name + tagline + description (AI alone is not a category).
KEYWORD_TAGS = [
    ("BioTech", ["biotech", "drug", "therapeut", "oncolog", "cancer", "tumor", "genomic", "genome",
                 "molecul", "antibod", "protein", "vaccine", "clinical-stage", "medicine", "life science",
                 "synthetic biology", "biolog"]),
    ("Health", ["healthcare", "health care", "patient", "clinic", "medical", "mental health", "telehealth",
                "health system", "health record", "diagnos", "surgical", "doctor", "hospital", "pharmac"]),
    ("Cybersecurity", ["cybersecurity", "cyber threat", "phishing", "malware", "ransomware", "endpoint",
                       "zero trust", "vulnerab", "authentication", "email security", "email threat",
                       "business email compromise", "data loss prevention", "insider threat"]),
    ("FinTech / Insurance", ["fintech", "payment", "banking", "lending", "insurance", "financial services",
                             "wallet", "invoic", "accounting", "payroll", "treasury", "billing",
                             "capital markets", "brokerage"]),
    ("RegTech/Gov/Legal", ["legal", "compliance", "regulat", "law firm", "attorney", "government agenc"]),
    ("Deeptech / Robotics / AR/VR", ["robot", "hardware", "semiconductor", "chip", "drone", "aerospace",
                                     "augmented reality", "virtual reality", "satellite", "quantum", "sensor",
                                     "unmanned", "autonomous system", "microreactor", "nuclear", "directed energy",
                                     "electromagnetic pulse", "reusable rocket", "spacecraft", "defense tech",
                                     "defense technology", "defense agenc", "battlefield"]),
    ("Data & Analytics", ["analytics", "business intelligence", "data platform", "data warehouse", "data lake",
                          "data pipeline", "insights", "dashboard", "big data", "data intelligence",
                          "data engineering", "data processing"]),
    ("Web3 / Crypto", ["cryptocurrency", "blockchain", "web3", "on-chain", "ethereum", "bitcoin", "decentral",
                       "stablecoin", "nft"]),
    ("Dev Tools / Cloud", ["developer", " api ", "apis", "infrastructure", "database", "cloud computing",
                           "open source", "devops", "sdk", "kubernetes", "container", "compute", "storage",
                           "serverless", "inference", "gpu", "cloud", "deploy", "web application", "next.js"]),
    ("Transportation / Mobility", ["mobility", "vehicle", "transport", "aviation", "aircraft", "rocket",
                                   "space travel", "space exploration", "rideshar"]),
    ("Logistics / Supply Chain", ["logistics", "supply chain", "freight", "warehouse", "delivery",
                                  "procurement", "inventory", "fulfillment", "shipping"]),
    ("PropTech", ["real estate", "property", "housing", "mortgage", "rental", "construction", "tenant"]),
    ("CPG", ["beverage", "snack", "consumer packaged", "beauty", "cosmetic", "apparel", "grocery", "skincare"]),
    ("Climate / Sustainability", ["clean energy", "solar", "battery recycling", "sustainab", "renewable",
                                  "emission", "electric grid", "energy storage", "thermal storage"]),
    ("Gaming / Media / Entertainment", ["game", "gaming", "music", "video", "creator", "streaming", "media"]),
    ("Consumer", ["marketplace", "consumer", "shopping", "social network", "community", "ecommerce",
                  "e-commerce", "subscription"]),
    ("Future of Work", ["workforce", "hiring", "recruit", "employee", "productivity", "collaboration",
                        "talent", "workplace"]),
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
    s = unescape(re.sub(r"\s+", " ", s)).strip()
    return s or None


def strip_html(h):
    if not h:
        return None
    return clean(re.sub(r"<[^>]+>", " ", h))


def fetch_grid():
    """Parse the portfolio grid page: 16 `.lakit-images-layout__item` entries,
    each with a name, logo, and detail-page link. No pagination."""
    html = get(PORTFOLIO_URL)
    soup = BeautifulSoup(html, "html.parser")
    items = []
    seen = set()
    for a in soup.select("a.lakit-images-layout__link"):
        href = clean(a.get("href"))
        if not href or href in seen:
            continue
        seen.add(href)
        h5 = a.select_one("h5.lakit-images-layout__title")
        grid_name = clean(h5.get_text()) if h5 else None
        img = a.select_one("img.lakit-images-layout__image-instance")
        logo_url = clean(img.get("src")) if img else None
        items.append({"detail_url": href, "grid_name": grid_name, "logo_url": logo_url})
    return items


def parse_categories(class_attr):
    """WordPress `category-*` / `tag-*` classes on the single-page container.
    'portfolio-asset' is a curation flag (every company has it), not a sector,
    so it's dropped. Kept as a raw, site-tailored `categories` field (not
    mapped to everywhere_tags -- too noisy/firm-specific; see module docstring)."""
    classes = (class_attr or "").split()
    cats = [c[len("category-"):] for c in classes if c.startswith("category-") and c != "category-portfolio-asset"]
    tags = [c[len("tag-"):] for c in classes if c.startswith("tag-")]
    return cats, tags


def parse_leadership(html):
    """Leadership block: one or more people, `<br />`-separated, each formatted
    as `<a href="URL">Name</a>, Title` (LinkedIn/Twitter -- occasionally a name
    has no link, e.g. George Tenet at CHAOS Industries). Returns a list of
    {name, title, url} dicts; url/title are null when not present/parseable."""
    m = re.search(r">Leadership</div>.*?<p[^>]*>(.*?)</p>", html, re.S)
    if not m:
        return []
    block = m.group(1)
    entries_html = re.split(r"<br\s*/?>", block)
    people = []
    for entry in entries_html:
        text = strip_html(entry)
        if not text:
            continue
        url_m = re.search(r'href="([^"]+)"', entry)
        url = clean(url_m.group(1)) if url_m else None
        # split "Name, Title" or "Name – Title" (exowatt/radiant-nuclear use an en-dash)
        parts = re.split(r"\s*[,–]\s*", text, maxsplit=1)
        name = clean(parts[0]) if parts else None
        title = clean(parts[1]) if len(parts) > 1 else None
        if not name:
            continue
        people.append({"name": name, "title": title, "url": url})
    return people


def parse_detail(url):
    html = get(url)

    m_cats = re.search(r'data-elementor-type="single-page"[^>]*class="([^"]*)"', html)
    categories, wp_tags = parse_categories(m_cats.group(1) if m_cats else None)

    h2s = re.findall(r"<h2[^>]*>(.*?)</h2>", html, re.S)
    h2s_clean = [strip_html(h) for h in h2s]
    company_name = h2s_clean[0] if h2s_clean else None
    tagline = h2s_clean[1] if len(h2s_clean) > 1 else None

    m_website = re.search(
        r'<a href="([^"]+)" target="_blank"[^>]*>\s*<span class="elementor-button-content-wrapper">\s*'
        r'<span class="elementor-button-text">Visit Client Site',
        html,
    )
    company_url = clean(m_website.group(1)) if m_website else None

    founders = parse_leadership(html)

    m_desc = re.search(r">Company Info</div>.*?<p[^>]*>(.*?)</p>", html, re.S)
    description = strip_html(m_desc.group(1)) if m_desc else None

    return {
        "company_name": company_name,
        "tagline": tagline,
        "description": description,
        "company_url": company_url,
        "company_profile_url": url,
        "founders": founders,
        "categories": categories,
    }


def everywhere_tags(name, tagline, description):
    """Keyword-classify name + tagline + description against the 17-tag
    taxonomy. AI alone is not a category (classify by market served). Order
    most->least relevant; cap at 4; no dupes."""
    text = f"{name or ''} {tagline or ''} {description or ''}".lower()
    tags = []
    for tag, kws in KEYWORD_TAGS:
        if any(kw in text for kw in kws):
            tags.append(tag)
    return tags[:4]


def main():
    limit = int(sys.argv[sys.argv.index("--limit") + 1]) if "--limit" in sys.argv else None

    grid = fetch_grid()
    if limit:
        grid = grid[:limit]

    scraped_at = datetime.now(timezone.utc).isoformat()
    out = []
    for i, g in enumerate(grid, 1):
        print(f"[{i}/{len(grid)}] {g['grid_name']} -> {g['detail_url']}")
        detail = parse_detail(g["detail_url"])
        name = detail["company_name"] or g["grid_name"]
        tagline = detail["tagline"]
        description = detail["description"]
        out.append({
            "company_name": name,
            "tagline": tagline,
            "description": description,
            "company_url": detail["company_url"],
            "company_profile_url": detail["company_profile_url"],
            "logo_url": g["logo_url"],
            "founders": detail["founders"],
            "categories": detail["categories"],
            "everywhere_tags": everywhere_tags(name, tagline, description),
            "source_url": PORTFOLIO_URL,
            "scraped_at": scraped_at,
        })
        time.sleep(SLEEP_BETWEEN)

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    # ---- summary ----
    n = len(out)
    print(f"\nWrote {n} companies -> {OUT}")
    for field in ("tagline", "description", "company_url", "logo_url"):
        miss = sum(1 for r in out if not r[field])
        print(f"  {field:20s} missing: {miss}/{n}")
    print(f"  founders empty:      {sum(1 for r in out if not r['founders'])}/{n}")
    print(f"  categories empty:    {sum(1 for r in out if not r['categories'])}/{n}")
    untagged = [r["company_name"] for r in out if not r["everywhere_tags"]]
    print(f"  untagged:            {len(untagged)}/{n}" + (f" -> {untagged}" if untagged else ""))
    by_tag = {}
    for r in out:
        for t in r["everywhere_tags"]:
            by_tag[t] = by_tag.get(t, 0) + 1
    print("  by everywhere_tag:")
    for t, k in sorted(by_tag.items(), key=lambda x: -x[1]):
        print(f"    {k:3d}  {t}")


if __name__ == "__main__":
    main()
