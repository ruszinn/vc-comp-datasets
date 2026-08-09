#!/usr/bin/env python3
"""
Bain Capital Ventures portfolio scraper -> baincapital_companies.json

Scrapes Bain Capital Ventures' portfolio (https://www.baincapitalventures.com/portfolio)
into a JSON file. The site is Next.js + a headless **Sanity.io** CMS (project id
`0ystv7v4`, dataset `production`). Only ~9 "featured" companies are server-rendered
in the static HTML (a `companies_grid` teaser) -- the full portfolio is *not* paginated
through any REST/GraphQL route on baincapitalventures.com itself, but Sanity's public
CDN query API is reachable directly and unauthenticated:

    GET https://0ystv7v4.apicdn.sanity.io/v2021-10-21/data/query/production
        ?query=<GROQ>

This returns every `_type=="portfolio"` document (269 as of this run) in one call,
with references to `domains` (BCV's 7 investment verticals), `stage` (BCV's 5-value
investment-stage/status taxonomy: Seed/Early/Growth/IPO/Acquired), and `partner`
(actually references the `team` document type -- BCV's own investment team members
associated with the deal) resolved inline via a single GROQ projection.

Schema notes:
  - `description`: from `about` (falls back to `short_description`, which is present
    for ~64 records and is usually a duplicate/near-duplicate of `about`).
  - `company_url`: `company_website` (38/269 have none -- mostly older
    acquired/defunct companies whose site no longer has its own domain).
  - `logo_url`: BCV stores only a Sanity asset ref (`image-<hash>-<W>x<H>-<ext>`),
    not a URL -- reconstructed as the public `cdn.sanity.io/images/<project>/<dataset>/...`
    URL (verified reachable; this is a different CDN host than Webflow's, so the
    known cdn.webflow.com routing issue does not apply here).
  - `sectors`: BCV's own `domains` vertical tags (AI Apps, AI Infrastructure, Commerce,
    Fintech, Healthcare, Industrials, Security) -- source of truth, read verbatim.
  - `stage`: BCV's own multi-value stage/status tags, kept verbatim as published
    (e.g. a company can be tagged both "Seed" and "Acquired"). **Empty != absent**
    applies here in reverse-safe fashion: rather than treating "Acquired" as a
    normal funding stage, `status`/`acquirer`/`exit_year` are *derived* from it (see
    below), and the raw `stage` list is kept as-is for transparency.
  - `status`/`acquirer`/`exit_year`: BCV has no separate structured exit fields.
    "Acquired" is one of the 5 `stage` values (91/269 records); of those, 68 also
    carry the acquirer (and sometimes year) **denormalized into the company name
    suffix** -- `Foo (Acquirer)` or `Foo (acquired by Acquirer in YYYY)` or
    `Foo (Acquirer since YYYY)`. `derive_exit()` parses this per the "Empty != absent"
    rule; company_name is kept verbatim (suffix intact) as the RRE/Founders-Fund
    precedent does. 23 Acquired-stage companies have no suffix and no acquirer
    named in the description either -- acquirer/exit_year stay null for those.
    "IPO" stage (11 records) has no ticker anywhere (checked names + descriptions;
    only substring false positives like "public cloud") -- ticker_symbol is not
    published by BCV, so it is intentionally omitted from the schema.
  - CAVEAT: a handful of `stage` tags look stale/inconsistent with the companies'
    actual known status as of this scrape (e.g. Cleanlab, Veza, Wrike, MaintainX all
    carry "Acquired" alongside an early/growth stage, despite being widely known as
    still-independent, actively-operating companies). This is BCV's own published
    tag, taken verbatim per "never fabricate" -- not corrected against outside
    knowledge.
  - `founded_year`: BCV's own `founded` field, populated for only 28/269 -- kept as
    published (sparse by source, not a mining failure: checked name/description
    prose for "founded in"/"est." wording and found none).
  - `year_of_investment`: BCV's first-investment year, populated for 267/269.
  - `partners`: BCV team member(s) tied to the deal, resolved from the `partner`
    reference (which points at the `team` document type, not the separate `partner`
    document type -- verified by dereferencing) to `{name, slug}` with a
    `baincapitalventures.com/team/<slug>` profile URL BCV itself publishes.
  - No founders, no location/HQ published anywhere in the `portfolio` schema.

requirements:
    pip install requests

usage:
    python3 baincapital_scraper.py            # writes ../data/baincapital_companies.json
    python3 baincapital_scraper.py --limit 10 # only the first ~10 for a test run
"""

import json
import os
import re
import sys
import time
from datetime import datetime, timezone

import requests

SANITY_PROJECT = "0ystv7v4"
SANITY_DATASET = "production"
API = f"https://{SANITY_PROJECT}.apicdn.sanity.io/v2021-10-21/data/query/{SANITY_DATASET}"
SOURCE_URL = "https://www.baincapitalventures.com/portfolio"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "data", "baincapital_companies.json")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}
TIMEOUT = 45
RETRIES = 3

GROQ_QUERY = """
*[_type=="portfolio"]{
  title,
  "slug": slug.current,
  about,
  short_description,
  company_website,
  founded,
  year_of_investment,
  "logo_ref": logo.asset->_id,
  "domains": domains[]->{title, "slug": slug.current},
  "stage": stage[]->{name, "slug": slug.current},
  "partner": partner[]->{firstname, last_name, "slug": slug.current}
}
"""

# BCV's 7 investment-vertical `domains` -> the 17-tag everywhere_tags taxonomy.
# "AI Apps" and "AI Infrastructure" are intentionally NOT mapped: AI alone is not
# a category (classify by the market served) -- left to the keyword fallback.
SECTOR_TAG_MAP = {
    "Healthcare": ["Health"],
    "Fintech": ["FinTech / Insurance"],
    "Security": ["Cybersecurity"],
    "Industrials": ["Deeptech / Robotics / AR/VR"],
    "Commerce": ["Consumer"],
}

# everywhere_tags keyword classifier (substrings, lowercased) -- copied from
# menlo_scraper.py / rre_scraper.py / iconiq_scraper.py.
KEYWORD_TAGS = [
    ("BioTech", ["biotech", "drug", "therapeut", "oncolog", "cancer", "tumor", "genomic", "genome",
                 "molecul", "antibod", "protein", "vaccine", "clinical-stage", "medicine", "opioid", "life science",
                 "synthetic biology", "biolog"]),
    ("Health", ["healthcare", "health care", "patient", "clinic", "medical", "mental health", "telehealth",
                "health system", "health record", "diagnos", "surgical", "doctor", "hospital", "pharmac", "therapy",
                "biomarker", "cardiovascular", "seniors", "loneliness", "companion"]),
    ("Cybersecurity", ["cybersecurity", "security", "secure", "privacy", "fraud", "phishing", "malware",
                       "ransomware", "endpoint", "zero trust", "vulnerab", "authentication", "threat", "defense system",
                       "identity", "information protection", "id verification", "trust™", "trustworthy relationships",
                       "verify, screen"]),
    ("FinTech / Insurance", ["fintech", "payment", "bank", "lending", "loan", "insurance", "credit", "trading",
                             "wallet", "financ", "invoic", "accounting", "payroll", "treasury", "billing", "pricing platform",
                             "rebate", " tax", "audit", "money management", "robo-advisor", "brokerage", "spend management",
                             "capital markets", "investing", "claims"]),
    ("Web3 / Crypto", ["crypto", "blockchain", "web3", "token", "on-chain", "ethereum", "bitcoin", "decentral", "stablecoin", "nft"]),
    ("Gaming / Media / Entertainment", ["game", "gaming", "music", "video", "creator", "content", "publish",
                                        "entertain", "newsletter", "podcast", "film", "streaming", "social media", "media platform",
                                        "radio station", "creative file"]),
    ("Dev Tools / Cloud", ["developer", " api ", "apis", "api platform", "infrastructure", "database", "cloud",
                           "open source", "devops", "sdk", "kubernetes", "container", "observability", "deploy", "compute",
                           "storage", "serverless", "inference", "networking", "ethernet", "coding", "codebase", "low-code",
                           "no-code", "source code", "development platform", "incident", " sre", "voicemail", "communications",
                           "llm", "foundation model", "interpretability", "voice keyboard", "voice recognition", "frame relay",
                           "log management", "file sharing", "tech stack", "voice agent", "appliance software",
                           "text to speech", "models", "digital workplace", "self-service platform",
                           "dataframe", "rust and python", "network performance monitoring", "network ai platform",
                           "network monitoring", "edge intelligence", "iot application", "build ml", "api",
                           "file management", "map", "integration development", "physical science"]),
    ("Data & Analytics", ["analytics", "business intelligence", "data platform", "data warehouse", "data lake",
                          "data pipeline", "insights", "dashboard", "experimentation", "decision intelligence", "data quality",
                          "analyz", "data curation", "quality management", "relationship intelligence",
                          "data discovery", "data analysis", "edge-data", "complex data", "real-world data", "analyst",
                          "data intelligence", "reliable models"]),
    ("Future of Work", ["workforce", "hiring", "recruit", "employee", "productivity", "collaborat", "talent",
                        "workplace", "human resources", " hr ", "learning platform", "customer success", "customer service",
                        "customer support", "presales", " sales ", "onboarding", "workflow", "saas management", "ai assistant",
                        "project management", "partnerships platform", "partnership", "teamwork", "scheduling", "work assistant",
                        "maintenance", "frontline teams", "pipeline to revenue", "customer engagement", "compensation plan",
                        "call center coaching", "calendars", "demo platform", "customer data", "revenue opportunity",
                        "feedback, design, and engineering", "ship better products"]),
    ("Transportation / Mobility", ["mobility", "vehicle", "transport", "autonomous", "fleet", "driving", "aviation",
                                   "aircraft", "electric vehicle", "scooter", " bike", "boat", "watercraft", "rideshar", "travel"]),
    ("Logistics / Supply Chain", ["logistics", "supply chain", "supply and demand", "freight", "warehouse",
                                  "delivery", "procurement", "inventory", "fulfillment", "shipping"]),
    ("PropTech", ["real estate", "property", "housing", "mortgage", "rental", "construction", "tenant", "smart building"]),
    ("CPG", ["beverage", "snack", "consumer packaged", "beauty", "cosmetic", "apparel", "grocery", "skincare",
             "eyewear", "glasses", "footwear", "pet sitter", "pet "]),
    ("Climate / Sustainability", ["climate", "carbon", "renewable", "solar", "battery", "sustainab", "emission",
                                  "clean energy", "ev charging", "electrif", "energy", "power is produced", "power grid"]),
    ("RegTech/Gov/Legal", ["legal", "compliance", "government", "regulat", "law firm", "attorney", "risk services",
                           "lawsuit", "lawyer", "third-party risk", "vendor", "data protection as a service", "agree"]),
    ("Deeptech / Robotics / AR/VR", ["robot", "hardware", "semiconductor", "chip", "drone", "aerospace",
                                     "augmented reality", "virtual reality", "satellite", "quantum", "sensor", "rfid", "wifi",
                                     "space", "rocket", "launch vehicle", "optics", "defense", "gpu", "data center"]),
    ("Consumer", ["marketplace", "consumer", "shopping", "social network", "community", "app for", "app that",
                  "ecommerce", "e-commerce", "subscription", "retailer", "universit", "student", "education",
                  "learning", "fashion", "dealers", "wholesale vehicle", "relationship backup", "personal ai",
                  "life goals", "professionals to their networks"]),
]


def get_json(url, params=None):
    last = None
    for attempt in range(1, RETRIES + 1):
        try:
            r = requests.get(url, headers=HEADERS, params=params, timeout=TIMEOUT)
            r.raise_for_status()
            return r.json()
        except requests.RequestException as e:  # noqa
            last = e
            wait = 1.5 * attempt
            print(f"  ! request failed ({e}); retry {attempt}/{RETRIES} in {wait:.1f}s", file=sys.stderr)
            time.sleep(wait)
    raise SystemExit(f"FATAL: could not fetch {url}: {last}")


def clean(s):
    if s is None:
        return None
    s = re.sub(r"\s+", " ", s.replace("\xa0", " ")).strip()
    return s or None


def logo_url_from_ref(ref):
    """Sanity image asset refs look like 'image-<hash>-<W>x<H>-<ext>'; the public
    CDN URL is https://cdn.sanity.io/images/<project>/<dataset>/<hash>-<W>x<H>.<ext>
    (verified reachable -- this is cdn.sanity.io, not the unreachable cdn.webflow.com)."""
    if not ref or not ref.startswith("image-"):
        return None
    body = ref[len("image-"):]
    m = re.match(r"^(.*)-(\w+)$", body)
    if not m:
        return None
    stem, ext = m.groups()
    return f"https://cdn.sanity.io/images/{SANITY_PROJECT}/{SANITY_DATASET}/{stem}.{ext}"


# Parses the acquirer (and optional year) denormalized into the company-name
# suffix for "Acquired"-stage companies, e.g.:
#   "Trooly (Airbnb)"                          -> acquirer=Airbnb,   year=None
#   "SendGrid (acquired by Twilio in 2018)"    -> acquirer=Twilio,   year=2018
#   "Kiva Systems (Amazon Robotics since 2012)"-> acquirer=Amazon Robotics, year=2012
NAME_SUFFIX_RE = re.compile(r"^(.*?)\s*\(([^()]+)\)\s*$")
ACQUIRED_BY_RE = re.compile(r"^acquir\w*\s+by\s+(.+?)\s+in\s+(\d{4})$", re.I)
SINCE_YEAR_RE = re.compile(r"^(.*?)\s+since\s+(\d{4})$", re.I)


def derive_exit(name, stage_names):
    """Returns (status, acquirer, exit_year). status is one of Active/Acquired/Public
    derived from BCV's own `stage` tags (kept verbatim, not re-guessed)."""
    if "Acquired" in stage_names:
        status = "Acquired"
    elif "IPO" in stage_names:
        status = "Public"
    else:
        status = "Active"

    acquirer, exit_year = None, None
    if status == "Acquired":
        m = NAME_SUFFIX_RE.match(name)
        if m:
            suffix = m.group(2).strip()
            m2 = ACQUIRED_BY_RE.match(suffix)
            if m2:
                acquirer, exit_year = m2.group(1).strip(), m2.group(2)
            else:
                m3 = SINCE_YEAR_RE.match(suffix)
                if m3:
                    acquirer, exit_year = m3.group(1).strip(), m3.group(2)
                else:
                    acquirer = suffix
    return status, acquirer, exit_year


def everywhere_tags(name, description, sectors):
    """BCV domains first (mapped via SECTOR_TAG_MAP), then keyword fallback on
    name + description to add/refine. Order most->least relevant, cap at 4."""
    tags = []
    for sec in sectors:
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


def fetch_all():
    data = get_json(API, params={"query": GROQ_QUERY})
    if "error" in data:
        raise SystemExit(f"FATAL: Sanity query error: {data['error']}")
    return data.get("result") or []


def main():
    limit = int(sys.argv[sys.argv.index("--limit") + 1]) if "--limit" in sys.argv else None
    raw = fetch_all()
    if limit:
        raw = raw[:limit]

    scraped_at = datetime.now(timezone.utc).isoformat()
    out = []
    for c in raw:
        name = clean(c.get("title"))
        if not name:
            continue
        description = clean(c.get("about")) or clean(c.get("short_description"))
        sectors = [d["title"] for d in (c.get("domains") or []) if d and d.get("title")]
        stage_names = [s["name"] for s in (c.get("stage") or []) if s and s.get("name")]
        status, acquirer, exit_year = derive_exit(name, stage_names)
        partners = []
        for p in c.get("partner") or []:
            fn = clean(p.get("firstname"))
            ln = clean(p.get("last_name"))
            full = " ".join(x for x in (fn, ln) if x)
            slug = p.get("slug")
            if not full:
                continue
            entry = {
                "name": full,
                "profile_url": f"https://www.baincapitalventures.com/team/{slug}" if slug else None,
            }
            if entry not in partners:
                partners.append(entry)

        out.append({
            "company_name": name,
            "description": description,
            "company_url": clean(c.get("company_website")),
            "logo_url": logo_url_from_ref(c.get("logo_ref")),
            "sectors": sectors,
            "stage": stage_names,
            "status": status,
            "acquirer": acquirer,
            "exit_year": exit_year,
            "founded_year": clean(c.get("founded")),
            "year_of_first_investment": clean(c.get("year_of_investment")),
            "partners": partners,
            "everywhere_tags": everywhere_tags(name, description, sectors),
            "source_url": SOURCE_URL,
            "scraped_at": scraped_at,
        })

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2)

    # ---- summary ----
    n = len(out)
    print(f"wrote {n} companies -> {OUT}")
    for field in ("description", "company_url", "logo_url", "founded_year", "year_of_first_investment"):
        miss = sum(1 for r in out if not r[field])
        print(f"  {field:25s} missing: {miss}/{n}")
    print(f"  sectors empty:            {sum(1 for r in out if not r['sectors'])}/{n}")
    print(f"  stage empty:              {sum(1 for r in out if not r['stage'])}/{n}")
    print(f"  partners empty:           {sum(1 for r in out if not r['partners'])}/{n}")
    by_status = {}
    for r in out:
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1
    print(f"  by status: {by_status}")
    acquired = [r for r in out if r["status"] == "Acquired"]
    print(f"  acquired w/ acquirer: {sum(1 for r in acquired if r['acquirer'])}/{len(acquired)}")
    print(f"  acquired w/ exit_year: {sum(1 for r in acquired if r['exit_year'])}/{len(acquired)}")
    untagged = [r["company_name"] for r in out if not r["everywhere_tags"]]
    print(f"  untagged:      {len(untagged)}/{n}" + (f" -> {untagged}" if untagged else ""))
    by_tag = {}
    for r in out:
        for t in r["everywhere_tags"]:
            by_tag[t] = by_tag.get(t, 0) + 1
    print("  by everywhere_tag:")
    for t, k in sorted(by_tag.items(), key=lambda x: -x[1]):
        print(f"    {k:3d}  {t}")


if __name__ == "__main__":
    main()
