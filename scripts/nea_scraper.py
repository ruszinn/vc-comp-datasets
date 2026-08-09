#!/usr/bin/env python3
"""
NEA (New Enterprise Associates) portfolio scraper -> nea_companies.json

nea.com/portfolio is a Next.js (App Router) site whose content is served from a
headless Statamic CMS at statamic.nea.com. The portfolio grid itself is fetched
client-side from a Next.js API route (https://www.nea.com/api/portfolio/companies)
which returns all companies in one JSON payload, but the richer per-company fields
(company website, NEA investment team, board members) are only available via the
underlying Statamic GraphQL endpoint, so this script queries that endpoint directly
(same data, same collection, no LLM/third-party scraping involved -- just NEA's own
public GraphQL API):

    POST https://statamic.nea.com/graphql
    query { entries(collection: "portfolio", limit: 1000) { total data { ... } } }

`limit: 1000` returns the whole "portfolio" collection (903 entries) in a single
page/request -- no pagination needed. Introspection is open on this endpoint, which
is how the field list below (external_url, team, board_members, board_member_observer,
investment_type, company_status/company_status_line_two, theme/additional_theme,
company_category, ...) was discovered.

requirements:
    pip install requests

usage:
    python3 nea_scraper.py                 # full portfolio (one GraphQL request)
    python3 nea_scraper.py --limit 20      # only the first 20 companies (testing)
"""

import json
import os
import re
import sys
import time
from collections import Counter
from datetime import datetime, timezone

import requests

GRAPHQL_URL = "https://statamic.nea.com/graphql"
SOURCE_URL = "https://www.nea.com/portfolio"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "data", "nea_companies.json")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Content-Type": "application/json",
}
TIMEOUT = 60
RETRIES = 4
SLEEP = 1.0

QUERY = """
query PortfolioCompanies($limit: Int!) {
  entries(collection: "portfolio", limit: $limit, sort: ["title"]) {
    total
    data {
      id
      title
      ... on Entry_Portfolio_Portfolio {
        slug
        url
        external_url
        logo { permalink }
        text_logo
        short_description
        description
        first_invested
        company_stage { value label }
        company_status_value { value label }
        company_status
        company_status_line_two
        theme { value label }
        additional_theme { value label }
        investment_type { value label }
        team { title }
        board_members { title }
        board_member_observer { title }
        company_category { title slug }
      }
    }
  }
}
"""

TAG_STOP = re.compile(r"<[^>]+>")


def clean(s):
    if not s:
        return None
    s = TAG_STOP.sub(" ", s)
    s = s.replace("&#039;", "'").replace("&amp;", "&").replace("&quot;", '"').replace("&nbsp;", " ")
    s = re.sub(r"\s+", " ", s).strip()
    return s or None


def fetch_graphql(limit):
    """POST the GraphQL query with retries/backoff. Returns the list of entry dicts."""
    payload = {"query": QUERY, "variables": {"limit": limit}}
    last = None
    for attempt in range(1, RETRIES + 1):
        try:
            r = requests.post(GRAPHQL_URL, headers=HEADERS, json=payload, timeout=TIMEOUT)
            r.raise_for_status()
            data = r.json()
            if "errors" in data and not data.get("data"):
                raise ValueError(data["errors"])
            return data["data"]["entries"]["data"], data["data"]["entries"]["total"]
        except (requests.RequestException, ValueError, KeyError) as e:
            last = e
            wait = SLEEP * attempt * 3
            print(f"  ! GraphQL request failed ({e}); retry {attempt}/{RETRIES} in {wait:.1f}s", file=sys.stderr)
            time.sleep(wait)
    print(f"  !! giving up on GraphQL fetch: {last}", file=sys.stderr)
    return [], 0


# --- everywhere_tags -------------------------------------------------------
# NEA's own "company_category" (Technology/Enterprise/Healthcare/Life Sciences/
# Consumer/Digital Health/Fintech/Biopharma/AI/Energy) and "theme" (AI, SaaS,
# Infrastructure, E-commerce, Digital Media, Robotics/Frontier, Marketplaces,
# Social) fields are the primary signal; keyword classifier is the fallback.
SECTOR_TAG_MAP = {
    "healthcare": ["Health"],
    "digital-health": ["Health", "Data & Analytics"],
    "biopharma": ["BioTech"],
    "life-sciences": ["BioTech"],
    "fintech": ["FinTech / Insurance"],
    "energy": ["Climate / Sustainability"],
    # left to the keyword classifier -- too broad / AI alone isn't a category:
    "technology": [], "enterprise": [], "consumer": [], "artificial-intelligence": [],
}
THEME_TAG_MAP = {
    "infrastructure": ["Dev Tools / Cloud"],
    "saas": ["Dev Tools / Cloud"],
    "e-commerce": ["Consumer"],
    "marketplaces": ["Consumer"],
    "digital-media": ["Gaming / Media / Entertainment"],
    "social": ["Consumer"],
    "robotics-frontier": ["Deeptech / Robotics / AR/VR"],
    "ai": [],
    "default": [],
}

KEYWORD_TAGS = [
    ("BioTech", ["biotech", "drug", "therapeut", "oncolog", "cancer", "tumor", "genomic", "genome",
                 "molecul", "antibod", "protein", "vaccine", "clinical-stage", "biopharma", "life science", "biolog"]),
    ("Health", ["healthcare", "health care", "patient", "clinic", "medical", "mental health", "telehealth",
                "health system", "health record", "diagnos", "surgical", "doctor", "hospital", "pharmac", "therapy"]),
    ("Cybersecurity", ["cybersecurity", "security", "secure", "privacy", "fraud", "phishing", "malware",
                       "ransomware", "endpoint", "zero trust", "vulnerab", "authentication", "threat", "identity"]),
    ("FinTech / Insurance", ["fintech", "payment", "bank", "lending", "loan", "insurance", "credit", "trading",
                             "wallet", "financ", "invoic", "accounting", "payroll", "treasury", "billing",
                             "brokerage", "spend management", "expense"]),
    ("Web3 / Crypto", ["crypto", "blockchain", "web3", "token", "on-chain", "ethereum", "bitcoin", "decentral", "stablecoin", "nft"]),
    ("Gaming / Media / Entertainment", ["game", "gaming", "music", "video", "creator", "content", "publish",
                                        "entertain", "newsletter", "podcast", "film", "streaming", "social media", "media platform"]),
    ("Dev Tools / Cloud", ["developer", " api ", "apis", "api platform", "infrastructure", "database", "cloud",
                           "open source", "devops", "sdk", "kubernetes", "container", "observability", "deploy", "compute",
                           "storage", "serverless", "networking", "software company", "enterprise software"]),
    ("Data & Analytics", ["analytics", "business intelligence", "data platform", "data warehouse", "data lake",
                          "data pipeline", "insights", "dashboard", "machine learning", "predictive", "artificial intelligence"]),
    ("Future of Work", ["workforce", "hiring", "recruit", "employee", "productivity", "collaboration", "talent",
                        "workplace", "human resources", "learning platform", "customer success", "customer service",
                        "customer support", "onboarding", "workflow", "crm"]),
    ("Transportation / Mobility", ["mobility", "vehicle", "transport", "autonomous", "fleet", "driving", "aviation",
                                   "aircraft", "electric vehicle", "scooter", " bike", "rideshar"]),
    ("Logistics / Supply Chain", ["logistics", "supply chain", "freight", "warehouse", "last-mile", "delivery",
                                  "procurement", "inventory", "fulfillment", "shipping"]),
    ("PropTech", ["real estate", "property", "housing", "mortgage", "rental", "construction", "tenant"]),
    ("CPG", ["beverage", "snack", "consumer packaged", "beauty", "cosmetic", "apparel", "grocery", "skincare", "eyewear", "footwear"]),
    ("Climate / Sustainability", ["climate", "carbon", "renewable", "solar", "battery", "sustainab", "emission",
                                  "clean energy", "ev charging", "electrif", "energy management"]),
    ("RegTech/Gov/Legal", ["legal", "compliance", "government", "regulat", "law firm", "attorney", "govtech", "public sector"]),
    ("Deeptech / Robotics / AR/VR", ["robot", "hardware", "semiconductor", "chip", "drone", "aerospace",
                                     "augmented reality", "virtual reality", "satellite", "quantum", "sensor", "iot"]),
    ("Consumer", ["marketplace", "consumer", "shopping", "social network", "community", "ecommerce", "e-commerce",
                  "subscription", "retailer"]),
]


def everywhere_tags(name, description, category_slugs, theme_slug, additional_theme_slugs):
    tags = []
    for slug in category_slugs:
        for t in SECTOR_TAG_MAP.get(slug, []):
            if t not in tags:
                tags.append(t)
    for slug in [theme_slug] + list(additional_theme_slugs):
        for t in THEME_TAG_MAP.get(slug, []):
            if t not in tags:
                tags.append(t)
    if not tags:
        text = f"{name or ''} {description or ''}".lower()
        for tag, kws in KEYWORD_TAGS:
            if any(kw in text for kw in kws) and tag not in tags:
                tags.append(tag)
    return tags[:4]


# --- status / ticker / acquirer derivation ---------------------------------
# NEA denormalizes exit state into two free-text fields rather than the name:
# company_status ("NASDAQ: ACAD", "Acquired by Pure Energies", "NASDAQ: RPRX (old)")
# and company_status_line_two (only used for the ipo-acquired combo case, holding
# the "Acquired by X" half while company_status holds the pre-acquisition ticker).


def parse_status(status_value, status_text, status_line_two, stage_label=None):
    """Return (status, ticker_symbol, acquirer) derived from NEA's structured fields."""
    value = (status_value or {}).get("value")
    label = (status_value or {}).get("label")
    status = label or None
    ticker = None
    acquirer = None

    def ticker_from(text):
        if not text:
            return None
        m = re.search(r"\b([A-Z]{2,10}(?:/[A-Z]{2,10})?):\s*([A-Z][A-Z0-9.]{0,9})\b", text)
        return f"{m.group(1)}: {m.group(2)}" if m else None

    def acquirer_from(text):
        if not text:
            return None
        m = re.search(r"Acquired by (.+)$", text, flags=re.I)
        return clean(m.group(1)) if m else None

    if value == "ipo":
        status = "Public"
        ticker = ticker_from(status_text)
    elif value == "acquired":
        status = "Acquired"
        acquirer = acquirer_from(status_text)
    elif value == "ipo-acquired":
        status = "Public/Acquired"
        ticker = ticker_from(status_text)
        acquirer = acquirer_from(status_line_two) or acquirer_from(status_text)
        if not acquirer and status_line_two and not ticker_from(status_line_two):
            # NEA's "line two" slot in the ipo-acquired combo is consistently used
            # for the post-IPO outcome (acquirer/merger partner) even when it
            # omits the "Acquired by" phrasing, e.g. "Force10 Networks",
            # "Merged with SynOptics" -- take it verbatim rather than drop it.
            acquirer = clean(status_line_two)
    elif value == "private":
        status = "Private"
    else:
        # ~4 "default"/unset records: fall back to parsing whatever free text exists.
        ticker = ticker_from(status_text)
        acquirer = acquirer_from(status_text) or acquirer_from(status_line_two)
        if not acquirer and status_line_two and not ticker_from(status_line_two):
            acquirer = clean(status_line_two)
        if acquirer:
            status = "Acquired"
        elif ticker:
            status = "Public"
        elif status_text:
            status = clean(status_text)
        elif stage_label and "public" in stage_label.lower():
            # e.g. Interleukin Genetics: company_status_value is unset and no
            # ticker/acquirer text exists anywhere, but company_stage says
            # "Public/PIPES" -- the clearest signal NEA itself provides.
            status = "Public"
    return status, ticker, acquirer


def main():
    limit = None
    if "--limit" in sys.argv:
        try:
            limit = int(sys.argv[sys.argv.index("--limit") + 1])
        except (IndexError, ValueError):
            sys.exit("usage: python3 nea_scraper.py [--limit N]")

    fetch_limit = limit if limit else 1000
    print(f"Querying NEA GraphQL ({GRAPHQL_URL}) for portfolio collection (limit={fetch_limit})...")
    rows, total = fetch_graphql(fetch_limit)
    print(f"  fetched {len(rows)} companies (collection total={total})")

    out = []
    scraped_at = datetime.now(timezone.utc).isoformat()
    for r in rows:
        name = clean(r.get("title"))
        description = clean(r.get("description")) or clean(r.get("short_description"))

        stage = (r.get("company_stage") or {}).get("label")
        theme_obj = r.get("theme") or {}
        theme_slug = theme_obj.get("value")
        theme_label = theme_obj.get("label")
        theme_label = None if theme_label == "Unset" else theme_label
        additional_theme = [t.get("label") for t in (r.get("additional_theme") or []) if t.get("label")]
        additional_theme_slugs = [t.get("value") for t in (r.get("additional_theme") or []) if t.get("value")]

        categories = r.get("company_category") or []
        category_names = [c.get("title") for c in categories if c.get("title")]
        category_slugs = [c.get("slug") for c in categories if c.get("slug")]

        status, ticker, acquirer = parse_status(
            r.get("company_status_value"), r.get("company_status"), r.get("company_status_line_two"), stage
        )

        first_invested = r.get("first_invested")
        first_invested_year = None
        if first_invested:
            m = re.search(r"(19|20)\d{2}", first_invested)
            if m:
                first_invested_year = int(m.group(0))

        logo = r.get("logo")
        logo_url = logo.get("permalink") if isinstance(logo, dict) else None

        investment_type = (r.get("investment_type") or {}).get("label")

        out.append({
            "company_name": name,
            "description": description,
            "company_url": r.get("external_url") or None,
            "company_profile_url": f"https://www.nea.com{r['url']}" if r.get("url") else None,
            "logo_url": logo_url,
            "sectors": category_names,
            "theme": theme_label,
            "additional_theme": additional_theme,
            "investment_stage": stage,
            "first_invested_year": first_invested_year,
            "investment_type": investment_type,
            "nea_team": [clean(t.get("title")) for t in (r.get("team") or []) if t.get("title")],
            "board_members": [clean(t.get("title")) for t in (r.get("board_members") or []) if t.get("title")],
            "board_observers": [clean(t.get("title")) for t in (r.get("board_member_observer") or []) if t.get("title")],
            "status": status,
            "ticker_symbol": ticker,
            "acquirer": acquirer,
            "everywhere_tags": everywhere_tags(name, description, category_slugs, theme_slug, additional_theme_slugs),
            "source_url": SOURCE_URL,
            "scraped_at": scraped_at,
        })

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    by_tag = Counter(t for o in out for t in o["everywhere_tags"])
    by_status = Counter(o["status"] or "Unknown" for o in out)
    print(f"\nWrote {len(out)} companies -> {OUT}")
    print("coverage:",
          "description", sum(1 for o in out if o["description"]),
          "| website", sum(1 for o in out if o["company_url"]),
          "| sectors", sum(1 for o in out if o["sectors"]),
          "| theme", sum(1 for o in out if o["theme"]),
          "| nea_team", sum(1 for o in out if o["nea_team"]),
          "| board_members", sum(1 for o in out if o["board_members"]),
          "| ticker", sum(1 for o in out if o["ticker_symbol"]),
          "| acquirer", sum(1 for o in out if o["acquirer"]),
          "| untagged", sum(1 for o in out if not o["everywhere_tags"]))
    print("By status:", dict(by_status))
    print("By everywhere_tag:")
    for t, c in by_tag.most_common():
        print(f"  {c:>4}  {t}")


if __name__ == "__main__":
    main()
