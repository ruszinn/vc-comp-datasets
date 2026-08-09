#!/usr/bin/env python3
"""
Spark Capital portfolio scraper -> spark_companies.json

Scrapes Spark Capital's portfolio (https://www.sparkcapital.com/companies) into
a JSON file. The site is a Webflow build (native Webflow CMS collection lists,
`w-dyn-item` -- no Finsweet), and the company data is **fully server-rendered
in the static HTML** -- one page, no API, no pagination.

Page structure: a tab widget with 6 panes -- "All", "Consumer", "Enterprise",
"AI", "Frontier" (Tech), "Fintech". The "All" pane (48 items) is exactly the
union/dedupe of the other 5 sector panes (48 unique names total, confirmed by
diffing name sets) -- so we scrape the 5 sector panes and union each company's
sector membership, skipping "All" to avoid double counting.

Per `.collection-item.w-dyn-item`:
  - `<h3 class="h3 margin-top---0">`                    -> company name
  - `.company-specs.spacing---extra-small` (text node)  -> description (all 48)
  - `.company-link[href]` (inside `.website-link`)      -> external website (all 48)
  - `.modal-image-wrap img[src]`                          -> logo (all 48)
  - `.acquisition-spec` (text, empty for most)          -> denormalized exit info
    (Empty != absent): "NYSE: TWTR in 2013" / "NASDAQ: COIN in 2021" (public) or
    "Acquired by Yahoo in 2013" (M&A). Only 10/48 are non-empty (the rest are
    active private companies) -- parsed into status/ticker_symbol/exchange or
    status/acquirer/exit_year.
  - sector = which of the 5 tab panes (Consumer/Enterprise/AI/Frontier/Fintech)
    the company's card appears under; a company can appear in multiple tabs
    (e.g. Anthropic: Consumer + Enterprise + AI).

What Spark does NOT expose on this page (checked names + descriptions per the
"Empty != absent" rule -- no systematic hits beyond the acquisition-spec field
already captured; one incidental description mentions "based in Austin, Texas"
(Base Power) but that's a one-off aside, not a site-wide structured field, so
no `location` field is added): no founders, no investment stage/round, no
founded year, no per-company Spark detail page (the only outbound link is the
company's own site), no location/HQ field.

Network note: this sandbox cannot route to sparkcapital.com's current Webflow
edge IP (198.202.211.1 -- connection times out), and the previously-documented
legacy-IP workaround (--resolve host:443:75.2.70.75) now fails TLS with an
"internal_error" alert for this host (tested; likely decommissioned since it
was last verified). `fetch()` therefore tries, in order: (1) normal HTTPS,
(2) the legacy Webflow IP via a monkeypatched `socket.getaddrinfo`, (3) the
`r.jina.ai` read-only fetch proxy (relays the *same* sparkcapital.com content;
not a third-party data source -- it fetches this page verbatim). Whichever
path succeeds, the parsed HTML is identical.

requirements:
    pip install requests beautifulsoup4

usage:
    python3 spark_scraper.py            # writes ../data/spark_companies.json
    python3 spark_scraper.py --limit 10 # only the first ~10 for a test run
"""

import html
import json
import os
import re
import socket
import sys
import time
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://www.sparkcapital.com/companies"
SOURCE_URL = "https://www.sparkcapital.com/companies"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "data", "spark_companies.json")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}
TIMEOUT = 45
RETRIES = 3

LEGACY_WEBFLOW_IP = "75.2.70.75"
JINA_PROXY = "https://r.jina.ai/"

# Tab panes to scrape. "All" is skipped -- it's the union/dedupe of these 5,
# confirmed by comparing name sets during recon (48 == 48, no extra names).
SECTOR_PANES = ["Consumer", "Enterprise", "AI", "Frontier", "Fintech"]
CANON_ORDER = SECTOR_PANES


def _real_getaddrinfo(*args, **kwargs):
    raise NotImplementedError  # placeholder, replaced at runtime


def fetch_direct(url):
    """Normal HTTPS fetch, no DNS tricks."""
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    return r.text


def fetch_legacy_ip(url):
    """Monkeypatch socket.getaddrinfo so requests connects to the legacy
    Webflow edge IP for this host, then restore normal DNS resolution."""
    from urllib.parse import urlparse

    host = urlparse(url).hostname
    orig_getaddrinfo = socket.getaddrinfo

    def patched(node, *args, **kwargs):
        if node == host:
            return orig_getaddrinfo(LEGACY_WEBFLOW_IP, *args, **kwargs)
        return orig_getaddrinfo(node, *args, **kwargs)

    socket.getaddrinfo = patched
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        return r.text
    finally:
        socket.getaddrinfo = orig_getaddrinfo


def fetch_via_jina(url):
    """Read-only fetch-proxy relay of the *same* sparkcapital.com page (used
    only because this sandbox cannot route to the site directly / via the
    legacy IP) -- not a third-party data source."""
    r = requests.get(
        JINA_PROXY + url,
        headers={**HEADERS, "X-Respond-With": "html"},
        timeout=TIMEOUT,
    )
    r.raise_for_status()
    return r.text


def fetch(url):
    last = None
    for attempt in range(1, RETRIES + 1):
        for name, fn in (
            ("direct", fetch_direct),
            ("legacy-ip", fetch_legacy_ip),
            ("jina-proxy", fetch_via_jina),
        ):
            try:
                text = fn(url)
                if text and len(text) > 1000:
                    return text
            except Exception as e:  # noqa
                last = e
                print(f"  ! {name} fetch failed ({e})", file=sys.stderr)
        wait = 1.5 * attempt
        print(f"  ! all fetch strategies failed; retry {attempt}/{RETRIES} in {wait:.1f}s", file=sys.stderr)
        time.sleep(wait)
    raise SystemExit(f"FATAL: could not fetch {url}: {last}")


def clean(s):
    if s is None:
        return None
    s = html.unescape(s)
    s = re.sub(r"\s+", " ", s).strip()
    return s or None


# Denormalized exit info lives in `.acquisition-spec`, one of two shapes:
#   "NYSE: TWTR in 2013"          -> public, exchange + ticker + year
#   "Acquired by Yahoo in 2013"   -> M&A, acquirer + year
RE_PUBLIC = re.compile(r"^([A-Za-z .]+):\s*([A-Za-z.]+)\s+in\s+(\d{4})$")
RE_ACQUIRED = re.compile(r"^Acquired by\s+(.+?)\s+in\s+(\d{4})$", re.IGNORECASE)


def parse_acquisition_spec(spec):
    """Returns (status, exchange, ticker_symbol, acquirer, exit_year)."""
    if not spec:
        return "Active", None, None, None, None
    m = RE_PUBLIC.match(spec)
    if m:
        exchange, ticker, year = m.groups()
        return "Public", exchange.strip(), ticker.strip(), None, int(year)
    m = RE_ACQUIRED.match(spec)
    if m:
        acquirer, year = m.groups()
        return "Acquired", None, None, acquirer.strip(), int(year)
    # unrecognized shape -- keep the raw text visible via status, don't fabricate
    return spec, None, None, None, None


# everywhere_tags keyword classifier (substrings, lowercased) -- adapted from
# menlo_scraper.py / iconiq_scraper.py.
KEYWORD_TAGS = [
    ("BioTech", ["biotech", "drug", "therapeut", "oncolog", "cancer", "tumor", "genomic", "genome",
                 "molecul", "antibod", "protein", "vaccine", "clinical-stage", "medicine", "opioid", "life science",
                 "synthetic biology", "biolog"]),
    ("Health", ["healthcare", "health care", "patient", "clinic", "medical", "mental health", "telehealth",
                "health system", "health record", "diagnos", "surgical", "doctor", "hospital", "pharmac", "therapy",
                "medication"]),
    ("Cybersecurity", ["cybersecurity", "security", "privacy", "fraud", "phishing", "malware",
                       "ransomware", "endpoint", "zero trust", "vulnerab", "authentication", "threat", "identity",
                       "information protection", "secure software"]),
    ("FinTech / Insurance", ["fintech", "payment", "bank", "lending", "loan", "insurance", "credit", "trading",
                             "wallet", "financ", "invoic", "accounting", "payroll", "treasury", "billing",
                             "capital markets", "investing", "claims", "prediction market"]),
    ("Web3 / Crypto", ["crypto", "blockchain", "web3", "token", "on-chain", "ethereum", "bitcoin", "decentral",
                        "stablecoin", "nft"]),
    ("Gaming / Media / Entertainment", ["game", "gaming", "music", "video", "creator", "content", "publish",
                                        "entertain", "newsletter", "podcast", "film", "streaming", "social media",
                                        "media platform", "talking about"]),
    ("Dev Tools / Cloud", ["developer", " api ", "apis", "api platform", "infrastructure", "database", "cloud",
                           "open source", "devops", "sdk", "kubernetes", "container", "observability", "deploy",
                           "compute", "data storage", "cloud storage", "serverless", "inference", "networking",
                           "coding", "codebase", "low-code", "no-code", "source code", "development platform",
                           "llm", "foundation model", "interpretability", "neural interface", "software platform",
                           "chip", "semiconductor", "silicon", "supercomputing"]),
    ("Data & Analytics", ["analytics", "business intelligence", "data platform", "data warehouse", "data lake",
                          "data pipeline", "insights", "dashboard", "experimentation", "decision intelligence",
                          "data quality", "analyz", "research", "scientific r&d", "hypothesis"]),
    ("Future of Work", ["workforce", "hiring", "recruit", "employee", "productivity", "collaborat", "talent",
                        "workplace", "human resources", " hr ", "customer success", "customer service",
                        "customer support", "onboarding", "workflow", "hr platform", "candidates", "job search",
                        "assistant", "voice, video, and text"]),
    ("Transportation / Mobility", ["mobility", "vehicle", "transport", "autonomous", "fleet", "driving", "aviation",
                                   "aircraft", "electric vehicle", "scooter", " bike", "rideshar", "airplane",
                                   "flight", "airspace"]),
    ("Logistics / Supply Chain", ["logistics", "supply chain", "freight", "warehouse", "delivery", "procurement",
                                  "inventory", "fulfillment", "shipping"]),
    ("PropTech", ["real estate", "property", "housing", "mortgage", "rental", "construction", "tenant"]),
    ("CPG", ["beverage", "snack", "consumer packaged", "beauty", "cosmetic", "apparel", "grocery", "skincare",
             "eyewear", "footwear", "protein", "seafood", "food"]),
    ("Climate / Sustainability", ["climate", "carbon", "renewable", "solar", "battery", "sustainab", "emission",
                                  "clean energy", "ev charging", "electrif", "energy", "electrical grid",
                                  "power grid", "battery storage"]),
    ("RegTech/Gov/Legal", ["legal", "compliance", "government", "regulat", "law firm", "attorney", "public safety",
                           "surveillance", "defense", "security camera", "eliminate crime", "safer communities"]),
    ("Deeptech / Robotics / AR/VR", ["robot", "hardware", "drone", "aerospace", "augmented reality",
                                     "virtual reality", "satellite", "quantum", "sensor", "space", "rocket",
                                     "launch vehicle", "spacecraft", "neural interface", "optics", "x-ray",
                                     "ct scanner", "photonics"]),
    ("Consumer", ["marketplace", "consumer", "shopping", "social network", "social media", "community", "app for",
                  "app that", "ecommerce", "e-commerce", "subscription", "messaging", "friends"]),
]


def everywhere_tags(name, description, sectors):
    """Spark's own sector tabs are broad (Consumer/Enterprise/AI/Frontier/
    Fintech) and don't map cleanly to the 17-tag taxonomy (AI alone isn't a
    category; Consumer/Enterprise/Frontier are too generic) -- so tagging is
    keyword-only on name + description. `sectors` kept for the signature/
    parity with other scrapers but unused in the mapping itself."""
    tags = []
    text = f"{name or ''} {description or ''}".lower()
    for tag, kws in KEYWORD_TAGS:
        if tag in tags:
            continue
        if any(kw in text for kw in kws):
            tags.append(tag)
    return tags[:4]


ITEM_RE = re.compile(
    r'<div (?:id="w-node-[^"]*" )?role="listitem" class="collection-item w-dyn-item">(.*?)'
    r'<div class="modal-bg-close">',
    re.S,
)
PANE_ID = {
    "All": "w-tabs-0-data-w-pane-0",
    "Consumer": "w-tabs-0-data-w-pane-1",
    "Enterprise": "w-tabs-0-data-w-pane-2",
    "AI": "w-tabs-0-data-w-pane-3",
    "Frontier": "w-tabs-0-data-w-pane-4",
    "Fintech": "w-tabs-0-data-w-pane-5",
}


def parse_pane(html_text, pane_name):
    """Slice out one tab pane's HTML (from its pane id to the next pane's, or
    end of doc) and regex out each `.collection-item.w-dyn-item` company card."""
    ids_in_order = ["All", "Consumer", "Enterprise", "AI", "Frontier", "Fintech"]
    idx = ids_in_order.index(pane_name)
    start_marker = f'id="{PANE_ID[pane_name]}"'
    start = html_text.find(start_marker)
    if start == -1:
        return []
    if idx + 1 < len(ids_in_order):
        end_marker = f'id="{PANE_ID[ids_in_order[idx + 1]]}"'
        end = html_text.find(end_marker, start)
        if end == -1:
            end = len(html_text)
    else:
        end = len(html_text)
    chunk = html_text[start:end]

    items = []
    for it in ITEM_RE.findall(chunk):
        h3 = re.search(r'<h3 class="h3 margin-top---0">([^<]*)</h3>', it)
        name = clean(h3.group(1)) if h3 else None
        if not name:
            continue
        desc = re.search(r'class="company-specs spacing---extra-small">([^<]*)</div>', it)
        acq = re.search(r'class="acquisition-spec[^"]*">([^<]*)</div>', it)
        website = re.search(r'class="website-link"><a href="([^"]*)"', it)
        logo = re.search(r'class="modal-image-wrap"><img[^>]*\s+src="([^"]*)"', it)
        items.append({
            "name": name,
            "desc": clean(desc.group(1)) if desc else None,
            "acq": clean(acq.group(1)) if acq else None,
            "website": clean(website.group(1)) if website else None,
            "logo": clean(logo.group(1)) if logo else None,
        })
    return items


def main():
    limit = int(sys.argv[sys.argv.index("--limit") + 1]) if "--limit" in sys.argv else None

    html_text = fetch(BASE_URL)

    companies = {}
    for pane in SECTOR_PANES:
        items = parse_pane(html_text, pane)
        for it in items:
            name = it["name"]
            if name not in companies:
                companies[name] = {
                    "name": name,
                    "desc": it["desc"],
                    "website": it["website"],
                    "logo": it["logo"],
                    "acq": it["acq"],
                    "sectors": [],
                }
            if pane not in companies[name]["sectors"]:
                companies[name]["sectors"].append(pane)
        time.sleep(0.3)

    scraped_at = datetime.now(timezone.utc).isoformat()
    out = []
    for name, c in companies.items():
        sectors = [s for s in CANON_ORDER if s in c["sectors"]]
        status, exchange, ticker, acquirer, exit_year = parse_acquisition_spec(c["acq"])
        rec = {
            "company_name": name,
            "description": c["desc"],
            "company_url": c["website"],
            "logo_url": c["logo"],
            "sectors": sectors,
            "status": status,
            "exchange": exchange,
            "ticker_symbol": ticker,
            "acquirer": acquirer,
            "exit_year": exit_year,
            "everywhere_tags": everywhere_tags(name, c["desc"], sectors),
            "source_url": SOURCE_URL,
            "scraped_at": scraped_at,
        }
        out.append(rec)
        if limit and len(out) >= limit:
            break

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2)

    # ---- summary ----
    n = len(out)
    print(f"wrote {n} companies -> {OUT}")
    for field in ("description", "company_url", "logo_url"):
        miss = sum(1 for r in out if not r[field])
        print(f"  {field:14s} missing: {miss}/{n}")
    print(f"  sectors empty:  {sum(1 for r in out if not r['sectors'])}/{n}")
    by_status = {}
    for r in out:
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1
    print(f"  status breakdown: {by_status}")
    untagged = [r["company_name"] for r in out if not r["everywhere_tags"]]
    print(f"  untagged:       {len(untagged)}/{n}" + (f" -> {untagged}" if untagged else ""))
    by_tag = {}
    for r in out:
        for t in r["everywhere_tags"]:
            by_tag[t] = by_tag.get(t, 0) + 1
    print("  by everywhere_tag:")
    for t, k in sorted(by_tag.items(), key=lambda x: -x[1]):
        print(f"    {k:3d}  {t}")


if __name__ == "__main__":
    main()
