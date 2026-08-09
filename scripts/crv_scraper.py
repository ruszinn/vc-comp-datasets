#!/usr/bin/env python3
"""
CRV (Charles River Ventures) portfolio scraper -> crv_companies.json

Scrapes CRV's portfolio (https://www.crv.com/companies) into a JSON file.
The site is a Next.js (App Router) build backed by Sanity.io. There is no
public REST/GraphQL endpoint and no `__NEXT_DATA__` blob -- company data is
streamed as React Server Component (RSC) payloads via `self.__next_f.push([1,
"..."])` script tags. One of those pushed strings is a JS string literal
(escaped, e.g. `\\"`) whose decoded body is `<id>:{"categories": [...],
"companies": [...]}` -- found by grepping the pushed chunks for the literal
marker `"companies":[` (its numeric RSC id/position is NOT stable across
requests, so don't hardcode a chunk index).

Each company object exposes (verified against all ~183 records):
  - name, slug                                        (183/183)
  - about                -> Sanity Portable Text blocks; join span text       (183/183)
  - website               -> external company URL                            (159/183; 24 legacy/acquired cos have none)
  - logo.asset.url        -> direct Sanity CDN image URL (no Webflow CDN dependency) (183/183)
  - teamMembers           -> [{firstName, lastName}, ...]                     (147/183)
  - timeline              -> ["<Month? Year> - <Event>", ...] where Event is
                             one of Founded / Partnered / Acquired by <X> /
                             IPO[ (<TICKER>)] / Merged with <X>               (181/183)
  - categories            -> ONLY ever the single "Featured" curation flag
                             (16/183 have it) -- NOT a real sector taxonomy,
                             so this scraper does not treat it as `sectors`
                             and relies on `everywhere_tags` keyword-matching
                             name + about text instead.

Empty != absent, checked: CRV publishes no separate status/exit/acquirer/
ticker field, but ALL of that is denormalized into the `timeline` array as
"<date> - Acquired by <Acquirer>" / "<date> - IPO (<TICKER>)" / "<date> -
Merged with <X>" events (60 exit events across 57 companies) -- parsed out
into status/acquirer/ticker_symbol/exit_year. `year_founded` and
`year_partnered` (year CRV invested) are likewise mined from the "Founded"/
"Partnered" timeline entries rather than left null.

requirements:
    pip install requests beautifulsoup4  (bs4 unused here but kept per template)

usage:
    python3 crv_scraper.py            # writes ../data/crv_companies.json
    python3 crv_scraper.py --limit 20 # only the first ~20 for a test run
"""

import json
import os
import re
import sys
import time
from datetime import datetime, timezone

import requests

URL = "https://www.crv.com/companies"
SOURCE_URL = "https://www.crv.com/companies"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "data", "crv_companies.json")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}
TIMEOUT = 45
RETRIES = 4

RSC_PUSH_RE = re.compile(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)</script>', re.S)

MONTHS = ("January|February|March|April|May|June|July|August|September|October|November|December")
DATE_RE = re.compile(rf"^\s*(?:(?:{MONTHS})\s+)?(\d{{4}})\s*$")

# everywhere_tags keyword classifier (substrings, lowercased) -- copied from
# rre_scraper.py / iconiq_scraper.py. CRV publishes no real sector taxonomy of
# its own (the only structured "category" is the single "Featured" curation
# flag, seen on 16/183 companies), so tagging here is 100% keyword-driven from
# name + about text.
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
                           "text to speech", "software company", "platform for engineers", "engineering team"]),
    ("Data & Analytics", ["analytics", "business intelligence", "data platform", "data warehouse", "data lake",
                          "data pipeline", "insights", "dashboard", "experimentation", "decision intelligence", "data quality",
                          "analyz", "data curation", "quality management", "relationship intelligence",
                          "data discovery", "data analysis", "edge-data", "complex data", "real-world data", "analyst",
                          "data intelligence", "data transformation", "data integration"]),
    ("Future of Work", ["workforce", "hiring", "recruit", "employee", "productivity", "collaborat", "talent",
                        "workplace", "human resources", " hr ", "learning platform", "customer success", "customer service",
                        "customer support", "presales", " sales ", "onboarding", "workflow", "saas management", "ai assistant",
                        "project management", "partnerships platform", "partnership", "teamwork", "scheduling", "work assistant",
                        "relationship management"]),
    ("Transportation / Mobility", ["mobility", "vehicle", "transport", "autonomous", "fleet", "driving", "aviation",
                                   "aircraft", "electric vehicle", "scooter", " bike", "boat", "watercraft", "rideshar", "travel",
                                   "trucking", "drone-as-first-responder"]),
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
    s = re.sub(r"\s+", " ", s).strip()
    return s or None


_JS_ESCAPE_RE = re.compile(r'\\(u[0-9a-fA-F]{4}|.)')
_JS_ESCAPE_MAP = {'"': '"', '\\': '\\', 'n': '\n', 't': '\t', 'r': '\r', 'b': '\b', 'f': '\f', '/': '/'}


def js_string_unescape(s):
    """Unescape a JS string literal's escape sequences (\\", \\\\, \\n, \\uXXXX,
    ...) WITHOUT touching already-correct UTF-8 characters. NB: `codecs.decode
    (s, "unicode_escape")` looks tempting here but is wrong -- it operates
    byte-by-byte under latin-1 semantics and mangles any literal non-ASCII
    UTF-8 character already in the string (e.g. GrabCAD's real "'" curly quote
    -> "\xe2\x80\x99" mojibake), which is why this hand-rolled regex is used
    instead."""
    def repl(m):
        esc = m.group(1)
        if esc in _JS_ESCAPE_MAP:
            return _JS_ESCAPE_MAP[esc]
        if esc.startswith("u"):
            return chr(int(esc[1:5], 16))
        return m.group(0)
    return _JS_ESCAPE_RE.sub(repl, s)


def extract_companies_payload(html):
    """Find the __next_f.push chunk that carries the companies array. Its
    position among the pushed chunks is NOT stable across requests, so match
    by content (the literal `"companies":[` marker), not by index."""
    for raw in RSC_PUSH_RE.findall(html):
        if '\\"companies\\":[' not in raw and '"companies":[' not in raw:
            continue
        decoded = js_string_unescape(raw)
        # strip the leading RSC row id -- a hex counter, e.g. "18:[...]" or
        # "1a:[...]" -> "[...]" (NOT necessarily decimal digits only)
        body = decoded.split(":", 1)[1] if re.match(r"^[0-9a-fA-F]+:", decoded) else decoded
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            continue
        # data is ["$", "$L1f", null, {"categories": [...], "companies": [...]}]
        for el in data:
            if isinstance(el, dict) and "companies" in el:
                return el["companies"]
    return None


def about_text(blocks):
    texts = []
    for b in blocks or []:
        for ch in b.get("children", []) or []:
            t = ch.get("text")
            if t:
                texts.append(t)
    return clean(" ".join(texts))


def parse_timeline(timeline):
    """CRV has no structured founded/status/exit fields -- everything lives in
    the `timeline` list of "<date> - <Event>" strings. Mine:
      year_founded      <- "<date> - Founded"
      year_partnered    <- "<date> - Partnered"   (year CRV invested)
      status            <- "Acquired" (any Acquired/Merged event) else
                           "Public" (any IPO event) else "Active"
      acquirer          <- text after "Acquired by " / "Merged with "
      ticker_symbol     <- verbatim parenthetical after "IPO" (may be absent,
                           or a bare ticker, or "EXCH:TICK" -- kept as published)
      exit_year         <- year of the Acquired/IPO event that set `status`
    """
    year_founded = year_partnered = None
    status, acquirer, ticker_symbol, exit_year = "Active", None, None, None

    for entry in timeline or []:
        if " - " not in entry:
            continue
        date_part, event = entry.split(" - ", 1)
        date_part, event = date_part.strip(), event.strip()
        m = DATE_RE.match(date_part)
        year = int(m.group(1)) if m else None

        if event == "Founded":
            year_founded = year
            continue
        if event == "Partnered":
            year_partnered = year
            continue

        acq_m = re.match(r"Acquired by\s+(.+)$", event, re.I)
        merge_m = re.match(r"Merged with\s+(.+)$", event, re.I)
        ipo_m = re.match(r"IPO\s*(?:\((.+)\))?$", event, re.I)

        if acq_m or merge_m:
            status = "Acquired"
            acquirer = clean((acq_m or merge_m).group(1))
            exit_year = year
        elif ipo_m and status != "Acquired":
            status = "Public"
            ticker_symbol = clean(ipo_m.group(1)) if ipo_m.group(1) else ticker_symbol
            exit_year = year

    return year_founded, year_partnered, status, acquirer, ticker_symbol, exit_year


def everywhere_tags(name, description):
    """CRV's only structured category is the single "Featured" curation flag
    (not a sector taxonomy), so tagging is keyword-only on name + about text.
    Order most->least relevant, cap at 4."""
    tags = []
    text = f"{name or ''} {description or ''}".lower()
    for tag, kws in KEYWORD_TAGS:
        if any(kw in text for kw in kws):
            tags.append(tag)
    return tags[:4]


def parse_company(c, scraped_at):
    name = clean(c.get("name"))
    if not name:
        return None
    slug = clean(c.get("slug"))
    description = about_text(c.get("about"))
    company_url = clean(c.get("website"))

    logo = c.get("logo") or {}
    logo_url = clean(((logo.get("asset") or {}).get("url")))

    team = []
    for tm in c.get("teamMembers") or []:
        full = clean(f"{tm.get('firstName') or ''} {tm.get('lastName') or ''}")
        if full:
            team.append(full)

    is_featured = any((cat or {}).get("slug") == "featured" for cat in (c.get("categories") or []))

    year_founded, year_partnered, status, acquirer, ticker_symbol, exit_year = parse_timeline(c.get("timeline"))

    return {
        "company_name": name,
        "slug": slug,
        "description": description,
        "company_url": company_url,
        "logo_url": logo_url,
        "founders": team,
        "year_founded": year_founded,
        "year_partnered": year_partnered,
        "status": status,
        "acquirer": acquirer,
        "ticker_symbol": ticker_symbol,
        "exit_year": exit_year,
        "featured": is_featured,
        "everywhere_tags": everywhere_tags(name, description),
        "source_url": SOURCE_URL,
        "scraped_at": scraped_at,
    }


def main():
    limit = int(sys.argv[sys.argv.index("--limit") + 1]) if "--limit" in sys.argv else None

    print(f"Fetching {URL}")
    html = get(URL)
    companies_raw = extract_companies_payload(html)
    if companies_raw is None:
        raise SystemExit("FATAL: could not locate the companies RSC payload on the page")

    scraped_at = datetime.now(timezone.utc).isoformat()
    out, seen = [], set()
    for c in companies_raw:
        rec = parse_company(c, scraped_at)
        if not rec:
            continue
        k = rec["company_name"].strip().lower()
        if k in seen:
            print(f"  ! duplicate '{rec['company_name']}' — keeping first", file=sys.stderr)
            continue
        seen.add(k)
        out.append(rec)
        if limit and len(out) >= limit:
            break

    out.sort(key=lambda o: o["company_name"].lower())

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2)

    from collections import Counter
    n = len(out)
    by_status = Counter(o["status"] for o in out)
    by_tag = Counter(t for o in out for t in o["everywhere_tags"])
    print(f"\nWrote {n} companies -> {OUT}")
    print("By status:", dict(by_status),
          "| with acquirer:", sum(1 for o in out if o["acquirer"]),
          "| with ticker:", sum(1 for o in out if o["ticker_symbol"]))
    print("With website:", sum(1 for o in out if o["company_url"]),
          "| with description:", sum(1 for o in out if o["description"]),
          "| with logo:", sum(1 for o in out if o["logo_url"]),
          "| with founders:", sum(1 for o in out if o["founders"]),
          "| with year_founded:", sum(1 for o in out if o["year_founded"]),
          "| with year_partnered:", sum(1 for o in out if o["year_partnered"]),
          "| featured:", sum(1 for o in out if o["featured"]))
    untagged = [o["company_name"] for o in out if not o["everywhere_tags"]]
    print(f"Untagged: {len(untagged)}/{n}" + (f" -> {untagged}" if untagged else ""))
    print("By everywhere_tag:")
    for t, k in by_tag.most_common():
        print(f"  {k:>4}  {t}")


if __name__ == "__main__":
    main()
