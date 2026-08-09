#!/usr/bin/env python3
"""
Kleiner Perkins portfolio scraper -> kleinerperkins_companies.json

Site: https://www.kleinerperkins.com/partnerships -- WordPress theme with a
custom "companyTable" / "companyModals" component set (not Webflow/Finsweet,
not a JS API). The ENTIRE portfolio -- both the sortable/filterable table rows
AND every company's detail "modal" -- is server-rendered into one static page,
so a single GET is enough; no pagination, no per-company requests.

Two parallel tables live on the page (`<div id="featured">` and
`<div id="all">`), each holding `<li class="js-items" data-column="{...}">`
rows with an HTML-entity-encoded JSON blob (name/sector/first_invested/stage/
since) plus a `data-id` that links to a `<div class="... js-companies"
data-id="N">` modal further down the page. "featured" is a 47-company curated
subset of "all" (both point at the same modals) -- scraping only the "all"
table's 385 rows avoids double-counting; this was verified by diffing the two
tables (432 total <li> rows across both, 385 unique names, and "all" alone
already contains all 385 uniques).

Each modal supplies: description (prose, sometimes with embedded <p>/<a>
formatting -- HTML-stripped here), an external "Website" link (misc navigation
links to a KP partner's own site can appear right after it in the same <ul>,
so a scoped regex isolates only the pre-<ul> link block), "Partnered Since"
(year + first_invested stage label), "Stage" (KP's own combined
lifecycle/exit field -- one to three tokens: a lifecycle word [Early/Growth],
an exit type [IPO/Acquired], and for exits a ticker (IPO) or acquirer name
(Acquired); some long-lived companies show both, e.g. ["IPO","Acquired","HP"]
meaning Compaq IPO'd then was later acquired by HP), optional "Founders" (name
spans) and optional "Partners" (links to the KP partner's own /people/ page --
these are KP's own site navigation, not third-party enrichment).

No logo/location fields are published per company (the only <img> tags inside
the modals belong to a "Related perspectives" article carousel, not a company
logo -- verified by inspecting the src filenames, e.g. "Alkira-x-Lumen-
Perspectives.jpg"). `sectors` values (AI, Consumer, Enterprise, Fintech,
Hardtech, Healthcare) come straight from KP's own row data.

requirements:
    pip install requests beautifulsoup4   (bs4 unused here but kept for parity)

usage:
    python3 kleinerperkins_scraper.py             # writes ../data/kleinerperkins_companies.json
    python3 kleinerperkins_scraper.py --limit 40  # first 40 companies only, for a test run
"""

import json
import os
import re
import sys
import time
import html as html_module
from collections import Counter
from datetime import datetime, timezone

import requests

BASE_URL = "https://www.kleinerperkins.com/partnerships"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "data", "kleinerperkins_companies.json")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}
TIMEOUT = 45
RETRIES = 3

# Map Kleiner Perkins' own sector labels -> the 17-tag everywhere_tags taxonomy.
# "AI" and "Enterprise" are intentionally NOT mapped here: AI alone is never a
# category (classify by the market it serves), and "Enterprise" is too broad
# for a single tag (spans dev tools, data, work, security, ...) -- both are
# left entirely to the keyword classifier below.
SECTOR_TAG_MAP = {
    "Consumer": "Consumer",
    "Fintech": "FinTech / Insurance",
    "Healthcare": "Health",
    "Hardtech": "Deeptech / Robotics / AR/VR",
}

# everywhere_tags keyword classifier (substrings, lowercased) -- copied from
# rre_scraper.py / menlo_scraper.py / insight_scraper.py.
KEYWORD_TAGS = [
    ("BioTech", ["biotech", "drug", "therapeut", "oncolog", "cancer", "tumor", "genomic", "genome",
                 "molecul", "antibod", "protein", "vaccine", "clinical-stage", "medicine", "opioid", "life science",
                 "synthetic biology", "biolog"]),
    ("Health", ["healthcare", "health care", "patient", "clinic", "medical", "mental health", "telehealth",
                "health system", "health record", "diagnos", "surgical", "doctor", "hospital", "pharmac", "therapy",
                "fertility", "wellness"]),
    ("Cybersecurity", ["cybersecurity", "security", "secure", "privacy", "fraud", "phishing", "malware",
                       "ransomware", "endpoint", "zero trust", "vulnerab", "authentication", "threat", "defense system", "identity",
                       "information protection"]),
    ("FinTech / Insurance", ["fintech", "payment", "bank", "lending", "loan", "insurance", "credit", "trading",
                             "wallet", "financ", "invoic", "accounting", "payroll", "treasury", "billing", "pricing platform",
                             "rebate", " tax", "audit", "money management", "robo-advisor", "brokerage", "spend management",
                             "capital markets"]),
    ("Web3 / Crypto", ["crypto", "blockchain", "web3", "token", "on-chain", "ethereum", "bitcoin", "decentral", "stablecoin", "nft"]),
    ("Gaming / Media / Entertainment", ["game", "gaming", "music", "video", "creator", "content", "publish",
                                        "entertain", "newsletter", "podcast", "film", "streaming", "social media", "media platform",
                                        "comic"]),
    ("Dev Tools / Cloud", ["developer", " api ", "apis", "api platform", "infrastructure", "database", "cloud",
                           "open source", "devops", "sdk", "kubernetes", "container", "observability", "deploy", "compute",
                           "storage", "serverless", "inference", "networking", "ethernet", "coding", "codebase", "low-code",
                           "no-code", "source code", "development platform", "incident", " sre", "voicemail", "communications",
                           "llm", "foundation model", "interpretability", "voice keyboard", "voice recognition", "frame relay",
                           "log management", "file sharing", "tech stack", "voice agent", "appliance software", "javascript",
                           "ci/cd", "crash reporting", "runtime"]),
    ("Data & Analytics", ["analytics", "business intelligence", "data platform", "data warehouse", "data lake",
                          "data pipeline", "insights", "dashboard", "experimentation", "decision intelligence", "data quality",
                          "analyz", "data curation", "quality management", "relationship intelligence",
                          "data discovery", "data analysis", "edge-data", "complex data", "real-world data"]),
    ("Future of Work", ["workforce", "hiring", "recruit", "employee", "productivity", "collaboration", "talent",
                        "workplace", "human resources", " hr ", "learning platform", "customer success", "customer service",
                        "customer support", "presales", " sales ", "onboarding", "workflow", "saas management", "ai assistant",
                        "project management", "partnerships platform", "partnership"]),
    ("Transportation / Mobility", ["mobility", "vehicle", "transport", "autonomous", "fleet", "driving", "aviation",
                                   "aircraft", "electric vehicle", "scooter", " bike", "boat", "watercraft", "rideshar", "travel"]),
    ("Logistics / Supply Chain", ["logistics", "supply chain", "supply and demand", "freight", "warehouse",
                                  "delivery", "procurement", "inventory", "fulfillment", "shipping"]),
    ("PropTech", ["real estate", "property", "housing", "mortgage", "rental", "construction", "tenant"]),
    ("CPG", ["beverage", "snack", "consumer packaged", "beauty", "cosmetic", "apparel", "grocery", "skincare",
             "eyewear", "glasses", "footwear", "pet sitter", "pet "]),
    ("Climate / Sustainability", ["climate", "carbon", "renewable", "solar", "battery", "sustainab", "emission",
                                  "clean energy", "ev charging", "electrif", "energy", "power is produced", "power grid"]),
    ("RegTech/Gov/Legal", ["legal", "compliance", "government", "regulat", "law firm", "attorney", "risk services"]),
    ("Deeptech / Robotics / AR/VR", ["robot", "hardware", "semiconductor", "chip", "drone", "aerospace",
                                     "augmented reality", "virtual reality", "satellite", "quantum", "sensor", "rfid", "wifi",
                                     "space", "rocket", "launch vehicle", "physical ai", "iot"]),
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
    s = html_module.unescape(s)
    s = re.sub(r"<[^>]+>", " ", s)
    s = s.replace("\xa0", " ")
    s = re.sub(r"\s+", " ", s).strip()
    return s or None


def to_year(s):
    s = clean(s)
    if s and s.isdigit() and 1900 <= int(s) <= 2100:
        return int(s)
    return None


ROW_RE = re.compile(
    r'<li class="js-items[^"]*" data-column="(&#x7B;.*?&#x7D;)">\s*'
    r'<button class="w-full items-baseline text-left" data-component="companyModalToggle" data-id="(\d+)">',
    re.S,
)

MODAL_SPLIT_RE = re.compile(
    r'<div\s+class="absolute w-full hidden js-companies"\s*\n\s*data-id="(\d+)"\s*\n\s*data-current="\d+"\s*\n\s*>',
    re.S,
)

LI_FIELD_RE = re.compile(
    r'<li class="flex max-sm:flex-col sm:items-baseline gap-12 py-10 sm:py-20">(.*?)</li>', re.S
)
H3_RE = re.compile(r'<h3 class="text-current/75">\s*(.*?)\s*</h3>', re.S)
SPAN_RE = re.compile(r"<span[^>]*>(.*?)</span>", re.S)
LINK_RE = re.compile(r'<a[^>]*href="([^"]*)"[^>]*>\s*([^<]*?)\s*</a>', re.S)

STAGE_WORDS = {"Early", "Growth"}
EXIT_WORDS = {"IPO", "Acquired"}


def parse_rows(html_text):
    """Parse the 'all' company table's <li> rows -> {data_id: row_dict}."""
    all_start = html_text.find('data-table-id="all"')
    if all_start == -1:
        raise SystemExit("FATAL: could not find the 'all' company table on the page")
    all_html = html_text[all_start:]
    rows = {}
    for blob, data_id in ROW_RE.findall(all_html):
        row = json.loads(html_module.unescape(blob))
        rows[data_id] = row
    return rows


def extract_website(modal_html):
    """The 'Website' external link sits in the single <div ... mt-40"> block
    right after the description and before the details <ul> -- scoped so we
    don't also pick up KP-partner /people/ links from the Partners field."""
    m = re.search(r'<div class="flex items-center gap-x-24 mt-40">(.*?)</div>', modal_html, re.S)
    if not m:
        return None
    hrefs = re.findall(r'href="([^"]*)"', m.group(1))
    return hrefs[0] if hrefs else None


def extract_li_fields(modal_html):
    fields = {}
    for li_m in LI_FIELD_RE.finditer(modal_html):
        li = li_m.group(1)
        h3_m = H3_RE.search(li)
        if not h3_m:
            continue
        label = clean(h3_m.group(1))
        spans = [clean(s) for s in SPAN_RE.findall(li)]
        links = [(clean(text), href) for href, text in LINK_RE.findall(li)]
        fields[label] = {"spans": [s for s in spans if s is not None], "links": links}
    return fields


def parse_modal(data_id, modal_html):
    # Trim off the "Related" perspectives carousel before parsing fields --
    # it isn't part of the company's own data and its images aren't logos.
    rel_idx = modal_html.find('<h2 class="md:text-current/75 max-md:mb-12">Related</h2>')
    if rel_idx != -1:
        modal_html = modal_html[:rel_idx]

    name_m = re.search(r'<h2 class="text-20 md:text-36 max-w-490 text-pretty">\s*(.*?)\s*</h2>', modal_html, re.S)
    name = clean(name_m.group(1)) if name_m else None

    desc_m = re.search(
        r'<div class="text-12 sm:text-14 md:text-17 text-current/60 text-pretty">\s*(.*?)\s*</div>',
        modal_html, re.S,
    )
    description = clean(desc_m.group(1)) if desc_m else None

    company_url = extract_website(modal_html)

    fields = extract_li_fields(modal_html)

    partnered_since = fields.get("Partnered Since", {}).get("spans", [])
    year_partnered = to_year(partnered_since[0]) if partnered_since else None
    first_invested_stage = partnered_since[1] if len(partnered_since) > 1 else None

    stage_spans = fields.get("Stage", {}).get("spans", [])
    current_stage = next((s for s in stage_spans if s in STAGE_WORDS), None)
    exit_types = [s for s in stage_spans if s in EXIT_WORDS]
    trailing = [s for s in stage_spans if s not in STAGE_WORDS and s not in EXIT_WORDS]
    ticker_symbol = None
    acquirer = None
    if "IPO" in exit_types and trailing:
        # a bare ticker (IPO-only) is the sole trailing value; when both IPO
        # and Acquired are present, the trailing value is the acquirer name.
        if "Acquired" in exit_types:
            acquirer = trailing[0]
        else:
            ticker_symbol = trailing[0]
    elif "Acquired" in exit_types and trailing:
        acquirer = trailing[0]

    if "Acquired" in exit_types and "IPO" in exit_types:
        status = "Acquired (post-IPO)"
    elif "Acquired" in exit_types:
        status = "Acquired"
    elif "IPO" in exit_types:
        status = "Public"
    elif current_stage:
        status = "Active"
    else:
        status = None

    founders = fields.get("Founders", {}).get("spans", [])
    partners = [text for text, href in fields.get("Partners", {}).get("links", []) if text]

    return {
        "company_name": name,
        "description": description,
        "company_url": company_url,
        "sectors": [],  # filled in from the row data by the caller
        "year_partnered": year_partnered,
        "first_invested_stage": first_invested_stage,
        "status": status,
        "acquirer": acquirer,
        "ticker_symbol": ticker_symbol,
        "founders": founders,
        "kp_partners": partners,
    }


def everywhere_tags(name, description, sectors):
    """KP sectors first (mapped via SECTOR_TAG_MAP), then keyword fallback on
    name + description to add/refine. Order most->least relevant, cap at 4."""
    tags = []
    for sec in sectors:
        mapped = SECTOR_TAG_MAP.get(sec)
        if mapped and mapped not in tags:
            tags.append(mapped)
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
        limit = int(sys.argv[sys.argv.index("--limit") + 1])

    print(f"Fetching {BASE_URL} ...")
    html_text = get(BASE_URL)
    print(f"  {len(html_text):,} bytes")

    rows = parse_rows(html_text)
    print(f"Found {len(rows)} rows in the 'all' table")

    modals_start = html_text.find('data-component="companyModals"')
    modal_block = html_text[modals_start:]
    parts = MODAL_SPLIT_RE.split(modal_block)
    modal_pairs = list(zip(parts[1::2], parts[2::2]))
    modals = {data_id: content for data_id, content in modal_pairs}
    print(f"Found {len(modals)} company detail modals")

    scraped_at = datetime.now(timezone.utc).isoformat()
    companies = []
    data_ids = list(rows.keys())
    if limit:
        data_ids = data_ids[:limit]

    for data_id in data_ids:
        row = rows[data_id]
        modal_html = modals.get(data_id)
        if modal_html is None:
            print(f"  ! no modal for data-id={data_id} ('{row.get('name')}') -- skipping", file=sys.stderr)
            continue
        rec = parse_modal(data_id, modal_html)
        if not rec["company_name"]:
            rec["company_name"] = row.get("name")
        sectors = [s.strip() for s in (row.get("sector") or "").split(",") if s.strip()]
        rec["sectors"] = sectors
        rec["everywhere_tags"] = everywhere_tags(rec["company_name"], rec["description"], sectors)
        rec["source_url"] = BASE_URL
        rec["scraped_at"] = scraped_at
        companies.append(rec)
        time.sleep(0.02)

    companies.sort(key=lambda o: (o["company_name"] or "").lower())

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(companies, f, ensure_ascii=False, indent=2)

    by_status = Counter(o["status"] for o in companies)
    by_sector = Counter(s for o in companies for s in o["sectors"])
    by_tag = Counter(t for o in companies for t in o["everywhere_tags"])

    print(f"\nWrote {len(companies)} companies -> {OUT}")
    print("By status:", dict(by_status),
          "| with acquirer:", sum(1 for o in companies if o["acquirer"]),
          "| with ticker:", sum(1 for o in companies if o["ticker_symbol"]))
    print("With website:", sum(1 for o in companies if o["company_url"]),
          "| with description:", sum(1 for o in companies if o["description"]),
          "| with year_partnered:", sum(1 for o in companies if o["year_partnered"]),
          "| with founders:", sum(1 for o in companies if o["founders"]),
          "| with kp_partners:", sum(1 for o in companies if o["kp_partners"]),
          "| untagged:", sum(1 for o in companies if not o["everywhere_tags"]))
    print("By sector:")
    for t, c in by_sector.most_common():
        print(f"  {c:>4}  {t}")
    print("By everywhere_tag:")
    for t, c in by_tag.most_common():
        print(f"  {c:>4}  {t}")


if __name__ == "__main__":
    main()
