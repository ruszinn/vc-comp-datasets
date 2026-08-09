#!/usr/bin/env python3
"""
Andreessen Horowitz (a16z) portfolio scraper -> a16z_companies.json

Site: https://a16z.com/portfolio/ -- WordPress + Vite/Alpine.js theme (custom
"wr25" component set, not Webflow/Finsweet). The portfolio grid looks like a
client-side widget (`x-data="wr25Portfolio()"`), but the full company dataset
is actually **server-rendered into the DOM** as an HTML-escaped JSON array in
a `data-companies="[...]"` attribute on that element -- no API call needed,
no pagination, everything in one `GET /portfolio/`. All 849 records share an
identical 40-key schema (verified), so this is the richest, most uniform
source scraped in this repo so far: it already publishes structured
`status`/`ticker_symbol`/`acquirer`/`stages`/`founders_list`/`focus_areas`,
so (per PLAYBOOK "Empty != absent") there's no need to mine names/descriptions
for exit info -- a16z's own `title` field already spells out
"Acquired By: <X>" / "IPO: <TICK>" / "DPO: <TICK>" / "SPAC: <TICK>".

Fields taken as-is from each object in data-companies:
  - id, name (post_title), permalink (a16z profile page), logo
  - external_url/company_url (site's own site) -- both blank for the handful
    of companies with no live site (then `url` just falls back to the a16z
    permalink itself, so `url` is NOT used as company_url in that case)
  - website_description -> description
  - status (Active / Exits / "Exits;Active" if reprised after an M&A exit) --
    kept as `status_raw` (raw semicolon string) plus a derived `status` enum
  - stages (list, e.g. ["Venture"], ["M&A"], ["IPO"]) -> investment_stages
  - ticker_symbol, acquirer, exit_date -- published directly (structured)
  - founders_list -- a single "A, B and C" string -> split into a list
  - focus_areas -- a16z's own vertical tags (Enterprise, Crypto, Consumer,
    Bio + Health, Infra, Fintech, American Dynamism, Games, Seed, Growth,
    CLF=Cultural Leadership Fund, TxO=Talent x Opportunity). Seed/Growth/CLF/
    TxO are a16z FUND/PROGRAM labels, not sectors -- excluded from `sectors`
    but harmless if present in focus_areas so we filter them for `sectors`.
  - socials -> list of {platform, url} (X/LinkedIn/Facebook/GitHub/Instagram)
  - year_founded -- present for 91/849 (a16z simply doesn't track it for most)
  - initial_a16z_date_funded -- the reliable "first investment" datetime
    (848/849 populated); NOTE the *separate* `investment_date`/
    `investment_date_raw` fields are broken upstream (32/51 non-empty values
    are the literal string "1970", a Unix-epoch bug) so they are dropped
    entirely rather than shipped as bad data.
  - jobs -- open roles count a16z shows on the card (0 for most; not an
    enrichment number, it's what the site displays today)

Dropped as always-empty / redundant / not useful, confirmed on the full 849:
  - email (100% blank), overview (100% blank), articles (100% empty list),
    logo_width (100% null), display_name (always == name or blank),
    number_of_jobs (string duplicate of `jobs`), investment_date /
    investment_date_raw (epoch-bug, see above), title/tag/tags/filter_by/
    verticals/search_haystack/announcement/_sort_order/website_supercategory
    (internal site plumbing -- their useful content, e.g. acquirer/exit/
    status, is already exposed via the cleaner dedicated fields above).

requirements:
    pip install requests beautifulsoup4

usage:
    python3 a16z_scraper.py            # writes ../data/a16z_companies.json
    python3 a16z_scraper.py --limit 10 # only the first ~10 for a test run
"""

import html
import json
import os
import re
import sys
import time
from datetime import datetime, timezone

import requests

URL = "https://a16z.com/portfolio/"
SOURCE_URL = "https://a16z.com/portfolio/"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "data", "a16z_companies.json")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}
TIMEOUT = 45
RETRIES = 3

# a16z's own focus_areas that are fund/program labels, not sectors -- exclude
# from the `sectors` field (still visible if present in raw focus_areas, but
# we don't ship them as a "sector").
NON_SECTOR_FOCUS_AREAS = {"Seed", "Growth", "CLF", "TxO"}

# a16z's own verticals -> the 17-tag everywhere_tags taxonomy. "American
# Dynamism" (defense/aerospace/industrial policy) and "Games" map directly;
# "Crypto" and "Bio + Health" map directly. "Enterprise" and "Infra" have no
# single clean tag (Enterprise spans dev-tools/work/data/security; Infra
# mostly means dev-tools/cloud) -- left to the keyword classifier.
SECTOR_TAG_MAP = {
    "Bio + Health": ["Health", "BioTech"],
    "Crypto": ["Web3 / Crypto"],
    "Fintech": ["FinTech / Insurance"],
    "American Dynamism": ["Deeptech / Robotics / AR/VR"],
    "Games": ["Gaming / Media / Entertainment"],
    "Consumer": ["Consumer"],
    "Infra": ["Dev Tools / Cloud"],
}

# everywhere_tags keyword classifier (substrings, lowercased) -- copied from
# menlo_scraper.py / rre_scraper.py / iconiq_scraper.py.
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
    if not isinstance(s, str):
        s = str(s)
    s = re.sub(r"\s+", " ", s).strip()
    return s or None


def extract_companies_json(page_html):
    """The portfolio grid's Alpine component reads its data from a
    data-companies="[...]" attribute holding HTML-escaped JSON. Extract by
    finding the attribute, then the matching closing quote (the escaped JSON
    itself never contains a literal unescaped double-quote)."""
    marker = 'data-companies="'
    i = page_html.find(marker)
    if i == -1:
        raise SystemExit("FATAL: could not find data-companies attribute -- site markup may have changed")
    start = i + len(marker)
    end = page_html.find('"', start)
    if end == -1:
        raise SystemExit("FATAL: could not find closing quote for data-companies attribute")
    raw = html.unescape(page_html[start:end])
    return json.loads(raw)


def split_founders(s):
    if not s:
        return []
    # "A, B and C" / "A, B, and C" / "A and B" / "A, B"
    s = s.replace(", and ", ", ").replace(" and ", ", ")
    return [clean(p) for p in s.split(",") if clean(p)]


def derive_status(title, status_raw, ticker_symbol, acquirer, stages):
    """a16z's own `title` field already spells out the exit type
    ("Acquired By: X", "IPO: TICK", "DPO: TICK", "SPAC: TICK", "M&A: TICK");
    derive a clean status enum from it, falling back to `stages` (present
    even for the ~19 exits where `title` is blank, e.g. Character.AI/EXIT,
    AltSchool/M&A) and finally a bare ticker (a hard signal of being public
    even on the handful where a16z's own status/title fields are stale, e.g.
    Zulily, Yubico). No name/description mining needed -- this data is
    already structured (Empty != absent checked: title/stages/ticker_symbol/
    acquirer/exit_date are all populated directly by the source)."""
    t = (title or "").strip()
    if t.startswith("IPO:"):
        return "Public (IPO)"
    if t.startswith("DPO:"):
        return "Public (DPO)"
    if t.startswith("SPAC:"):
        return "Public (SPAC)"
    if t.startswith("Acquired By:") or t.startswith("M&A:") or acquirer:
        return "Acquired"
    if ticker_symbol:
        return "Public"
    if "IPO" in stages:
        return "Public (IPO)"
    if "DPO" in stages:
        return "Public (DPO)"
    if "SPAC" in stages:
        return "Public (SPAC)"
    if "M&A" in stages:
        return "Acquired"
    if "EXIT" in stages or (status_raw and "Exits" in status_raw):
        return "Exited"
    if status_raw and "Active" in status_raw:
        return "Active"
    return None


def everywhere_tags(name, description, sectors):
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


def parse_record(d, scraped_at):
    name = clean(d.get("name") or d.get("post_title"))
    if not name:
        return None

    company_url = clean(d.get("external_url")) or clean(d.get("company_url"))
    permalink = clean(d.get("permalink"))
    logo_url = clean(d.get("logo"))
    description = clean(d.get("website_description"))

    stages = d.get("stages")
    if isinstance(stages, str):
        investment_stages = [stages] if clean(stages) else []
    else:
        investment_stages = [clean(x) for x in (stages or []) if clean(x)]

    focus_areas = [clean(x) for x in (d.get("focus_areas") or []) if clean(x)]
    sectors = [f for f in focus_areas if f not in NON_SECTOR_FOCUS_AREAS]

    founders = split_founders(clean(d.get("founders_list")))

    socials = []
    for s in (d.get("socials") or []):
        platform = clean((s.get("constants.select.social_icons") or {}).get("label"))
        surl = clean(s.get("url"))
        if surl:
            socials.append({"platform": platform, "url": surl})

    ticker_symbol = clean(d.get("ticker_symbol"))
    acquirer = clean(d.get("acquirer"))
    exit_date = clean(d.get("exit_date"))
    title = clean(d.get("title"))
    status_raw = clean(d.get("status"))
    status = derive_status(title, status_raw, ticker_symbol, acquirer, investment_stages)

    year_founded = clean(d.get("year_founded"))
    initial_investment_date = clean(d.get("initial_a16z_date_funded"))
    if initial_investment_date and initial_investment_date.endswith(" 00:00:00"):
        initial_investment_date = initial_investment_date[: -len(" 00:00:00")]

    open_jobs = d.get("jobs")
    if not isinstance(open_jobs, int):
        open_jobs = None

    return {
        "company_name": name,
        "description": description,
        "company_url": company_url,
        "a16z_profile_url": permalink,
        "logo_url": logo_url,
        "sectors": sectors,
        "investment_stages": investment_stages,
        "founders": founders,
        "socials": socials,
        "year_founded": year_founded,
        "initial_investment_date": initial_investment_date,
        "status": status,
        "acquirer": acquirer,
        "ticker_symbol": ticker_symbol,
        "exit_year": exit_date,
        "open_jobs": open_jobs,
        "everywhere_tags": everywhere_tags(name, description, sectors),
        "source_url": SOURCE_URL,
        "scraped_at": scraped_at,
    }


def main():
    limit = int(sys.argv[sys.argv.index("--limit") + 1]) if "--limit" in sys.argv else None

    page_html = get(URL)
    companies = extract_companies_json(page_html)
    scraped_at = datetime.now(timezone.utc).isoformat()

    out, seen = [], set()
    for d in companies:
        rec = parse_record(d, scraped_at)
        if not rec or rec["company_name"] in seen:
            continue
        seen.add(rec["company_name"])
        out.append(rec)
        if limit and len(out) >= limit:
            break

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2)

    # ---- summary ----
    n = len(out)
    print(f"wrote {n} companies -> {OUT}")
    for field in ("description", "company_url", "logo_url", "year_founded",
                  "initial_investment_date", "status", "acquirer", "ticker_symbol", "exit_year"):
        miss = sum(1 for r in out if not r[field])
        print(f"  {field:24s} missing: {miss}/{n}")
    print(f"  sectors empty:            {sum(1 for r in out if not r['sectors'])}/{n}")
    print(f"  founders empty:           {sum(1 for r in out if not r['founders'])}/{n}")
    print(f"  socials empty:            {sum(1 for r in out if not r['socials'])}/{n}")
    from collections import Counter
    print("  status breakdown:", dict(Counter(r["status"] for r in out)))
    untagged = [r["company_name"] for r in out if not r["everywhere_tags"]]
    print(f"  untagged:                 {len(untagged)}/{n}" + (f" -> {untagged[:20]}" if untagged else ""))
    by_tag = {}
    for r in out:
        for t in r["everywhere_tags"]:
            by_tag[t] = by_tag.get(t, 0) + 1
    print("  by everywhere_tag:")
    for t, k in sorted(by_tag.items(), key=lambda x: -x[1]):
        print(f"    {k:3d}  {t}")


if __name__ == "__main__":
    main()
