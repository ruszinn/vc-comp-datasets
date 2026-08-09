#!/usr/bin/env python3
"""
TCV (Technology Crossover Ventures) portfolio scraper -> tcv_companies.json

Scrapes TCV's curated portfolio showcase (https://www.tcv.com/partnerships) into
a JSON file. The site is a Next.js (App Router) build backed by Contentful; the
company grid is NOT baked into static HTML tags but IS present in the page's
React Server Component "flight" payload -- a series of
`self.__next_f.push([1,"..."])` script tags whose string bodies contain escaped
JSON. One such chunk on /partnerships holds `{"companies":[...]}` with all 151
showcase companies (93 Active / 58 Prior by TCV's own `status` field) in one
shot -- no pagination, no "load more". Each company also has its own detail
page `https://www.tcv.com/partnerships/<slug>` whose flight payload carries a
much richer `{"company": {...}}` object: `websiteUrl`, `yearFounded`,
`linkedInUrl`, `twitterUrl`, `jobsUrl`, `tagline`, `about` (long description),
`tcvPerspective` (TCV's own blurb, sometimes populated), and `investmentTeam`
(the TCV partners/principals on the deal, with a `/team/<slug>` profile).

Note: TCV also publishes a much bigger (800+) flat list of full LEGAL entity
names at /all-companies (a compliance/legal-disclosure page: one giant
`<p>` blob, no logos/links/sectors/descriptions -- not a showcase). This
scraper intentionally uses the richer /partnerships showcase instead, per
CLAUDE.md's "site-tailored schema" principle: /all-companies has essentially
no structured fields to extract beyond a name string, while /partnerships
carries a real per-company schema.

Empty != absent: TCV's structured `status` field is only ever "Active" or
"Prior" -- no acquirer/ticker/exit-year field exists anywhere in the API
payload. But the free-text `about` paragraph frequently states it in prose:
"X was acquired by Y in YYYY." or "X went public in YYYY on the <Exchange>
(<EXCH>: <TICK>)." (occasionally "X is listed on the <Exchange> (<EXCH>:
<TICK>)" with no "went public" verb, e.g. Xero). `derive_exit()` regexes these
out into `exit_type` / `acquirer` / `ticker_symbol` / `exit_year`. One phrasing
trap: "TCV's ownership in X was acquired by Y in YYYY" describes TCV selling
its OWN stake (a secondary sale), not an acquisition of the company -- handled
as a distinct `exit_type` so it isn't confused with a company-level M&A exit
(see Prodege). Also: when an acquirer's own ticker is parenthesized in the
sentence (e.g. "Venafi was acquired by CyberArk (NASDAQ: CYBR) in 2024"), that
ticker belongs to the ACQUIRER, not the portfolio company, so it is deliberately
NOT captured as the company's `ticker_symbol`. Most "Prior" companies disclose
no exit detail in prose at all (e.g. GoDaddy, Groupon, Interactive Brokers,
Genesys) -- checked and left null, not invented.

requirements:
    pip install requests

usage:
    python3 tcv_scraper.py            # writes ../data/tcv_companies.json
    python3 tcv_scraper.py --limit 10 # only the first ~10 for a test run
"""

import json
import os
import re
import sys
import time
from datetime import datetime, timezone

import requests

GRID_URL = "https://www.tcv.com/partnerships"
DETAIL_URL_TMPL = "https://www.tcv.com/partnerships/{slug}"
SOURCE_URL = "https://www.tcv.com/partnerships"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "data", "tcv_companies.json")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}
TIMEOUT = 45
RETRIES = 3
SLEEP_BETWEEN = 0.4

FLIGHT_RE = re.compile(r'self\.__next_f\.push\((\[.*?\])\)</script>', re.S)

# TCV's own 4 "sectors2" categories -> the 17-tag everywhere_tags taxonomy.
# "Application Software" is intentionally NOT mapped (too generic -- spans
# dev-tools/work/data/security/etc.) -- left to the keyword classifier.
SECTOR_TAG_MAP = {
    "Fintech & Payments": ["FinTech / Insurance"],
    "Consumer/SME": ["Consumer"],
    "Infrastructure Software": ["Dev Tools / Cloud"],
}

# everywhere_tags keyword classifier (substrings, lowercased) -- copied from
# menlo_scraper.py / rre_scraper.py / iconiq_scraper.py.
KEYWORD_TAGS = [
    ("BioTech", ["biotech", "drug", "therapeut", "oncolog", "cancer", "tumor", "genomic", "genome",
                 "molecul", "antibod", "protein", "vaccine", "clinical-stage", "medicine", "opioid", "life science",
                 "synthetic biology", "biolog"]),
    ("Health", ["healthcare", "health care", "patient", "clinic", "medical", "mental health", "telehealth",
                "health system", "health record", "diagnos", "surgical", "doctor", "hospital", "pharmac", "therapy",
                "musculoskeletal", " msk "]),
    ("Cybersecurity", ["cybersecurity", "security", "secure", "privacy", "fraud", "phishing", "malware",
                       "ransomware", "endpoint", "zero trust", "vulnerab", "authentication", "threat", "defense system",
                       "identity", "information protection", "machine identity"]),
    ("FinTech / Insurance", ["fintech", "payment", "bank", "lending", "loan", "insurance", "credit", "trading",
                             "wallet", "financ", "invoic", "accounting", "payroll", "treasury", "billing",
                             "pricing platform", "rebate", " tax", "audit", "money management", "robo-advisor",
                             "brokerage", "spend management", "capital markets", "investing", "claims", "broker"]),
    ("Web3 / Crypto", ["crypto", "blockchain", "web3", "token", "on-chain", "ethereum", "bitcoin", "decentral",
                       "stablecoin", "nft"]),
    ("Gaming / Media / Entertainment", ["game", "gaming", "music", "video", "creator", "content", "publish",
                                        "entertain", "newsletter", "podcast", "film", "streaming", "social media",
                                        "media platform"]),
    ("Dev Tools / Cloud", ["developer", " api ", "apis", "api platform", "infrastructure", "database", "cloud",
                           "open source", "devops", "sdk", "kubernetes", "container", "observability", "deploy",
                           "compute", "storage", "serverless", "inference", "networking", "ethernet", "coding",
                           "codebase", "low-code", "no-code", "source code", "development platform", "incident",
                           " sre", "voicemail", "communications", "llm", "foundation model", "interpretability",
                           "sd-wan", "wan ", "wide area network", "wireless edge", "artifact management",
                           "quality lab", "test automation", "machine data"]),
    ("Data & Analytics", ["analytics", "business intelligence", "data platform", "data warehouse", "data lake",
                          "data pipeline", "insights", "dashboard", "experimentation", "decision intelligence",
                          "data quality", "analyz", "data curation", "quality management", "relationship intelligence",
                          "data discovery", "data analysis", "edge-data", "complex data", "real-world data", "analyst",
                          "data intelligence", "data transformation", "data integration", "operational intelligence"]),
    ("Future of Work", ["workforce", "hiring", "recruit", "employee", "productivity", "collaborat", "talent",
                        "workplace", "human resources", " hr ", "learning platform", "customer success",
                        "customer service", "customer support", "presales", " sales ", "onboarding", "workflow",
                        "saas management", "ai assistant", "project management", "partnerships platform",
                        "partnership", "teamwork", "scheduling", "work assistant", "contract intelligence",
                        "legal and operational workflows"]),
    ("Transportation / Mobility", ["mobility", "vehicle", "transport", "autonomous", "fleet", "driving", "aviation",
                                   "aircraft", "electric vehicle", "scooter", " bike", "boat", "watercraft",
                                   "rideshar", "travel"]),
    ("Logistics / Supply Chain", ["logistics", "supply chain", "supply and demand", "freight", "warehouse",
                                  "delivery", "procurement", "inventory", "fulfillment", "shipping"]),
    ("PropTech", ["real estate", "property", "housing", "mortgage", "rental", "construction", "tenant"]),
    ("CPG", ["beverage", "snack", "consumer packaged", "beauty", "cosmetic", "apparel", "grocery", "skincare",
             "eyewear", "glasses", "footwear", "pet sitter", "pet ", "shaving", "grooming"]),
    ("Climate / Sustainability", ["climate", "carbon", "renewable", "solar", "battery", "sustainab", "emission",
                                  "clean energy", "ev charging", "electrif", "energy", "power is produced",
                                  "power grid"]),
    ("RegTech/Gov/Legal", ["legal", "compliance", "government", "regulat", "law firm", "attorney", "risk services",
                           "lawsuit", "lawyer", "tax compliance"]),
    ("Deeptech / Robotics / AR/VR", ["robot", "hardware", "semiconductor", "chip", "drone", "aerospace",
                                     "augmented reality", "virtual reality", "satellite", "quantum", "sensor",
                                     "rfid", "wifi", "space", "rocket", "launch vehicle", "optics", "defense",
                                     "wearable sensor", "computer vision", " iot ", "internet of things"]),
    ("Consumer", ["marketplace", "consumer", "shopping", "social network", "community", "app for", "app that",
                  "ecommerce", "e-commerce", "subscription", "retailer", "universit", "student", "education",
                  "learning", "fashion", "dating", "pet parent", "vacation rental", "hospitality"]),
]


def http_get(url):
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
    print(f"  ! FAILED (giving up): {url}: {last}", file=sys.stderr)
    return None


def clean(s):
    if s is None:
        return None
    s = re.sub(r"\s+", " ", s).strip()
    return s or None


def extract_flight_json(html, needle):
    """Next.js App Router ships page data as a series of
    `self.__next_f.push([1,"<escaped-json-string>"])` script tags. Find the
    chunk whose decoded string contains `needle`, then locate & json.loads the
    JSON object that starts right after the given key."""
    for raw in FLIGHT_RE.findall(html):
        try:
            arr = json.loads(raw)
        except (ValueError, TypeError):
            continue
        if len(arr) < 2 or not isinstance(arr[1], str):
            continue
        payload = arr[1]
        if needle in payload:
            yield payload


def parse_object_after_key(payload, key):
    """payload contains `"<key>":{...}` (plain JSON, no React $-refs inside) --
    slice out the balanced-brace object by scanning with a string-aware brace
    counter (the naive 'find matching }}' used for a couple of known
    terminators breaks on nested braces in track-record text)."""
    marker = f'"{key}":{{'
    start = payload.find(marker)
    if start == -1:
        return None
    obj_start = start + len(marker) - 1  # position of the opening '{'
    depth = 0
    in_str = False
    esc = False
    i = obj_start
    n = len(payload)
    while i < n:
        c = payload[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        else:
            if c == '"':
                in_str = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    i += 1
                    break
        i += 1
    obj_str = payload[obj_start:i]
    try:
        return json.loads(obj_str)
    except (ValueError, TypeError):
        return None


def fetch_grid():
    html = http_get(GRID_URL)
    if not html:
        raise SystemExit(f"FATAL: could not fetch {GRID_URL}")
    for payload in extract_flight_json(html, '"companies":['):
        arr = parse_object_after_key("{" + payload[payload.find('"companies":['):], "companies")
        if arr is not None:
            return arr.get("items") if isinstance(arr, dict) else arr
        # parse_object_after_key expects "key":{ - companies is "key":[ so handle directly
    # fallback: manual bracket scan for "companies":[ ... ]
    for payload in extract_flight_json(html, '"companies":['):
        start = payload.find('"companies":[') + len('"companies":')
        depth = 0
        in_str = False
        esc = False
        i = start
        n = len(payload)
        started = False
        while i < n:
            c = payload[i]
            if in_str:
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == '"':
                    in_str = False
            else:
                if c == '"':
                    in_str = True
                elif c in "[{":
                    depth += 1
                    started = True
                elif c in "]}":
                    depth -= 1
                    if started and depth == 0:
                        i += 1
                        break
            i += 1
        arr_str = payload[start:i]
        try:
            return json.loads(arr_str)
        except (ValueError, TypeError) as e:
            print(f"  ! could not parse companies grid JSON: {e}", file=sys.stderr)
    return []


def fetch_detail(slug):
    url = DETAIL_URL_TMPL.format(slug=slug)
    html = http_get(url)
    if not html:
        return None, url
    for payload in extract_flight_json(html, '"company":{'):
        company = parse_object_after_key(payload, "company")
        if company:
            return company, url
    return None, url


TICKER_RE = re.compile(r"\(([A-Za-z. ]{2,20}):\s*([A-Z]{1,6}(?:\.[A-Z])?)\)")
# Year can land either before the ticker parenthetical ("went public in 2020 on
# the NASDAQ ... (NASDAQ: ABNB)") or after it ("went public on the ASX (ASX:
# SDR) in 2021") -- match a whole sentence around "went public"/"is listed" and
# pull the first 4-digit year found anywhere in it.
WENT_PUBLIC_SENTENCE_RE = re.compile(r"[^.]*(?:went public|is listed)[^.]*\.", re.I)
YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")
TCV_SECONDARY_RE = re.compile(
    r"TCV[’']s (?:ownership|stake|position) in .{0,60}?was acquired by\s+"
    r"([A-Za-z0-9&.,’'\- ]+?)\s+in\s+(\d{4})", re.I)
GENERIC_ACQUIRED_RE = re.compile(
    r"(?<!ownership in )(?<!stake in )(?<!position in )acquired by\s+"
    r"([A-Za-z0-9&.,’'\- ]+?)(?:\s*\([A-Za-z. ]{2,20}:\s*[A-Z]{1,6}\))?\s+in\s+(\d{4})", re.I)


def derive_exit(about):
    """TCV's `status` field is only ever Active/Prior -- no structured
    acquirer/ticker/exit-year field exists. Mine the free-text `about`
    paragraph instead (see module docstring for the phrasing patterns and
    traps). Returns (exit_type, acquirer, ticker_symbol, exit_year), any of
    which may be None if the prose doesn't state it."""
    if not about:
        return None, None, None, None
    secondary = TCV_SECONDARY_RE.search(about)
    if secondary:
        return "Secondary Sale (TCV exit)", clean(secondary.group(1)), None, int(secondary.group(2))
    acquired = GENERIC_ACQUIRED_RE.search(about)
    if acquired:
        return "Acquired", clean(acquired.group(1)), None, int(acquired.group(2))
    tick = TICKER_RE.search(about)
    went_public = re.search(r"went public|is listed", about, re.I)
    if tick and went_public:
        sentence = WENT_PUBLIC_SENTENCE_RE.search(about)
        yr = YEAR_RE.search(sentence.group(0)) if sentence else None
        return "Public", None, f"{tick.group(1).strip()}: {tick.group(2).strip()}", (int(yr.group(0)) if yr else None)
    return None, None, None, None


def sectors_from(sectors2):
    if not sectors2:
        return []
    out = []
    for item in sectors2.get("items", []) or []:
        name = clean(item.get("name"))
        if name and name not in out:
            out.append(name)
    return out


def everywhere_tags(name, description, sectors):
    """TCV sectors first (mapped via SECTOR_TAG_MAP), then keyword fallback on
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


def main():
    limit = int(sys.argv[sys.argv.index("--limit") + 1]) if "--limit" in sys.argv else None

    grid = fetch_grid()
    if not grid:
        raise SystemExit("FATAL: grid parse returned 0 companies -- site structure may have changed")
    print(f"grid: {len(grid)} companies found on {GRID_URL}")
    if limit:
        grid = grid[:limit]

    scraped_at = datetime.now(timezone.utc).isoformat()
    out = []
    for i, g in enumerate(grid, 1):
        slug = g.get("slug")
        name = clean(g.get("name"))
        if not name or not slug:
            continue
        print(f"  [{i}/{len(grid)}] {name} ({slug})")
        detail, profile_url = fetch_detail(slug)
        time.sleep(SLEEP_BETWEEN)

        # prefer detail-page fields; fall back to the grid record if the
        # detail fetch failed for some reason (politeness / transient error)
        d = detail or {}
        description = clean(d.get("about")) or clean(g.get("description"))
        tagline = clean(d.get("tagline")) or clean(g.get("description"))
        location = clean(d.get("location")) or clean(g.get("location"))
        continent = clean(d.get("continent")) or clean(g.get("continent"))
        status = clean(d.get("status")) or clean(g.get("status"))
        sectors = sectors_from(d.get("sectors2")) or sectors_from(g.get("sectors2"))
        logo = (d.get("logo") or g.get("logo") or {}).get("url")
        hero = (d.get("heroImage") or g.get("heroImage") or {}).get("url") if (d.get("heroImage") or g.get("heroImage")) else None

        exit_type, acquirer, ticker_symbol, exit_year = derive_exit(d.get("about"))

        investment_team = []
        for member in (d.get("investmentTeam") or {}).get("items", []) or []:
            mname = clean(member.get("name"))
            if not mname:
                continue
            mslug = member.get("slug")
            investment_team.append({
                "name": mname,
                "title": clean(member.get("title")),
                "profile_url": f"https://www.tcv.com/team/{mslug}" if mslug else None,
            })

        out.append({
            "company_name": name,
            "tagline": tagline,
            "description": description,
            "company_url": clean(d.get("websiteUrl")),
            "linkedin_url": clean(d.get("linkedInUrl")),
            "twitter_url": clean(d.get("twitterUrl")),
            "jobs_url": clean(d.get("jobsUrl")),
            "location": location,
            "continent": continent,
            "year_founded": d.get("yearFounded") if isinstance(d.get("yearFounded"), int) else None,
            "sectors": sectors,
            "status": status,
            "exit_type": exit_type,
            "acquirer": acquirer,
            "ticker_symbol": ticker_symbol,
            "exit_year": exit_year,
            "investment_team": investment_team,
            "tcv_perspective": clean(d.get("tcvPerspective")),
            "logo_url": clean(logo),
            "hero_image_url": clean(hero),
            "company_profile_url": profile_url,
            "everywhere_tags": everywhere_tags(name, description, sectors),
            "source_url": SOURCE_URL,
            "scraped_at": scraped_at,
        })

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2)

    # ---- summary ----
    n = len(out)
    print(f"\nwrote {n} companies -> {OUT}")
    for field in ("tagline", "description", "company_url", "linkedin_url", "location", "logo_url"):
        miss = sum(1 for r in out if not r[field])
        print(f"  {field:18s} missing: {miss}/{n}")
    print(f"  year_founded missing: {sum(1 for r in out if r['year_founded'] is None)}/{n}")
    print(f"  sectors empty:        {sum(1 for r in out if not r['sectors'])}/{n}")
    by_status = {}
    for r in out:
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1
    print(f"  by status: {by_status}")
    print(f"  exit_type populated: {sum(1 for r in out if r['exit_type'])}/{n}"
          f"  (acquirer: {sum(1 for r in out if r['acquirer'])}, "
          f"ticker: {sum(1 for r in out if r['ticker_symbol'])}, "
          f"exit_year: {sum(1 for r in out if r['exit_year'])})")
    print(f"  investment_team empty: {sum(1 for r in out if not r['investment_team'])}/{n}")
    untagged = [r["company_name"] for r in out if not r["everywhere_tags"]]
    print(f"  untagged: {len(untagged)}/{n}" + (f" -> {untagged}" if untagged else ""))
    by_tag = {}
    for r in out:
        for t in r["everywhere_tags"]:
            by_tag[t] = by_tag.get(t, 0) + 1
    print("  by everywhere_tag:")
    for t, k in sorted(by_tag.items(), key=lambda x: -x[1]):
        print(f"    {k:3d}  {t}")


if __name__ == "__main__":
    main()
