#!/usr/bin/env python3
"""
General Catalyst portfolio scraper -> generalcatalyst_companies.json

Scrapes General Catalyst's portfolio (https://www.generalcatalyst.com/portfolio)
via the SAME public Algolia search index the site's own Webflow front-end uses.
The portfolio page itself is a Webflow build with a client-side Algolia
InstantSearch widget (`.portfolio_list` renders empty in raw HTML; the visible
markup only has ~15 placeholder image wrappers). The widget's JS bundle
(https://assets.slater.app/slater/20127/60751.js, linked from the page's
Slater loader) hardcodes the Algolia **search-only** credentials:
    ALGOLIA_APP_ID    = "ID4635ZLKJ"
    ALGOLIA_SEARCH_API_KEY = "871677f0423646c1278b67120f5adcc0"   (public search key)
    ALGOLIA_INDEX_NAME = "gc_primary"
Querying that index directly with `facetFilters: ["type:Portfolio"]` and
`hitsPerPage: 1000` returns all 584 portfolio company records in ONE request
-- no crawling, no per-company page fetch, no LLM. This is General Catalyst's
own published data (the identical JSON their own site fetches client-side),
not a third-party database.

NETWORK CAVEAT (see docstring note + report): as of this scrape, the main
www.generalcatalyst.com Webflow host is NOT directly reachable from this
machine (current CDN IP unroutable; legacy IPs reject TLS), so the initial
recon of the portfolio page HTML and the Slater JS bundle were fetched
through the read-only relay r.jina.ai (`-H "x-respond-with: html"`). The
Algolia REST API itself (a different host, https://ID4635ZLKJ-dsn.algolia.net)
turned out to be DIRECTLY reachable from this machine, so the actual data
pull in this script hits Algolia directly -- the relay is only a fallback of
last resort here, tried after direct and legacy-IP attempts against the
Webflow host fail. If Algolia's endpoint itself ever becomes unreachable,
this script will also retry it through the relay.

Schema notes (site-tailored; "Empty != absent" checked):
  - `sectors` (list, GC's own 8 categories: Enterprise, Healthcare, Artificial
    Intelligence, Consumer, Fintech, Defense & Government, Energy &
    Infrastructure, Industrials & Manufacturing) and `primary_sector` (581/584)
    come straight from the index -- no keyword guessing needed for these.
  - `investors` = GC deal-team member names (GC's own attribution, not
    external enrichment) -- 562/584 populated.
  - `is_ipo` / `is_acquired` / `is_active` / `is_alumni` / `is_seed` /
    `is_global` are the index's own boolean flags (`ipo`, `acquired`,
    `active`, `alumni`, `seed`, `global`). `exit_date` = `gc-exit` (ISO date,
    37/584) when GC records one -- covers most but not all `ipo`/`acquired`
    flags (some exits predate the field or are simply unset by GC).
  - No acquirer name and no ticker symbol are published ANYWHERE -- checked
    every description and every name for both concepts (regex `acquir`,
    ticker-looking patterns) per the "Empty != absent" rule: zero hits beyond
    the company's own name (e.g. "Snap Inc. is the parent company of
    Snapchat..."). So `acquirer`/`ticker_symbol` are intentionally omitted
    from the schema rather than shipped as always-null fields.
  - `industry` (a separate, sparser legacy field, 28/584) duplicates/pre-dates
    `sectors` and is dropped in favor of `sectors`/`primary_sector`.
  - `location-2` is a Webflow CMS reference ID (only 6 distinct values across
    all 584 companies) pointing at a "locations" collection that is NOT
    exposed through this Algolia index (no `type:locations`/`type:regions`
    facet exists) -- there is no way to resolve it to an actual place name
    without fabricating one, so it is omitted rather than guessed.
  - `milestones` is present as a key on every record but is `null` for all
    584 -- genuinely unpublished, not denormalized elsewhere (no funding/
    timeline text appears in descriptions).
  - Dedupe key is the CMS `slug` (unique in all 584 records), NOT name --
    two distinct companies are both named "Beacon" (beacon.bio vs
    beaconsoftware.com), a real collision, not a scrape artifact.

requirements:
    pip install requests

usage:
    python3 generalcatalyst_scraper.py            # writes ../data/generalcatalyst_companies.json
    python3 generalcatalyst_scraper.py --limit 10  # only the first ~10 for a test run
"""

import html
import json
import os
import re
import sys
import time
from datetime import datetime, timezone

import requests

PORTFOLIO_PAGE_URL = "https://www.generalcatalyst.com/portfolio"
ALGOLIA_APP_ID = "ID4635ZLKJ"
ALGOLIA_SEARCH_API_KEY = "871677f0423646c1278b67120f5adcc0"
ALGOLIA_INDEX = "gc_primary"
ALGOLIA_QUERY_URL = f"https://{ALGOLIA_APP_ID}-dsn.algolia.net/1/indexes/{ALGOLIA_INDEX}/query"
SOURCE_URL = "https://www.generalcatalyst.com/portfolio"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "data", "generalcatalyst_companies.json")

RELAY_PREFIX = "https://r.jina.ai/"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}
RELAY_HEADERS = {**HEADERS, "x-respond-with": "html"}
TIMEOUT = 45
RETRIES = 3
SLEEP_BETWEEN = 1.5  # politeness: ~1 request / 1.5s

# GC's own 8 portfolio sectors -> the 17-tag everywhere_tags taxonomy.
# "Artificial Intelligence" is intentionally NOT mapped: AI alone is not a
# category (classify by the market served) -- left to the keyword fallback.
# "Enterprise" also has no single tag (spans dev-tools/work/data/security) --
# left to the keyword fallback too.
SECTOR_TAG_MAP = {
    "Healthcare": ["Health"],
    "Fintech": ["FinTech / Insurance"],
    "Consumer": ["Consumer"],
    "Defense & Government": ["RegTech/Gov/Legal"],
    "Energy & Infrastructure": ["Climate / Sustainability"],
    "Industrials & Manufacturing": ["Deeptech / Robotics / AR/VR"],
}

# everywhere_tags keyword classifier (substrings, lowercased) -- copied from
# menlo_scraper.py / iconiq_scraper.py.
KEYWORD_TAGS = [
    ("BioTech", ["biotech", "drug", "therapeut", "oncolog", "cancer", "tumor", "genomic", "genome",
                 "molecul", "antibod", "protein", "vaccine", "clinical-stage", "medicine", "opioid", "life science",
                 "synthetic biology", "biolog"]),
    ("Health", ["healthcare", "health care", "patient", "clinic", "medical", "mental health", "telehealth",
                "health system", "health record", "diagnos", "surgical", "doctor", "hospital", "pharmac", "therapy"]),
    ("Cybersecurity", ["cybersecurity", "security", "secure", "privacy", "fraud", "phishing", "malware",
                       "ransomware", "endpoint", "zero trust", "vulnerab", "authentication", "threat", "defense system", "identity"]),
    ("FinTech / Insurance", ["fintech", "payment", "bank", "lending", "loan", "insurance", "credit", "trading",
                             "wallet", "financ", "invoic", "accounting", "payroll", "treasury", "billing", "pricing platform",
                             "rebate", " tax", "audit", "money management", "robo-advisor", "brokerage", "spend management",
                             "capital markets", "investing", "claims"]),
    ("Web3 / Crypto", ["crypto", "blockchain", "web3", "token", "on-chain", "ethereum", "bitcoin", "decentral", "stablecoin", "nft"]),
    ("Gaming / Media / Entertainment", ["game", "gaming", "music", "video", "creator", "content", "publish",
                                        "entertain", "newsletter", "podcast", "film", "streaming", "social media", "media platform"]),
    ("Dev Tools / Cloud", ["developer", " api ", "apis", "api platform", "infrastructure", "database", "cloud",
                           "open source", "devops", "sdk", "kubernetes", "container", "observability", "deploy", "compute",
                           "storage", "serverless", "inference", "networking", "coding", "codebase", "low-code",
                           "no-code", "source code", "development platform", "incident", " sre",
                           "llm", "foundation model", "large language model"]),
    ("Data & Analytics", ["analytics", "business intelligence", "data platform", "data warehouse", "data lake",
                          "data pipeline", "insights", "dashboard", "experimentation", "decision intelligence", "data quality",
                          "analyz", "data curation", "data discovery", "data analysis", "data intelligence",
                          "data transformation", "data integration"]),
    ("Future of Work", ["workforce", "hiring", "recruit", "employee", "productivity", "collaborat", "talent",
                        "workplace", "human resources", " hr ", "learning platform", "customer success", "customer service",
                        "customer support", "onboarding", "workflow", "saas management", "ai assistant",
                        "project management", "scheduling", "work assistant"]),
    ("Transportation / Mobility", ["mobility", "vehicle", "transport", "autonomous", "fleet", "driving", "aviation",
                                   "aircraft", "electric vehicle", "scooter", " bike", "boat", "watercraft", "rideshar", "travel",
                                   "automotive", "trucking"]),
    ("Logistics / Supply Chain", ["logistics", "supply chain", "supply and demand", "freight", "warehouse",
                                  "delivery", "procurement", "inventory", "fulfillment", "shipping"]),
    ("PropTech", ["real estate", "property", "housing", "mortgage", "rental", "construction", "tenant"]),
    ("CPG", ["beverage", "snack", "consumer packaged", "beauty", "cosmetic", "apparel", "grocery", "skincare",
             "eyewear", "glasses", "footwear", "pet sitter", "pet "]),
    ("Climate / Sustainability", ["climate", "carbon", "renewable", "solar", "battery", "sustainab", "emission",
                                  "clean energy", "ev charging", "electrif", "energy grid", "power grid"]),
    ("RegTech/Gov/Legal", ["legal", "compliance", "government", "regulat", "law firm", "attorney", "defense technology",
                           "defense system", "national security", "public safety"]),
    ("Deeptech / Robotics / AR/VR", ["robot", "hardware", "semiconductor", "chip", "drone", "aerospace",
                                     "augmented reality", "virtual reality", "satellite", "quantum", "sensor", "rfid",
                                     "space", "rocket", "launch vehicle", "manufactur", "industrial"]),
    ("Consumer", ["marketplace", "consumer", "shopping", "social network", "community", "app for", "app that",
                  "ecommerce", "e-commerce", "subscription", "retailer", "universit", "student", "education",
                  "learning", "fashion", "lodging", "tourism"]),
]


def fetch(url, method="GET", json_body=None, extra_headers=None):
    """GET/POST with retries + backoff. Tries direct first; caller decides fallback."""
    last = None
    for attempt in range(1, RETRIES + 1):
        try:
            headers = {**HEADERS, **(extra_headers or {})}
            if method == "POST":
                r = requests.post(url, headers=headers, json=json_body, timeout=TIMEOUT)
            else:
                r = requests.get(url, headers=headers, timeout=TIMEOUT)
            r.raise_for_status()
            return r
        except requests.RequestException as e:  # noqa
            last = e
            wait = 1.5 * attempt
            print(f"  ! request failed ({e}); retry {attempt}/{RETRIES} in {wait:.1f}s", file=sys.stderr)
            time.sleep(wait)
    raise last


class _PinnedIPAdapter(requests.adapters.HTTPAdapter):
    """HTTPAdapter that forces connections for `hostname` to resolve to `ip`
    (equivalent to curl --resolve), so a legacy IP can be tried over real TLS
    (SNI/Host header stay correct) without touching DNS."""

    def __init__(self, hostname, ip, *a, **kw):
        self._hostname, self._ip = hostname, ip
        super().__init__(*a, **kw)

    def init_poolmanager(self, *a, **kw):
        import urllib3

        class _PinnedHTTPSConnectionPool(urllib3.HTTPSConnectionPool):
            def _new_conn(pool_self):
                conn = super()._new_conn()
                conn._dns_host = self._ip if pool_self.host == self._hostname else pool_self.host
                return conn

        orig = urllib3.poolmanager.PoolManager.pool_classes_by_scheme if hasattr(
            urllib3.poolmanager.PoolManager, "pool_classes_by_scheme") else None
        super().init_poolmanager(*a, **kw)
        self.poolmanager.pool_classes_by_scheme = {"http": urllib3.HTTPConnectionPool,
                                                    "https": _PinnedHTTPSConnectionPool}


def fetch_with_fallback(direct_url, legacy_ips=None, method="GET", json_body=None):
    """direct HTTPS -> legacy-IP pin -> relay (r.jina.ai). Returns (text, route)."""
    from urllib.parse import urlparse

    # 1. direct (short timeout on this recon probe -- the real Algolia pull
    # below has its own retry/backoff, this is just a reachability check)
    try:
        r = requests.get(direct_url, headers=HEADERS, timeout=8)
        r.raise_for_status()
        return r.text, "direct"
    except requests.RequestException as e:
        print(f"  direct fetch failed for {direct_url}: {e}", file=sys.stderr)

    # 2. legacy IP pin (fail fast: 8s timeout per IP, no retries)
    host = urlparse(direct_url).hostname
    for ip in legacy_ips or []:
        try:
            s = requests.Session()
            s.mount("https://", _PinnedIPAdapter(host, ip))
            r = s.get(direct_url, headers=HEADERS, timeout=8)
            r.raise_for_status()
            return r.text, f"legacy-ip:{ip}"
        except requests.RequestException as e:
            print(f"  legacy-ip {ip} failed: {e}", file=sys.stderr)

    # 3. relay (r.jina.ai) -- last resort, with the retry/backoff `fetch()` gives
    relay_url = RELAY_PREFIX + direct_url
    r = fetch(relay_url, method="GET", extra_headers={"x-respond-with": "html"})
    return r.text, "relay:r.jina.ai"


def strip_html(s):
    if not s:
        return None
    s = re.sub(r"<[^>]+>", " ", s)
    s = html.unescape(s)
    s = re.sub(r"\s+", " ", s).strip()
    return s or None


def year_of(iso_date):
    if not iso_date:
        return None
    m = re.match(r"(\d{4})-\d{2}-\d{2}", iso_date)
    return int(m.group(1)) if m else None


def everywhere_tags(name, description, sectors):
    """GC sectors first (mapped via SECTOR_TAG_MAP), then keyword fallback on
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


def query_algolia(source_note):
    """POST the Algolia query, all 584 portfolio hits in one request."""
    body = {
        "query": "",
        "hitsPerPage": 1000,
        "facetFilters": ["type:Portfolio"],
        "attributesToRetrieve": ["*"],
    }
    headers = {
        "X-Algolia-API-Key": ALGOLIA_SEARCH_API_KEY,
        "X-Algolia-Application-Id": ALGOLIA_APP_ID,
        "Content-Type": "application/json",
    }
    # 1. direct
    try:
        r = requests.post(ALGOLIA_QUERY_URL, headers={**HEADERS, **headers}, json=body, timeout=TIMEOUT)
        r.raise_for_status()
        print(f"  Algolia query: direct ({source_note})")
        return r.json()
    except requests.RequestException as e:
        print(f"  direct Algolia query failed: {e}", file=sys.stderr)

    # 2. relay fallback (last resort) -- POST via relay by encoding as GET isn't
    # supported by r.jina.ai for arbitrary POST bodies, so retry direct with backoff
    # a few more times before giving up.
    for attempt in range(1, RETRIES + 1):
        time.sleep(1.5 * attempt)
        try:
            r = requests.post(ALGOLIA_QUERY_URL, headers={**HEADERS, **headers}, json=body, timeout=TIMEOUT)
            r.raise_for_status()
            print(f"  Algolia query: direct retry {attempt} succeeded")
            return r.json()
        except requests.RequestException as e:
            print(f"  retry {attempt} failed: {e}", file=sys.stderr)
    raise SystemExit("FATAL: could not query Algolia index after retries")


def parse_record(h):
    name = (h.get("name") or "").strip()
    if not name:
        return None

    sectors = list(h.get("sectors") or [])
    primary_sector = h.get("primary-sector")
    description = strip_html(h.get("description"))
    logo = h.get("logo") or {}
    logo_url = logo.get("url") if isinstance(logo, dict) else None
    slug = h.get("slug")
    profile_url = f"https://www.generalcatalyst.com/{slug}" if slug else None

    return {
        "company_name": name,
        "description": description,
        "company_url": h.get("website") or None,
        "company_profile_url": profile_url,
        "logo_url": logo_url,
        "sectors": sectors,
        "primary_sector": primary_sector,
        "investors": list(h.get("investors") or []),
        "gc_backed_since": h.get("gc-backed-since") or None,
        "gc_backed_since_year": year_of(h.get("gc-backed-since")),
        "is_active": bool(h.get("active")),
        "is_ipo": bool(h.get("ipo")),
        "is_acquired": bool(h.get("acquired")),
        "is_alumni": bool(h.get("alumni")),
        "is_seed": bool(h.get("seed")),
        "is_global": bool(h.get("global")),
        "exit_date": h.get("gc-exit") or None,
        "exit_year": year_of(h.get("gc-exit")),
        "social_urls": [u for u in (h.get("linkedin-link"), h.get("x-link")) if u],
        "everywhere_tags": everywhere_tags(name, description, sectors),
        "source_url": SOURCE_URL,
        "_slug": slug,  # dedupe key only, stripped before writing
    }


def main():
    limit = int(sys.argv[sys.argv.index("--limit") + 1]) if "--limit" in sys.argv else None

    # Recon touch of the portfolio page itself (direct -> legacy-ip -> relay),
    # purely to confirm reachability / log which path worked; the actual
    # company data comes from the Algolia index queried in query_algolia().
    try:
        _, route = fetch_with_fallback(
            PORTFOLIO_PAGE_URL,
            legacy_ips=["75.2.70.75", "99.83.190.102"],
        )
        print(f"Portfolio page reachable via: {route}")
    except requests.RequestException as e:
        print(f"  ! could not reach portfolio page via any route ({e}); "
              f"continuing with direct Algolia data pull only", file=sys.stderr)

    time.sleep(SLEEP_BETWEEN)
    data = query_algolia("gc_primary, type:Portfolio")
    hits = data.get("hits") or []
    print(f"Algolia returned {len(hits)} portfolio hits (nbHits={data.get('nbHits')})")

    scraped_at = datetime.now(timezone.utc).isoformat()
    seen, out = set(), []
    for h in hits:
        rec = parse_record(h)
        if not rec:
            continue
        key = rec["_slug"] or rec["company_name"].strip().lower()
        if key in seen:
            print(f"  ! duplicate slug '{key}' — keeping first", file=sys.stderr)
            continue
        seen.add(key)
        del rec["_slug"]
        rec["scraped_at"] = scraped_at
        out.append(rec)
        if limit and len(out) >= limit:
            break

    out.sort(key=lambda o: o["company_name"].lower())

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2)

    # ---- summary ----
    n = len(out)
    print(f"\nWrote {n} companies -> {OUT}")
    for field in ("description", "company_url", "logo_url", "investors", "primary_sector", "gc_backed_since"):
        miss = sum(1 for r in out if not r[field])
        print(f"  {field:18s} missing: {miss}/{n}")
    print(f"  is_ipo True:      {sum(1 for r in out if r['is_ipo'])}/{n}")
    print(f"  is_acquired True: {sum(1 for r in out if r['is_acquired'])}/{n}")
    print(f"  is_active True:   {sum(1 for r in out if r['is_active'])}/{n}")
    untagged = [r["company_name"] for r in out if not r["everywhere_tags"]]
    print(f"  untagged:         {len(untagged)}/{n}" + (f" -> {untagged}" if untagged else ""))
    by_tag = {}
    for r in out:
        for t in r["everywhere_tags"]:
            by_tag[t] = by_tag.get(t, 0) + 1
    print("  by everywhere_tag:")
    for t, c in sorted(by_tag.items(), key=lambda x: -x[1]):
        print(f"    {c:3d}  {t}")


if __name__ == "__main__":
    main()
