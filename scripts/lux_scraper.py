#!/usr/bin/env python3
"""
Lux Capital portfolio scraper -> lux_companies.json

Scrapes Lux Capital's portfolio (https://www.luxcapital.com/companies) into a
JSON file. The site is a Webflow build. The companies grid at /companies is a
Finsweet CMS list (`fs-cmsload`, "load-under" mode = infinite scroll), so
rather than fight client-side lazy-loading we get the full, stable list of
company slugs from the site's own sitemap (`/sitemap.xml`, 213 `/companies/
<slug>` URLs) and crawl each company's own detail page
(`https://www.luxcapital.com/companies/<slug>`), which is fully
server-rendered and is a strict superset of what the grid card/modal shows
(same industries/milestones/founders, PLUS the external website + jobs link
the grid never exposes).

Per-company detail page (`.company-detail_component`):
  - `h1.company-detail_title`                    -> name (no status suffix seen)
  - `.company-detail_bio` (first, non-"hide")    -> description
  - `.company-detail_embed-media img`            -> cover image
  - `.company-detail_logo img`                   -> logo
  - `.company-detail_buttons a` (always 2, fixed order): "Open Positions" (jobs
    board link) then "Visit Website" (external site). Each is wrapped
    `w-condition-invisible` with `href="#"` when Lux has no such link for that
    company -- checked via the CSS class, not just the literal "#" href.
  - `.company-details_item` rows (label -> value, `w-condition-invisible` when
    empty): **industries** (Lux's own categories, one `<p>` each -- see below),
    **Milestones** (free-text lines -- see "Empty != absent" below), **Founders**
    (one `<p>` per name).

Not exposed on the detail page (so intentionally omitted, not N/A to invent):
the deal-team/"Lux partners" attribution. The grid page's `companies_item` card
does carry a `.company_partner-slugs` field (comma-separated team slugs), but
that grid only server-renders ~28/213 companies (Finsweet "load-under" =
client-side infinite scroll we can't drive without a JS-executing browser).
The "similar companies" strip at the bottom of a detail page reuses the same
card markup and occasionally happens to include the current company itself,
but only for ~1/4 of companies checked during recon, and even then it
sometimes disagreed with the grid's own value for the same company (e.g.
Databricks: "shahin-farshchi" there vs. "bilal-zuberi,brandon-reeves" on the
grid) -- too unreliable to ship.

Categories ("industries"): Lux tags each company with items from TWO of its own
taxonomies that share one field -- 5 "Mission" categories (Advancing Human
Health, Enabling Human Creativity + Free Expression, Increasing Productivity +
Efficiency, Progressing Science + Knowledge, Securing Life + Environment) and 5
"Technology" categories (Biology + Biochemistry, Chemistry + Material Science,
Engineering + Electronics, Infrastructure + Computer Science, Physics +
Aerospace). The detail page's "industries" prose strips the "+" and casing is
inconsistent ("Infrastructure computer science" vs "Infrastructure + Computer
Science"), so each `<p>` is canonicalized (whitespace/case-insensitive) back to
its proper display name via CATEGORY_CANON.

"Empty != absent": Lux has no structured status/exit/acquirer/ticker/founded-year
field, but the free-text **Milestones** block encodes exactly this across
several verb forms seen during recon: "Lux investment: YYYY", "Lux founded:
YYYY" / "Company founded: YYYY", "Acquired: YYYY", "Acquired by <X>: YYYY",
"Acquisition by <X>: YYYY", "Publicly listed: YYYY", "IPO: YYYY", and "IPO
(NASDAQ: GNCA) 2014" (ticker embedded). `parse_milestones()` regexes all of
these into `year_founded`, `lux_investment_year`, `status`, `acquirer`,
`exit_year`, `ticker_symbol`, while keeping the raw lines in `milestones` so
nothing is lost to an imperfect regex.

requirements:
    pip install requests beautifulsoup4

usage:
    python3 lux_scraper.py            # writes ../data/lux_companies.json
    python3 lux_scraper.py --limit 15 # only the first ~15 for a test run
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

BASE = "https://www.luxcapital.com"
SITEMAP_URL = f"{BASE}/sitemap.xml"
SOURCE_URL = f"{BASE}/companies"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "data", "lux_companies.json")

# --- Network workaround -------------------------------------------------
# This machine cannot route to Webflow's current CDN IP (cdn.webflow.com /
# luxcapital.com both resolve to 198.202.211.1, which times out here), and
# pinning the legacy Webflow IPs (75.2.70.75 / 99.83.190.102) via --resolve
# fails the TLS handshake for this domain too. As a last resort we fetch pages
# through the read-only relay r.jina.ai (which just serves the firm's own
# published HTML back to us) with header `x-respond-with: html` to get the
# raw markup instead of the relay's markdown conversion. The relay rate-limits
# aggressively (HTTP 429) under bursts, so requests are spaced out and retried
# with backoff.
JINA_PREFIX = "https://r.jina.ai/"
USE_RELAY = True

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "x-respond-with": "html",
}
TIMEOUT = 45
RETRIES = 4
SLEEP = 1.5

# Lux's 10 own categories (5 "Mission" + 5 "Technology") -> canonical display
# name. The detail page's "industries" prose strips "+" and has inconsistent
# casing/whitespace, so we match case/space-insensitively via a normalized key.
CATEGORY_CANON_RAW = [
    "Advancing Human Health",
    "Enabling Human Creativity + Free Expression",
    "Increasing Productivity + Efficiency",
    "Progressing Science + Knowledge",
    "Securing Life + Environment",
    "Biology + Biochemistry",
    "Chemistry + Material Science",
    "Engineering + Electronics",
    "Infrastructure + Computer Science",
    "Physics + Aerospace",
]


def _norm_key(s):
    s = unescape(s or "")
    s = s.replace("+", " ")
    s = re.sub(r"\s+", " ", s).strip().lower()
    return s


CATEGORY_CANON = {_norm_key(c): c for c in CATEGORY_CANON_RAW}
CANON_ORDER = CATEGORY_CANON_RAW

# Lux's own categories -> the 17-tag everywhere_tags taxonomy. Deliberately
# NOT mapped: "Increasing Productivity + Efficiency" (too broad -- spans dev
# tools/work/data), "Infrastructure + Computer Science" (spans dev
# tools/data/security), "Engineering + Electronics" and "Physics + Aerospace"
# (span deeptech/robotics/transportation/climate) -- these are left to the
# keyword classifier to refine from name + description.
SECTOR_TAG_MAP = {
    "Advancing Human Health": ["Health"],
    "Biology + Biochemistry": ["BioTech"],
    "Enabling Human Creativity + Free Expression": ["Gaming / Media / Entertainment"],
    "Securing Life + Environment": ["Climate / Sustainability"],
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
                           "no-code", "source code", "development platform", "incident", " sre", "voicemail", "communications",
                           "llm", "foundation model", "interpretability", "voice keyboard", "voice recognition", "frame relay",
                           "log management", "file sharing", "tech stack", "voice agent", "appliance software",
                           "text to speech"]),
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
                                   "aircraft", "electric vehicle", "scooter", " bike", "boat", "watercraft", "rideshar", "travel"]),
    ("Logistics / Supply Chain", ["logistics", "supply chain", "supply and demand", "freight", "warehouse",
                                  "delivery", "procurement", "inventory", "fulfillment", "shipping"]),
    ("PropTech", ["real estate", "property", "housing", "mortgage", "rental", "construction", "tenant"]),
    ("CPG", ["beverage", "snack", "consumer packaged", "beauty", "cosmetic", "apparel", "grocery", "skincare",
             "eyewear", "glasses", "footwear", "pet sitter", "pet "]),
    ("Climate / Sustainability", ["climate", "carbon", "renewable", "solar", "battery", "sustainab", "emission",
                                  "clean energy", "ev charging", "electrif", "energy", "power is produced", "power grid"]),
    ("RegTech/Gov/Legal", ["legal", "compliance", "government", "regulat", "law firm", "attorney", "risk services",
                           "lawsuit", "lawyer"]),
    ("Deeptech / Robotics / AR/VR", ["robot", "hardware", "semiconductor", "chip", "drone", "aerospace",
                                     "augmented reality", "virtual reality", "satellite", "quantum", "sensor", "rfid", "wifi",
                                     "space", "rocket", "launch vehicle", "optics", "defense"]),
    ("Consumer", ["marketplace", "consumer", "shopping", "social network", "community", "app for", "app that",
                  "ecommerce", "e-commerce", "subscription", "retailer", "universit", "student", "education",
                  "learning", "fashion"]),
]


def get(url):
    """GET url. When USE_RELAY, fetch it via r.jina.ai instead (see network
    workaround note above). Retries with backoff; a 429 from the relay gets a
    longer wait than a generic failure."""
    fetch_url = f"{JINA_PREFIX}{url}" if USE_RELAY else url
    last = None
    for attempt in range(1, RETRIES + 1):
        try:
            r = requests.get(fetch_url, headers=HEADERS, timeout=TIMEOUT)
            if r.status_code == 429:
                wait = 8 * attempt
                print(f"  ! 429 rate-limited; retry {attempt}/{RETRIES} in {wait:.1f}s", file=sys.stderr)
                time.sleep(wait)
                continue
            r.raise_for_status()
            return r.text
        except requests.RequestException as e:  # noqa
            last = e
            wait = 2.0 * attempt
            print(f"  ! request failed ({e}); retry {attempt}/{RETRIES} in {wait:.1f}s", file=sys.stderr)
            time.sleep(wait)
    raise SystemExit(f"FATAL: could not fetch {url}: {last}")


def clean(s):
    if s is None:
        return None
    s = re.sub(r"\s+", " ", unescape(s)).strip()
    return s or None


def to_year(s):
    if not s:
        return None
    m = re.search(r"(19|20)\d{2}", s)
    return int(m.group(0)) if m else None


def fetch_slugs():
    """Company slugs come from the site's own sitemap, not the grid page --
    the /companies grid is a Finsweet 'load-under' (infinite scroll) list that
    only server-renders the first ~28 items; the sitemap lists all of them.

    A few sitemap entries use mixed case (e.g. "Nominal", "eGenesis",
    "Perchwell"). Routing turned out NOT to be reliably case-insensitive --
    lowercasing these 404'd for some (eGenesis) despite working for a
    different one tested during recon (nominal) -- so the original sitemap
    casing is preserved verbatim; only exact-string duplicates are dropped."""
    xml = get(SITEMAP_URL)
    slugs = sorted(set(re.findall(r"luxcapital\.com/companies/([A-Za-z0-9-]+)", xml)))
    return slugs


STATUS_PATTERNS = [
    (re.compile(r"acquisition\s+by\s+(.+?)(?::|$)", re.I), "acquired"),
    (re.compile(r"acquired\s+by\s+(.+?)(?::|$)", re.I), "acquired"),
    (re.compile(r"^acquired\s*:?\s*$", re.I), "acquired_only"),
    (re.compile(r"^acquired$", re.I), "acquired_only"),
    (re.compile(r"publicly\s+listed", re.I), "public"),
    (re.compile(r"\bipo\b", re.I), "public"),
]


def parse_milestones(lines):
    """Lux has no structured status/exit/acquirer/ticker/founded field; that
    info lives only in the free-text Milestones lines. Returns a dict of
    derived fields; `lines` themselves are kept verbatim in the output too."""
    year_founded = None
    lux_investment_year = None
    status = "Active"
    acquirer = None
    exit_year = None
    ticker_symbol = None

    for raw in lines:
        line = clean(raw) or ""
        low = line.lower()

        if "lux investment" in low or "lux founded" in low:
            yr = to_year(line)
            if "founded" in low:
                year_founded = yr
            else:
                lux_investment_year = yr
            continue
        if low.startswith("company founded"):
            year_founded = to_year(line)
            continue

        # ticker embedded like "IPO (NASDAQ: GNCA) 2014"
        tick = re.search(r"\(([A-Za-z]{2,10}):\s*([A-Za-z.\-]{1,8})\)", line)
        if tick:
            ticker_symbol = f"{tick.group(1).upper()}: {tick.group(2).upper()}"

        m = re.search(r"acquisition\s+by\s+(.+?)\s*:\s*(\d{4})", line, re.I)
        if not m:
            m = re.search(r"acquired\s+by\s+(.+?)\s*:\s*(\d{4})", line, re.I)
        if m:
            status = "Acquired"
            acquirer = clean(m.group(1))
            exit_year = int(m.group(2))
            continue

        m = re.search(r"^acquired\s*:\s*(\d{4})", line, re.I)
        if m:
            status = "Acquired"
            exit_year = int(m.group(1))
            continue

        if "publicly listed" in low:
            status = "Public"
            exit_year = to_year(line)
            continue
        if "ipo" in low:
            status = "Public"
            yr = to_year(line)
            if yr:
                exit_year = yr
            continue

    return {
        "year_founded": year_founded,
        "lux_investment_year": lux_investment_year,
        "status": status,
        "acquirer": acquirer,
        "exit_year": exit_year,
        "ticker_symbol": ticker_symbol,
    }


def everywhere_tags(name, description, categories):
    """Lux categories first (mapped via SECTOR_TAG_MAP), then keyword fallback
    on name + description to add/refine. Order most->least relevant, cap at 4."""
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


def parse_detail_page(html, slug):
    soup = BeautifulSoup(html, "html.parser")

    comp = soup.select_one(".company-detail_component")
    if not comp:
        return None

    h1 = comp.select_one("h1.company-detail_title")
    name = clean(h1.get_text()) if h1 else None
    if not name:
        return None

    bio = None
    for el in comp.select(".company-detail_bio"):
        classes = el.get("class") or []
        if "hide" in classes:  # decoy placeholder-text block, not real content
            continue
        txt = clean(el.get_text())
        if txt:
            bio = txt
            break

    logo_img = comp.select_one(".company-detail_logo img")
    logo_url = clean(logo_img.get("src")) if logo_img else None

    cover_img = comp.select_one(".company-detail_embed-media img")
    cover_image_url = clean(cover_img.get("src")) if cover_img else None

    # buttons: fixed order [Open Positions, Visit Website]; each is
    # w-condition-invisible (href="#") when Lux has no such link for this company
    jobs_url = None
    company_url = None
    for a in comp.select(".company-detail_buttons a"):
        classes = a.get("class") or []
        if "w-condition-invisible" in classes:
            continue
        href = clean(a.get("href"))
        label = clean(a.get_text())
        if label and "open positions" in label.lower():
            jobs_url = href
        elif label and "visit website" in label.lower():
            company_url = href
        elif href and href != "#":
            company_url = company_url or href

    # industries / Milestones / Founders detail rows
    categories, milestone_lines, founders_raw = [], [], []
    for item in comp.select(".company-details_item"):
        classes = item.get("class") or []
        label_el = item.select_one(".company-details_label")
        if not label_el:
            continue
        label = clean(label_el.get_text()) or ""
        if "w-condition-invisible" in classes:
            continue  # empty for this company
        text_el = item.select_one(".company-details_text")
        if not text_el:
            continue
        ps = [clean(p.get_text()) for p in text_el.select("p")]
        ps = [p for p in ps if p]

        low = label.lower()
        if low == "industries":
            for p in ps:
                canon = CATEGORY_CANON.get(_norm_key(p))
                if canon and canon not in categories:
                    categories.append(canon)
        elif low == "milestones":
            milestone_lines.extend(ps)
        elif low == "founders":
            founders_raw.extend(ps)

    categories_sorted = [c for c in CANON_ORDER if c in categories]

    milestone_fields = parse_milestones(milestone_lines)

    return {
        "company_name": name,
        "description": bio,
        "company_url": company_url,
        "company_profile_url": f"{BASE}/companies/{slug}",
        "jobs_url": jobs_url,
        "logo_url": logo_url,
        "cover_image_url": cover_image_url,
        "categories": categories_sorted,
        "founders": founders_raw,
        "milestones": milestone_lines,
        **milestone_fields,
        "everywhere_tags": everywhere_tags(name, bio, categories_sorted),
        "source_url": SOURCE_URL,
    }


def main():
    limit = int(sys.argv[sys.argv.index("--limit") + 1]) if "--limit" in sys.argv else None

    print("Fetching sitemap for company slugs...")
    slugs = fetch_slugs()
    print(f"  found {len(slugs)} company slugs")
    if limit:
        slugs = slugs[:limit]

    scraped_at = datetime.now(timezone.utc).isoformat()
    out = []
    for i, slug in enumerate(slugs, 1):
        url = f"{BASE}/companies/{slug}"
        print(f"[{i}/{len(slugs)}] {slug}")
        html = get(url)
        rec = parse_detail_page(html, slug)
        if not rec:
            print(f"  ! could not parse {slug}", file=sys.stderr)
            continue
        rec["scraped_at"] = scraped_at
        out.append(rec)
        time.sleep(SLEEP)

    out.sort(key=lambda o: o["company_name"].lower())

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2)

    # ---- summary ----
    from collections import Counter
    n = len(out)
    print(f"\nwrote {n} companies -> {OUT}")
    for field in ("description", "company_url", "jobs_url", "logo_url", "cover_image_url"):
        miss = sum(1 for r in out if not r[field])
        print(f"  {field:18s} missing: {miss}/{n}")
    print(f"  categories empty:  {sum(1 for r in out if not r['categories'])}/{n}")
    print(f"  founders empty:    {sum(1 for r in out if not r['founders'])}/{n}")
    by_status = Counter(r["status"] for r in out)
    print("  by status:", dict(by_status),
          "| with acquirer:", sum(1 for r in out if r["acquirer"]),
          "| with ticker:", sum(1 for r in out if r["ticker_symbol"]),
          "| with year_founded:", sum(1 for r in out if r["year_founded"]),
          "| with lux_investment_year:", sum(1 for r in out if r["lux_investment_year"]))
    untagged = [r["company_name"] for r in out if not r["everywhere_tags"]]
    print(f"  untagged:          {len(untagged)}/{n}" + (f" -> {untagged}" if untagged else ""))
    by_cat = Counter(c for r in out for c in r["categories"])
    print("  by category:")
    for t, c in by_cat.most_common():
        print(f"    {c:3d}  {t}")
    by_tag = Counter(t for r in out for t in r["everywhere_tags"])
    print("  by everywhere_tag:")
    for t, c in by_tag.most_common():
        print(f"    {c:3d}  {t}")


if __name__ == "__main__":
    main()
