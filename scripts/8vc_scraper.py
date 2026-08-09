#!/usr/bin/env python3
"""
8VC portfolio scraper -> 8vc_companies.json

Scrapes 8VC's portfolio (https://www.8vc.com/companies) into a JSON file. The
site is a Webflow build with a client-side-filtered Finsweet CMS list; the full
company grid is server-rendered across TWO parallel `.companies-collection_wrapper`
lists on the one page (100 + 72 items, no overlap = 172 companies total, matching
the site's own sitemap.xml company-URL count). Each `.company-card_item` gives:
name, description, external website/Twitter/LinkedIn links (always 4 `a.card-link`
in fixed order: 8VC detail page, website, Twitter/X, LinkedIn -- `#` = absent),
logo, and a `stage` value (Seed/Series A/Series B/Series C+/Growth/Exited).

**Industry is an "Empty ≠ absent" trap**: the card grid only renders the
`fs-cmsfilter-field="industry"` tag for a minority of cards (the first wrapper
renders 0/100 -- it's AJAX-loaded client-side via `#tag-<slug>` `.load()` calls
this scraper doesn't execute; the second wrapper renders 57/72). The individual
company detail page (`/companies/<slug>`), however, reliably server-renders its
own industry tag(s) -- but NOT via a page-wide `[fs-cmsfilter-field="industry"]`
select(): that attribute is reused inside a generic "related companies" swiper
carousel that's byte-identical on every detail page (it would make every company
look like "AI, Financial Services"). The real, company-specific tag(s) live in
`.card-tag .text-size-small` elements scoped to the `<h1>`'s
`.team-member_heading-wrapper` container. This scraper crawls all 172 detail
pages (correctly scoped) and UNIONS each company's grid-rendered industry tags
with its detail-page tags. 8VC's 9 industry categories: AI, Smart Enterprise,
Consumer, Financial Services, Government & Defense, IT Infrastructure,
Healthcare, Life Sciences, Logistics (+ "Exited", which is a stage value
bleeding into the industry filter list -- excluded here since `stage` already
captures it).

What 8VC does NOT expose (checked names + descriptions per "Empty ≠ absent" --
Palantir's description is the one hit, "(NYSE: PLTR)", a real ticker, so a best-
effort regex mines `ticker_symbol` from the description text; no other company
in the sample had one): founders, founded year, headquarters/region. The detail
page DOES contain a Stage/Founded/Region/Fund "info-cell" template, but it is
`w-dyn-bind-empty` + `w-condition-invisible` for every company checked (incl.
Palantir, Anduril, Addepar, Hims, Deliverr, 180 Insurance) -- a genuinely unused
template, not denormalized data, so those fields are intentionally omitted
rather than emitted as always-null columns.

*** NETWORK CAVEAT ***
www.8vc.com is DIRECTLY UNREACHABLE from the machine this scraper was written
and run on: the current Webflow/Cloudflare CDN IP is unroutable, and both
legacy IPs (75.2.70.75, 99.83.190.102) reject the TLS handshake for this
hostname. This scraper therefore tries, in order: (1) direct HTTPS, (2) the
legacy IPs pinned via SNI, (3) the read-only relay `r.jina.ai` (which fetches
https://www.8vc.com/... server-side and returns the raw HTML via the
`x-respond-with: html` header). All data below came from the relay fallback --
it serves 8VC's own page content verbatim (no third-party enrichment), but
parsed output should be spot-checked extra carefully since it's one hop removed
from the origin. On a healthy network this script will use the direct route
unchanged. Politeness against the relay: ~1 request / 1.5s (it's shared
infrastructure), custom UA, timeouts, retries with backoff.

requirements:
    pip install requests beautifulsoup4

usage:
    python3 8vc_scraper.py            # writes ../data/8vc_companies.json
    python3 8vc_scraper.py --limit 15 # only the first ~15 for a test run
"""

import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

HOST = "www.8vc.com"
COMPANIES_URL = f"https://{HOST}/companies"
SOURCE_URL = COMPANIES_URL
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "data", "8vc_companies.json")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}
TIMEOUT = 45
RETRIES = 3
RELAY_SLEEP = 1.5   # politeness: r.jina.ai is shared infrastructure
DIRECT_SLEEP = 0.6

LEGACY_IPS = ["75.2.70.75", "99.83.190.102"]   # legacy Webflow/Fastly IPs (known to TLS-reject; tried anyway)

# 8VC's 9 industry categories -> the 17-tag everywhere_tags taxonomy. "AI" and
# "Smart Enterprise" are intentionally NOT mapped: AI alone is not a category
# (classify by the market served) and "Smart Enterprise" has no single tag --
# both left to the keyword fallback below.
SECTOR_TAG_MAP = {
    "Financial Services": ["FinTech / Insurance"],
    "Healthcare": ["Health"],
    "Life Sciences": ["BioTech"],
    "Logistics": ["Logistics / Supply Chain"],
    "Government & Defense": ["RegTech/Gov/Legal"],
    "IT Infrastructure": ["Dev Tools / Cloud"],
    "Consumer": ["Consumer"],
}

# everywhere_tags keyword classifier (substrings, lowercased) -- copied from
# rre_scraper.py / iconiq_scraper.py.
KEYWORD_TAGS = [
    ("BioTech", ["biotech", "drug", "therapeut", "oncolog", "cancer", "tumor", "genomic", "genome",
                 "molecul", "antibod", "protein", "vaccine", "clinical-stage", "medicine", "opioid", "life science",
                 "synthetic biology", "biolog"]),
    ("Health", ["healthcare", "health care", "patient", "clinic", "medical", "mental health", "telehealth",
                "health system", "health record", "diagnos", "surgical", "doctor", "hospital", "pharmac", "therapy"]),
    ("Cybersecurity", ["cybersecurity", "security", "secure", "privacy", "fraud", "phishing", "malware",
                       "ransomware", "endpoint", "zero trust", "vulnerab", "authentication", "threat", "defense system", "identity",
                       "information protection"]),
    ("FinTech / Insurance", ["fintech", "payment", "bank", "lending", "loan", "insurance", "credit", "trading",
                             "wallet", "financ", "invoic", "accounting", "payroll", "treasury", "billing", "pricing platform",
                             "rebate", " tax", "audit", "money management", "robo-advisor", "brokerage", "spend management",
                             "capital markets", "investing", "claims"]),
    ("Web3 / Crypto", ["crypto", "blockchain", "web3", "token", "on-chain", "ethereum", "bitcoin", "decentral", "stablecoin", "nft"]),
    ("Gaming / Media / Entertainment", ["game", "gaming", "music", "video", "creator", "content", "publish",
                                        "entertain", "newsletter", "podcast", "film", "streaming", "social media", "media platform"]),
    ("Dev Tools / Cloud", ["developer", " api ", "apis", "api platform", "infrastructure", "database", "cloud",
                           "open source", "devops", "sdk", "kubernetes", "container", "observability", "deploy", "compute",
                           "storage", "serverless", "inference", "networking", "ethernet", "coding", "codebase", "low-code",
                           "no-code", "source code", "development platform", "incident", " sre", "communications",
                           "llm", "foundation model", "interpretability", "log management", "file sharing", "tech stack"]),
    ("Data & Analytics", ["analytics", "business intelligence", "data platform", "data warehouse", "data lake",
                          "data pipeline", "insights", "dashboard", "experimentation", "decision intelligence", "data quality",
                          "analyz", "data curation", "quality management", "relationship intelligence",
                          "data discovery", "data analysis", "edge-data", "complex data", "real-world data", "analyst",
                          "data intelligence", "data transformation", "data integration"]),
    ("Future of Work", ["workforce", "hiring", "recruit", "employee", "productivity", "collaborat", "talent",
                        "workplace", "human resources", " hr ", "learning platform", "customer success", "customer service",
                        "customer support", "presales", " sales ", "onboarding", "workflow", "saas management", "ai assistant",
                        "project management", "partnerships platform", "partnership", "teamwork", "scheduling", "work assistant"]),
    ("Transportation / Mobility", ["mobility", "vehicle", "transport", "autonomous", "fleet", "driving", "aviation",
                                   "aircraft", "electric vehicle", "e-mobility", "scooter", " bike", "boat", "watercraft",
                                   "rideshar", "travel", "trucking", "truck driver"]),
    ("Logistics / Supply Chain", ["logistics", "supply chain", "supply and demand", "freight", "warehouse",
                                  "delivery", "procurement", "inventory", "fulfillment", "shipping", "dwell", "detention"]),
    ("PropTech", ["real estate", "property", "housing", "mortgage", "rental", "construction", "tenant"]),
    ("CPG", ["beverage", "snack", "consumer packaged", "beauty", "cosmetic", "apparel", "grocery", "skincare",
             "eyewear", "glasses", "footwear", "pet sitter", "pet "]),
    ("Climate / Sustainability", ["climate", "carbon", "renewable", "solar", "battery", "sustainab", "emission",
                                  "clean energy", "ev charging", "electrif", "energy management", "power grid"]),
    ("RegTech/Gov/Legal", ["legal", "compliance", "government", "regulat", "law firm", "attorney", "risk services",
                           "lawsuit", "lawyer", "defense", "military", "national security"]),
    ("Deeptech / Robotics / AR/VR", ["robot", "hardware", "semiconductor", "chip", "drone", "aerospace",
                                     "augmented reality", "virtual reality", "satellite", "quantum", "sensor", "rfid", "wifi",
                                     "space", "rocket", "launch vehicle", "optics", "manufactur"]),
    ("Consumer", ["marketplace", "consumer", "shopping", "social network", "community", "app for", "app that",
                  "ecommerce", "e-commerce", "subscription", "retailer", "universit", "student", "education",
                  "learning", "fashion"]),
]


def fetch(url):
    """GET url. Tries direct HTTPS, then legacy-IP-pinned HTTPS, then the r.jina.ai
    relay (which fetches the URL server-side and returns raw HTML via the
    `x-respond-with: html` header). Returns response text or raises SystemExit."""
    last = None

    # 1. direct
    for attempt in range(1, RETRIES + 1):
        try:
            r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
            r.raise_for_status()
            time.sleep(DIRECT_SLEEP)
            return r.text
        except requests.RequestException as e:  # noqa
            last = e
            break  # don't burn retries on a route already known-bad; fall through fast

    # 2. legacy IP pin (SNI-preserving via a Host header + explicit IP substitution)
    for ip in LEGACY_IPS:
        try:
            pinned = url.replace(f"https://{HOST}", f"https://{ip}")
            r = requests.get(pinned, headers={**HEADERS, "Host": HOST}, timeout=15, verify=False)
            r.raise_for_status()
            time.sleep(DIRECT_SLEEP)
            return r.text
        except requests.RequestException as e:  # noqa
            last = e

    # 3. relay fallback (r.jina.ai) -- shared infra, so retries/backoff + slower pacing
    relay_url = f"https://r.jina.ai/{url}"
    relay_headers = {**HEADERS, "x-respond-with": "html"}
    for attempt in range(1, RETRIES + 1):
        try:
            r = requests.get(relay_url, headers=relay_headers, timeout=TIMEOUT)
            r.raise_for_status()
            time.sleep(RELAY_SLEEP)
            return r.text
        except requests.RequestException as e:  # noqa
            last = e
            wait = 2.0 * attempt
            print(f"  ! relay fetch failed ({e}); retry {attempt}/{RETRIES} in {wait:.1f}s", file=sys.stderr)
            time.sleep(wait)

    raise SystemExit(f"FATAL: could not fetch {url} via direct, legacy IP, or relay: {last}")


def clean(s):
    if s is None:
        return None
    s = re.sub(r"\s+", " ", s).strip()
    return s or None


def parse_ticker(description):
    """8VC rarely states a ticker in prose; Palantir's is the model case:
    "Palantir (NYSE: PLTR) helps...". Best-effort regex; returns None if absent."""
    if not description:
        return None
    m = re.search(r"\((NYSE|NASDAQ|LSE|TSX):\s*([A-Z.\-]{1,8})\)", description)
    if m:
        return f"{m.group(1)}: {m.group(2)}"
    return None


def everywhere_tags(name, description, industries):
    """8VC industries first (mapped via SECTOR_TAG_MAP), then keyword fallback on
    name + description to add/refine. Order most->least relevant, cap at 4."""
    tags = []
    for ind in industries:
        for mapped in SECTOR_TAG_MAP.get(ind, []):
            if mapped not in tags:
                tags.append(mapped)
    text = f"{name or ''} {description or ''}".lower()
    for tag, kws in KEYWORD_TAGS:
        if tag in tags:
            continue
        if any(kw in text for kw in kws):
            tags.append(tag)
    return tags[:4]


def parse_card(item):
    name_el = item.select_one('[fs-cmsfilter-field="name"]')
    name = clean(name_el.get_text()) if name_el else None
    if not name:
        return None

    desc_el = item.select_one(".card-description-text")
    description = clean(desc_el.get_text()) if desc_el else None

    stage_el = item.select_one('[fs-cmsfilter-field="stage"]')
    stage = clean(stage_el.get_text()) if stage_el else None

    # the 2nd companies-collection wrapper renders the logo <img> with no class
    # at all (only the 1st wrapper uses `company-logo_image`), so select via the
    # shared `.company-logo_wrapper` container instead of the img's own class.
    logo_wrap = item.select_one(".company-logo_wrapper")
    logo = logo_wrap.select_one("img") if logo_wrap else None
    logo_url = clean(logo.get("src")) if logo and logo.get("src") else None

    # fixed 4-link order: [0] 8VC detail page, [1] website, [2] twitter/X, [3] linkedin
    links = item.select("a.card-link")
    detail_path = clean(links[0].get("href")) if len(links) > 0 else None
    company_url = clean(links[1].get("href")) if len(links) > 1 and links[1].get("href") not in (None, "#") else None
    twitter_url = clean(links[2].get("href")) if len(links) > 2 and links[2].get("href") not in (None, "#") else None
    linkedin_url = clean(links[3].get("href")) if len(links) > 3 and links[3].get("href") not in (None, "#") else None

    detail_url = urljoin(f"https://{HOST}/", detail_path) if detail_path and detail_path != "#" else None

    industries = []
    for ind in item.select('[fs-cmsfilter-field="industry"]'):
        v = clean(ind.get_text())
        if v and v not in industries:
            industries.append(v)

    return {
        "company_name": name,
        "description": description,
        "company_url": company_url,
        "logo_url": logo_url,
        "stage": stage,
        "industries": industries,
        "twitter_url": twitter_url,
        "linkedin_url": linkedin_url,
        "detail_url": detail_url,
    }


def fetch_detail_industries(detail_url):
    """Fetch a company's 8VC detail page and return its server-rendered industry
    tags (deduped, order preserved). Returns [] on any parse miss.

    NOTE: `[fs-cmsfilter-field="industry"]` is reused site-wide (it also appears
    inside a generic "related companies" swiper carousel that's identical on
    every detail page), so a page-wide select() there wrongly returns the SAME
    tags ("AI", "Financial Services") for every company. The company's own
    industry tag(s) live only in the `.card-tag .text-size-small` list sitting
    directly beside the page's `<h1>` (inside `.team-member_heading-wrapper`) --
    scope to that container instead."""
    html = fetch(detail_url)
    soup = BeautifulSoup(html, "html.parser")
    h1 = soup.select_one("h1")
    if not h1:
        return []
    wrap = h1.find_parent("div", class_="team-member_heading-wrapper") or h1.parent
    industries = []
    for tag in wrap.select(".card-tag .text-size-small"):
        v = clean(tag.get_text())
        if v and v not in industries:
            industries.append(v)
    return industries


def main():
    limit = int(sys.argv[sys.argv.index("--limit") + 1]) if "--limit" in sys.argv else None

    html = fetch(COMPANIES_URL)
    soup = BeautifulSoup(html, "html.parser")

    wrappers = soup.select(".companies-collection_wrapper")
    print(f"found {len(wrappers)} companies-collection_wrapper list(s)")

    rows, seen = [], set()
    for w in wrappers:
        for item in w.select(".company-card_item"):
            rec = parse_card(item)
            if not rec or rec["company_name"] in seen:
                continue
            seen.add(rec["company_name"])
            rows.append(rec)

    rows.sort(key=lambda r: r["company_name"].lower())
    if limit:
        rows = rows[:limit]

    scraped_at = datetime.now(timezone.utc).isoformat()
    out = []
    for i, rec in enumerate(rows, 1):
        # crawl the detail page to fill/union the industry tag(s) -- the grid only
        # server-renders industry for a minority of cards (see docstring)
        detail_industries = []
        if rec["detail_url"]:
            try:
                detail_industries = fetch_detail_industries(rec["detail_url"])
            except SystemExit as e:
                print(f"  ! giving up on detail page for '{rec['company_name']}': {e}", file=sys.stderr)

        industries = list(rec["industries"])
        for v in detail_industries:
            if v and v not in industries:
                industries.append(v)
        # "Exited" is a stage value that leaks into the industry filter list; keep
        # it out of `industries` since `stage` already captures it.
        industries = [v for v in industries if v.lower() != "exited"]

        out.append({
            "company_name": rec["company_name"],
            "description": rec["description"],
            "company_url": rec["company_url"],
            "logo_url": rec["logo_url"],
            "stage": rec["stage"] or None,
            "industries": industries,
            "twitter_url": rec["twitter_url"],
            "linkedin_url": rec["linkedin_url"],
            "ticker_symbol": parse_ticker(rec["description"]),
            "everywhere_tags": everywhere_tags(rec["company_name"], rec["description"], industries),
            "detail_url": rec["detail_url"],
            "source_url": SOURCE_URL,
            "scraped_at": scraped_at,
        })
        print(f"  [{i}/{len(rows)}] {rec['company_name']} -> industries={industries} stage={rec['stage']!r}")

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2)

    # ---- summary ----
    n = len(out)
    print(f"\nwrote {n} companies -> {OUT}")
    for field in ("description", "company_url", "logo_url", "stage", "twitter_url", "linkedin_url", "ticker_symbol"):
        miss = sum(1 for r in out if not r[field])
        print(f"  {field:14s} missing: {miss}/{n}")
    print(f"  industries empty: {sum(1 for r in out if not r['industries'])}/{n}")
    from collections import Counter
    by_stage = Counter(r["stage"] for r in out if r["stage"])
    by_ind = Counter(v for r in out for v in r["industries"])
    by_tag = Counter(t for r in out for t in r["everywhere_tags"])
    untagged = [r["company_name"] for r in out if not r["everywhere_tags"]]
    print(f"  untagged:      {len(untagged)}/{n}" + (f" -> {untagged}" if untagged else ""))
    print("  by stage:", dict(by_stage.most_common()))
    print("  by industry:")
    for t, c in by_ind.most_common():
        print(f"    {c:3d}  {t}")
    print("  by everywhere_tag:")
    for t, c in by_tag.most_common():
        print(f"    {c:3d}  {t}")


if __name__ == "__main__":
    main()
