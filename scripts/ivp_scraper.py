#!/usr/bin/env python3
"""
IVP (Institutional Venture Partners) portfolio scraper -> ivp_companies.json

Scrapes IVP's portfolio (https://www.ivp.com/portfolio/) into a JSON file. The
site is a Nuxt.js (Vue) app whose data is a GraphQL-backed payload embedded as
a Nuxt "devalue" reference array at:
    https://www.ivp.com/portfolio/_payload.json
(the same shape served for SSR hydration -- no HTML scraping needed for the
list). Each company also has its own richer detail payload at:
    https://www.ivp.com/portfolio/<slug>/_payload.json
which additionally exposes the external website, founder/CEO name, a longer
description ("body"), social links, and a "moments" timeline string
("Founded YYYY", "Partnered YYYY", "IPO YYYY", "Acquired [by X] YYYY") that
denormalizes exit info IVP does not expose as separate structured fields
(see CLAUDE.md "Empty != absent").

Nuxt's payload format is a flat JSON array where dict/list values reference
other array indices instead of nesting inline (Vue's `devalue` codec). This
script includes a small recursive resolver (`resolve_ref`) to dereference it
into plain Python objects.

requirements:
    pip install requests beautifulsoup4

usage:
    python3 ivp_scraper.py            # writes ../data/ivp_companies.json
    python3 ivp_scraper.py --limit 15 # only the first ~15 companies, for a test run
"""

import json
import os
import re
import sys
import time
from collections import Counter
from datetime import datetime, timezone

import requests

BASE = "https://www.ivp.com"
PORTFOLIO_URL = f"{BASE}/portfolio/"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "data", "ivp_companies.json")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}
TIMEOUT = 45
RETRIES = 3
SLEEP_BETWEEN = 0.4  # politeness delay between per-company detail fetches

# IVP's own 6 portfolio sectors -> the 17-tag everywhere_tags taxonomy.
# "AI" is intentionally NOT mapped (AI alone is not a category -- classify by
# the market it serves; handled by the keyword fallback below). "Apps" and
# "Infrastructure" are too broad for a single tag and are also left to the
# keyword fallback.
SECTOR_TAG_MAP = {
    "Consumer": "Consumer",
    "Health": "Health",
    "Fintech & Crypto": "FinTech / Insurance",
}

# everywhere_tags keyword classifier (substrings, lowercased) -- adapted from
# menlo_scraper.py / rre_scraper.py.
KEYWORD_TAGS = [
    ("BioTech", ["biotech", "drug", "therapeut", "oncolog", "cancer", "tumor", "genomic", "genome",
                 "molecul", "antibod", "protein", "vaccine", "clinical-stage", "life science",
                 "synthetic biology", "biolog"]),
    ("Health", ["healthcare", "health care", "patient", "clinic", "medical", "mental health", "telehealth",
                "health system", "health record", "diagnos", "surgical", "doctor", "hospital", "pharmac",
                "therapy", "wellness", "sleep"]),
    ("Cybersecurity", ["cybersecurity", "security", "secure", "privacy", "fraud", "phishing", "malware",
                       "ransomware", "endpoint", "zero trust", "vulnerab", "authentication", "threat",
                       "identity theft", "identity protection"]),
    ("FinTech / Insurance", ["fintech", "payment", "bank", "lending", "loan", "insurance", "credit", "trading",
                             "wallet", "financ", "invoic", "accounting", "payroll", "treasury", "billing",
                             "investing", "money transfer", "money management", "robo-advisor", "brokerage"]),
    ("Web3 / Crypto", ["crypto", "blockchain", "web3", "token", "on-chain", "ethereum", "bitcoin", "decentral",
                       "stablecoin", "nft"]),
    ("Gaming / Media / Entertainment", ["game", "gaming", "music", "video", "creator", "content", "publish",
                                        "entertain", "newsletter", "podcast", "film", "streaming",
                                        "social media", "media platform", "gif"]),
    ("Dev Tools / Cloud", ["developer", " api ", "apis", "api platform", "infrastructure", "database", "cloud",
                           "open source", "devops", "sdk", "kubernetes", "container", "observability", "deploy",
                           "compute", "storage", "serverless", "networking", "coding", "codebase", "low-code",
                           "no-code", "monitoring platform", "monitoring", "data lake", "endpoint protection",
                           "cloud infrastructure", "automation", "application intelligence", "software platform",
                           "software-defined", "ship products", "network of applications"]),
    ("Data & Analytics", ["analytics", "business intelligence", "data platform", "data warehouse",
                          "data pipeline", "insights", "dashboard", "product analytics", "machine data",
                          "data analysis", "decision-making platform", "internet intelligence"]),
    ("Future of Work", ["workforce", "hiring", "recruit", "employee", "productivity", "collaboration", "talent",
                        "workplace", "human resources", " hr ", "customer success", "customer service",
                        "customer support", "sales", "onboarding", "workflow", "team messaging",
                        "quote-to-cash", "revenue performance", "marketing", "writing", "communication assistant",
                        "time tracking", "professional services"]),
    ("Transportation / Mobility", ["mobility", "vehicle", "transport", "autonomous", "fleet", "driving",
                                   "aviation", "aircraft", "electric vehicle", "scooter", "rideshar", "gps tracking"]),
    ("Logistics / Supply Chain", ["logistics", "supply chain", "freight", "warehouse", "delivery",
                                  "procurement", "inventory", "fulfillment", "shipping"]),
    ("PropTech", ["real estate", "property", "housing", "mortgage", "rental", "construction", "tenant"]),
    ("CPG", ["beverage", "snack", "consumer packaged", "beauty", "cosmetic", "apparel", "grocery", "skincare",
             "baby", "family products", "non-toxic"]),
    ("Climate / Sustainability", ["climate", "carbon", "renewable", "solar", "battery", "sustainab", "emission",
                                  "clean energy", "electrif", "energy"]),
    ("RegTech/Gov/Legal", ["legal", "compliance", "government", "regulat", "law firm", "attorney"]),
    ("Deeptech / Robotics / AR/VR", ["robot", "hardware", "semiconductor", "chip", "drone", "aerospace",
                                     "augmented reality", "virtual reality", "satellite", "quantum", "sensor"]),
    ("Consumer", ["marketplace", "consumer", "shopping", "social network", "community", "ecommerce",
                  "e-commerce", "subscription", "retailer", "neighborhood", "hospitality", "sleep startup",
                  "direct-to-consumer"]),
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
    print(f"  ! giving up on {url}: {last}", file=sys.stderr)
    return None


def clean(s):
    if s is None:
        return None
    s = re.sub(r"\s+", " ", str(s)).strip()
    return s or None


URL_PATH_CHARS = r"A-Za-z0-9_.\-/?=&%~#:+@!*',;"


def clean_url(s):
    """Like clean(), but also guards against a source-data quirk seen on one
    company (Slack): IVP's own Nuxt payload occasionally concatenates a URL
    field directly with unrelated following text with NO separator (e.g.
    "https://twitter.com/slackhqIVP has been great to work with...", where
    the extra text is actually the (unrelated) testimonial-quote field's
    value bleeding in). A real URL's path/query never contains a space, so
    truncate at the first whitespace; then, since the glued-on prose's first
    word (here "IVP") has no space before it either, also strip a trailing
    run of *only* uppercase letters immediately followed by end-of-string or
    another capital letter (an acronym-like artifact a genuine social/site
    handle wouldn't end with) so the corrupted suffix is dropped rather than
    passed through."""
    s = clean(s)
    if not s or not s.startswith("http"):
        return s
    s = s.split(" ", 1)[0]  # URLs never contain a literal space
    m = re.match(rf"(https?://[{URL_PATH_CHARS}]*?[a-z0-9])[A-Z]{{2,}}$", s)
    if m:
        return m.group(1)
    return s


def resolve_ref(data, idx, depth=0):
    """Nuxt payload values are indices into the flat `data` array (Vue
    `devalue` codec) instead of being nested inline. Recursively dereference
    any int found in dict values / list items back into the array to rebuild
    a normal Python object. `['ShallowReactive'|'Reactive'|'Ref', idx]`
    wrapper pairs are unwrapped transparently."""
    if depth > 20:
        return None
    val = data[idx]
    if isinstance(val, dict):
        return {
            k: (resolve_ref(data, v, depth + 1) if isinstance(v, int) and 0 <= v < len(data) else v)
            for k, v in val.items()
        }
    if isinstance(val, list):
        if len(val) == 2 and val and val[0] in ("ShallowReactive", "Reactive", "Ref", "ShallowRef"):
            v = val[1]
            return resolve_ref(data, v, depth + 1) if isinstance(v, int) else v
        return [
            (resolve_ref(data, v, depth + 1) if isinstance(v, int) and 0 <= v < len(data) else v)
            for v in val
        ]
    return val


def load_nuxt_payload(text):
    return json.loads(text)


def get_build_id():
    """Extract the current Nuxt buildId from the portfolio page HTML. Only used
    to log/cache-bust; the _payload.json endpoints work without it too, but
    fetching it once confirms the site is up and gives a clean SOURCE_URL."""
    html = get(PORTFOLIO_URL)
    if not html:
        return None
    m = re.search(r'buildId:"([a-f0-9-]+)"', html)
    return m.group(1) if m else None


def fetch_portfolio_list():
    """Fetch and resolve the portfolio landing page's Nuxt payload, which
    contains ALL companies (list-level fields only: name, slug, headline,
    logo, investmentDate, sectors, IVP team members, status).

    The page embeds TWO separate GraphQL query results as sibling
    `gql:data:<hash>` entries in one top-level dict -- one resolves to a
    "portfolioPage" object (CMS copy: headline, CTA text, SEO fields) and a
    SEPARATE one resolves to `{paginatedItems: {items: [...] , totalCount}}`
    with the actual company list. So rather than assume they share a root,
    scan every top-level dict for whichever one has a "paginatedItems" key."""
    text = get(f"{PORTFOLIO_URL}_payload.json")
    if not text:
        raise SystemExit("FATAL: could not fetch portfolio list payload")
    data = load_nuxt_payload(text)

    paginated = None
    for i, v in enumerate(data):
        if isinstance(v, dict) and "paginatedItems" in v:
            paginated = resolve_ref(data, i)
            break
    if paginated is None:
        raise SystemExit("FATAL: unexpected payload shape (no paginatedItems found)")

    items = paginated.get("paginatedItems", {}).get("items", [])
    return [it for it in items if isinstance(it, dict)]


def fetch_company_detail(slug):
    """Fetch and resolve a single company's richer detail payload
    (website, founder/CEO leadership line, longer body description, social
    links, and the "moments" timeline string)."""
    text = get(f"{BASE}/portfolio/{slug}/_payload.json")
    if not text:
        return None
    try:
        data = load_nuxt_payload(text)
    except json.JSONDecodeError:
        return None
    for v in data:
        if isinstance(v, dict) and "company" in v:
            return resolve_ref(data, v["company"])
    return None


MONTH_YEAR_RE = re.compile(r"(19|20)\d{2}")


def parse_moments(moments):
    """Parse IVP's free-text "moments" timeline, e.g.:
      "Founded 2008\\r\\nPartnered 2013\\r\\nAcquired by Cisco 2017"
      "Founded 2010\\r\\nPartnered 2018\\r\\nIPO 2019"
    into (year_founded, ivp_partnered_year, status_event, acquirer, exit_year).
    status_event is "IPO" or "Acquired" if present, else None (the list-level
    `status` field -- Active/Public/Acquired -- is the primary status source;
    this only supplies the acquirer/exit-year IVP denormalizes here and
    nowhere else -- see CLAUDE.md "Empty != absent")."""
    year_founded = ivp_partnered_year = exit_year = None
    acquirer = None
    status_event = None
    if not moments:
        return year_founded, ivp_partnered_year, status_event, acquirer, exit_year

    for line in re.split(r"[\r\n]+", moments):
        line = line.strip()
        if not line:
            continue
        ym = MONTH_YEAR_RE.search(line)
        year = int(ym.group(0)) if ym else None
        low = line.lower()
        if low.startswith("founded"):
            year_founded = year
        elif low.startswith("partnered"):
            ivp_partnered_year = year
        elif low.startswith("ipo"):
            status_event = "IPO"
            exit_year = year
        elif low.startswith("acquired"):
            status_event = "Acquired"
            exit_year = year
            m = re.match(r"acquired\s+by\s+(.+?)\s*(?:19|20)\d{2}\s*$", line, re.I)
            if m:
                acquirer = clean(m.group(1))
    return year_founded, ivp_partnered_year, status_event, acquirer, exit_year


# Ordered, narrow prose patterns -- tried most- to least-specific so a short,
# unambiguous acquirer name is captured rather than a run-on clause. NOTE:
# deliberately NOT re.I -- these rely on [A-Z] to anchor on a capitalized
# proper noun (the acquirer); a global case-insensitive flag would let [A-Z]
# match lowercase too (e.g. swallowing a leading "and "), so keyword literals
# use inline (?i:...) groups instead.
ACQUIRED_PROSE_PATTERNS = [
    # "...acquired by <Name> in <YYYY>" / "...acquired by <Name>, <YYYY>"
    re.compile(r"(?i:acquired\s+by)\s+([A-Z][\w.&' -]{1,40}?)\s*(?:,)?\s*(?i:in)\s+(\d{4})"),
    # "<Name> recognized ... moving to acquire it in <YYYY>". The acquirer
    # name is a short run of capitalized words immediately before "recognized"
    # -- capture just that run (not any preceding "and"/clause text).
    re.compile(r"([A-Z][a-zA-Z.&'-]*(?:\s+[A-Z][a-zA-Z.&'-]*){0,3})\s+(?i:recognized)\b[^.]{0,150}?"
               r"(?i:mov(?:ed|ing)\s+to\s+acquire\s+it\s+in)\s+(\d{4})"),
    re.compile(r"([A-Z][\w.&' -]{1,40}?)\s+(?i:acquired|bought)\s+(?i:it|them|the\s+company)\s+(?i:in)\s+(\d{4})"),
]


def parse_body_acquisition(body):
    """Fallback for the ~1/18 acquired companies (e.g. SteelBrick) where IVP's
    "moments" timeline omits the acquirer/year but the free-text `body`
    description states it in prose ("...Salesforce recognized the immense
    value of SteelBrick's breakthrough technology early on, moving to
    acquire it in 2015."). See CLAUDE.md "Empty != absent". Returns
    (acquirer, exit_year) or (None, None) if no confident match (deliberately
    conservative -- a miss stays null rather than fabricating/over-capturing)."""
    if not body:
        return None, None
    for pat in ACQUIRED_PROSE_PATTERNS:
        m = pat.search(body)
        if not m:
            continue
        acquirer = clean(m.group(1))
        year = int(m.group(2))
        if acquirer and 1990 <= year <= 2100 and len(acquirer.split()) <= 5:
            return acquirer, year
    return None, None


def everywhere_tags(name, description, sectors):
    """IVP's own sectors first (mapped via SECTOR_TAG_MAP), then keyword
    fallback on name + description. Order most->least relevant, cap at 4."""
    tags = []
    cat_map = {k.lower(): v for k, v in SECTOR_TAG_MAP.items()}
    for sec in sectors:
        mapped = cat_map.get((sec or "").lower())
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

    print("Fetching IVP portfolio list payload...")
    list_items = fetch_portfolio_list()
    print(f"  {len(list_items)} companies in list payload")

    if limit:
        list_items = list_items[:limit]

    scraped_at = datetime.now(timezone.utc).isoformat()
    companies = []

    for i, item in enumerate(list_items, 1):
        name = clean(item.get("name"))
        slug = clean(item.get("slug"))
        if not name or not slug:
            continue

        print(f"[{i}/{len(list_items)}] {name} ({slug})")
        detail = fetch_company_detail(slug)
        time.sleep(SLEEP_BETWEEN)

        headline = clean(item.get("headline"))
        list_status = item.get("status") or {}
        status = clean(list_status.get("name"))

        sectors = [clean(s.get("name")) for s in (item.get("sectors") or []) if s.get("name")]
        ivp_team = [clean(t.get("fullName")) for t in (item.get("teamMembers") or []) if t.get("fullName")]

        investment_date = clean(item.get("investmentDate"))
        first_investment_year = None
        if investment_date and re.match(r"^\d{4}-\d{2}-\d{2}$", investment_date):
            first_investment_year = int(investment_date[:4])

        # Detail-page-only fields (may be None if the detail fetch failed).
        detail = detail or {}
        description = clean(detail.get("body")) or clean(detail.get("introduction")) or headline
        website = clean_url(detail.get("websiteUrl"))
        # IVP's OWN visible label for this field is generic "Leadership" (not
        # "Founder(s)") -- it's usually the founder/CEO (e.g. "Brian Long" for
        # Attentive) but sometimes a later CEO who isn't a founder (e.g. "Dave
        # McJannet" for HashiCorp). Keep the field name/value as-published
        # rather than mislabeling it "founders". It's also a plain string with
        # no reliable delimiter for multiple names (seen as e.g. "David
        # Wadhwani Jyoti Bansal" separated only by a single space) -- kept
        # verbatim rather than guessing a split that could mangle names.
        leadership = clean(detail.get("leadership"))
        linkedin_url = clean_url(detail.get("linkedinLinkUrl"))
        twitter_url = clean_url(detail.get("twittterLinkUrl"))  # sic: IVP's own field name is misspelled
        announcement_url = clean(detail.get("linkUrl"))
        if announcement_url and announcement_url.startswith("/"):
            announcement_url = BASE + announcement_url  # IVP's own CMS returns site-relative paths for its own posts
        announcement_text = clean(detail.get("linkText"))
        moments = detail.get("moments")  # NOT clean() -- parse_moments needs the raw \r\n line breaks

        year_founded, ivp_partnered_year, status_event, acquirer, exit_year = parse_moments(moments)
        if status == "Acquired" and not acquirer:
            body_acquirer, body_exit_year = parse_body_acquisition(detail.get("body"))
            acquirer = acquirer or body_acquirer
            exit_year = exit_year or body_exit_year

        logo = item.get("logo") or {}
        logo_url = clean(logo.get("assetUrl"))

        company_profile_url = f"{PORTFOLIO_URL}{slug}"

        rec = {
            "company_name": name,
            "description": description,
            "company_url": website,
            "company_profile_url": company_profile_url,
            "logo_url": logo_url,
            "sectors": sectors,
            "ivp_team": ivp_team,
            "leadership": leadership,
            "year_founded": year_founded,
            "ivp_first_investment_year": first_investment_year or ivp_partnered_year,
            "status": status,
            "acquirer": acquirer,
            "exit_year": exit_year,
            "social_urls": {k: v for k, v in {"linkedin": linkedin_url, "twitter": twitter_url}.items() if v},
            "announcement_url": announcement_url,
            "announcement_text": announcement_text,
            "everywhere_tags": everywhere_tags(name, description, sectors),
            "source_url": company_profile_url,
            "scraped_at": scraped_at,
        }
        companies.append(rec)

    companies.sort(key=lambda o: o["company_name"].lower())

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(companies, f, ensure_ascii=False, indent=2)

    by_status = Counter(c["status"] for c in companies)
    by_sector = Counter(s for c in companies for s in c["sectors"])
    by_tag = Counter(t for c in companies for t in c["everywhere_tags"])

    print(f"\nWrote {len(companies)} companies -> {OUT}")
    print("By status:", dict(by_status))
    print("With website:", sum(1 for c in companies if c["company_url"]),
          "| with description:", sum(1 for c in companies if c["description"]),
          "| with leadership:", sum(1 for c in companies if c["leadership"]),
          "| with year_founded:", sum(1 for c in companies if c["year_founded"]),
          "| with acquirer:", sum(1 for c in companies if c["acquirer"]),
          "| with exit_year:", sum(1 for c in companies if c["exit_year"]),
          "| with social_urls:", sum(1 for c in companies if c["social_urls"]),
          "| untagged:", sum(1 for c in companies if not c["everywhere_tags"]))
    print("By IVP sector:")
    for t, c in by_sector.most_common():
        print(f"  {c:>4}  {t}")
    print("By everywhere_tag:")
    for t, c in by_tag.most_common():
        print(f"  {c:>4}  {t}")


if __name__ == "__main__":
    main()
