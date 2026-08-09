#!/usr/bin/env python3
"""
Parkway Venture Capital portfolio scraper -> parkway_companies.json

Scrapes Parkway VC's portfolio (https://www.parkway.vc/portfolio) into a JSON
file. The site is Webflow + Finsweet CMS filters; the whole portfolio grid
(25 companies) is server-rendered into ONE page -- no pagination, no API key.
Each grid item (`.portfolio_main_item`) carries Parkway's own `industry` and
`stage` filter fields, an external website link (when Parkway has one on
file), and occasionally a short description. A minority of companies also
have a richer per-company "case study" detail page at
`/portfolio/<slug>` (e.g. /portfolio/figure, /portfolio/x-ai) with a founded
year, Parkway's own deal-team members ("Team" -- NOT the company's founders),
and a longer description; this scraper derives the slug deterministically
(`slugify(name)`) and only keeps the detail-page data if the returned page's
<title> matches the company name (prevents wrong-page fabrication) -- a 404
just means no detail page exists.

NETWORK WORKAROUND: this environment cannot route to Webflow's current CDN IP
for www.parkway.vc (-> cdn.webflow.com -> 198.202.211.1 times out over TCP).
The site is reachable on Webflow's legacy AWS Global Accelerator IPs
(75.2.70.75 / 99.83.190.102), which still terminate TLS for the real
hostname, so SNI/cert validation is unaffected -- only the IP lookup changes.
`fetch()` tries normal DNS/routing first (short timeout) and, only on a
connection failure, monkeypatches `socket.getaddrinfo` for this hostname to
return the pinned IP directly, then retries. This keeps the script portable
(no-op on a network where normal routing works).

requirements:
    pip install requests beautifulsoup4

usage:
    python3 parkway_scraper.py            # writes ../data/parkway_companies.json
    python3 parkway_scraper.py --limit 5  # only the first 5 grid entries (+ their detail pages)
"""

import json
import os
import re
import socket
import sys
import time
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

HOST = "www.parkway.vc"
PORTFOLIO_URL = f"https://{HOST}/portfolio"
DETAIL_URL = f"https://{HOST}/portfolio/{{slug}}"
SOURCE_URL = PORTFOLIO_URL
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "data", "parkway_companies.json")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}
TIMEOUT = 45
RETRIES = 3

# --- network workaround: pin www.parkway.vc to a reachable legacy Webflow IP
# only if normal routing fails (see module docstring). ---
PINNED_IPS = ["75.2.70.75", "99.83.190.102"]
_real_getaddrinfo = socket.getaddrinfo
_pinned = False


def _pinned_getaddrinfo(host, port, *args, **kwargs):
    if host == HOST:
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (PINNED_IPS[0], port))]
    return _real_getaddrinfo(host, port, *args, **kwargs)


def _enable_pin():
    global _pinned
    if not _pinned:
        socket.getaddrinfo = _pinned_getaddrinfo
        _pinned = True
        print(f"  ! normal routing to {HOST} failed; pinned to {PINNED_IPS[0]} "
              "(local network can't reach Webflow's current CDN IP)", file=sys.stderr)


# Parkway's own `industry` filter values -> the 17-tag everywhere_tags taxonomy.
# "A.I." and "Software" are intentionally NOT mapped (AI alone is not a
# category, and "Software" is too generic to mean any one of the 17) --
# classify those by the market served via the keyword fallback on name +
# description. "Complex Engineering" / "Simulation" / "Generative Design" /
# "Quantum Tech" describe Parkway's hardware/deeptech-heavy engineering bets
# (drones, fusion, holographic displays, CAD/manufacturing simulation) and map
# to Deeptech / Robotics / AR/VR; "Ubiquitous Data" maps to Data & Analytics.
SECTOR_TAG_MAP = {
    "Healthcare": ["Health"],
    "FinTech": ["FinTech / Insurance"],
    "Financial": ["FinTech / Insurance"],
    "Real Estate": ["PropTech"],
    "Transportation": ["Transportation / Mobility"],
    "Consumer": ["Consumer"],
    "Complex Engineering": ["Deeptech / Robotics / AR/VR"],
    "Simulation": ["Deeptech / Robotics / AR/VR"],
    "Generative Design": ["Deeptech / Robotics / AR/VR"],
    "Quantum Tech": ["Deeptech / Robotics / AR/VR"],
    "Ubiquitous Data": ["Data & Analytics"],
}

# everywhere_tags keyword classifier (substrings, lowercased) -- copied from
# menlo_scraper.py / rre_scraper.py / foundersfund_scraper.py.
KEYWORD_TAGS = [
    ("BioTech", ["biotech", "drug", "therapeut", "oncolog", "cancer", "tumor", "genomic", "genome",
                 "molecul", "antibod", "protein", "vaccine", "clinical-stage", "medicine", "opioid", "life science",
                 "synthetic biology", "biolog"]),
    ("Health", ["healthcare", "health care", "patient", "clinic", "medical", "mental health", "telehealth",
                "health system", "health record", "diagnos", "surgical", "doctor", "hospital", "pharmac", "therapy",
                "radiology"]),
    ("Cybersecurity", ["cybersecurity", "security", "secure", "privacy", "fraud", "phishing", "malware",
                       "ransomware", "endpoint", "zero trust", "vulnerab", "authentication", "threat", "defense system", "identity",
                       "information protection", "encryption", "cryptography"]),
    ("FinTech / Insurance", ["fintech", "payment", "bank", "lending", "loan", "insurance", "credit", "trading",
                             "wallet", "financ", "invoic", "accounting", "payroll", "treasury", "billing", "pricing platform",
                             "rebate", " tax", "audit", "money management", "robo-advisor", "brokerage", "spend management",
                             "capital markets"]),
    ("Web3 / Crypto", ["crypto", "blockchain", "web3", "token", "on-chain", "ethereum", "bitcoin", "decentral", "stablecoin", "nft"]),
    ("Gaming / Media / Entertainment", ["game", "gaming", "music", "video", "creator", "content", "publish",
                                        "entertain", "newsletter", "podcast", "film", "streaming", "social media", "media platform"]),
    ("Dev Tools / Cloud", ["developer", " api ", "apis", "api platform", "infrastructure", "database", "cloud",
                           "open source", "devops", "sdk", "kubernetes", "container", "observability", "deploy", "compute",
                           "storage", "serverless", "inference", "networking", "coding", "codebase", "low-code",
                           "no-code", "source code", "development platform", "llm", "foundation model",
                           "large language model", "context window"]),
    ("Data & Analytics", ["analytics", "business intelligence", "data platform", "data warehouse", "data lake",
                          "data pipeline", "insights", "dashboard", "experimentation", "decision intelligence", "data quality",
                          "analyz", "data curation", "quality management", "ubiquitous data", "carbon accounting",
                          "emissions data", "sensing"]),
    ("Future of Work", ["workforce", "hiring", "recruit", "employee", "productivity", "collaboration", "talent",
                        "workplace", "human resources", " hr ", "learning platform", "customer success", "customer service",
                        "customer support", "workflow"]),
    ("Transportation / Mobility", ["mobility", "vehicle", "transport", "autonomous", "fleet", "driving", "aviation",
                                   "aircraft", "electric vehicle", "scooter", " bike", "boat", "watercraft", "rideshar",
                                   "ride sharing", "ride-sharing"]),
    ("Logistics / Supply Chain", ["logistics", "supply chain", "supply and demand", "freight", "warehouse",
                                  "delivery", "procurement", "inventory", "fulfillment", "shipping"]),
    ("PropTech", ["real estate", "property", "housing", "mortgage", "rental", "construction", "tenant"]),
    ("CPG", ["beverage", "snack", "consumer packaged", "beauty", "cosmetic", "apparel", "grocery", "skincare",
             "eyewear", "glasses", "footwear"]),
    ("Climate / Sustainability", ["climate", "carbon", "renewable", "solar", "battery", "sustainab", "emission",
                                  "clean energy", "ev charging", "electrif"]),
    ("RegTech/Gov/Legal", ["legal", "compliance", "government", "regulat", "law firm", "attorney"]),
    ("Deeptech / Robotics / AR/VR", ["robot", "hardware", "semiconductor", "chip", "drone", "aerospace",
                                     "augmented reality", "virtual reality", "satellite", "quantum", "sensor", "rfid", "wifi",
                                     "humanoid", "complex engineering", "simulation software", "generative design"]),
    ("Consumer", ["marketplace", "consumer", "shopping", "social network", "community", "app for", "app that",
                  "ecommerce", "e-commerce", "subscription", "retailer"]),
]


def fetch(url):
    """GET url with retries/backoff; try normal routing first, fall back to a
    pinned IP for www.parkway.vc only on a connection failure (see docstring)."""
    last = None
    for attempt in range(1, RETRIES + 1):
        try:
            r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
            r.raise_for_status()
            return r
        except requests.exceptions.HTTPError as e:
            return e.response  # let caller inspect status_code (e.g. 404 on detail pages)
        except requests.RequestException as e:  # noqa
            last = e
            if not _pinned:
                _enable_pin()
                continue  # retry immediately on the pinned IP, don't burn a backoff slot
            wait = 1.5 * attempt
            print(f"  ! request failed ({e}); retry {attempt}/{RETRIES} in {wait:.1f}s", file=sys.stderr)
            time.sleep(wait)
    raise SystemExit(f"FATAL: could not fetch {url}: {last}")


def clean(s):
    if s is None:
        return None
    s = re.sub(r"\s+", " ", s).strip()
    return s or None


def slugify(name):
    s = name.lower().strip()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")


def looks_like_domain(s):
    """True for bare-domain strings like 'lyft.com' / 'figure.ai' -- these are
    fine as a last resort but a real display name (from the logo file or a
    clean share-link label) is preferred when available."""
    return bool(re.match(r"^[a-z0-9-]+\.(com|ai|io|co|org|net)$", s.strip(), re.I))


def name_from_logo(src):
    """Fallback name derivation from the logo filename, used only when neither
    the external-site link text nor a detail-page <title> gives a clean name.
    Handles the common 'Logo - <Name>.svg' convention seen on ~22/25 items."""
    if not src:
        return None
    fname = src.rsplit("/", 1)[-1]
    fname = re.sub(r"%20", " ", fname)
    fname = re.sub(r"\.(svg|png|jpe?g|webp)$", "", fname, flags=re.I)
    fname = re.sub(r"^([0-9a-f]{20,}_)+", "", fname, flags=re.I)
    m = re.match(r"^Logo\s*-\s*(.+)$", fname, re.I)
    if m:
        return clean(m.group(1))
    m = re.match(r"^([A-Za-z0-9.]+)[_-](Logo|Icon)\b", fname, re.I)
    if m:
        return clean(m.group(1).replace("_", " "))
    if fname.strip().lower() in ("images", "image", "logo", "icon"):
        return None
    return None


def everywhere_tags(name, description, sectors):
    """Parkway's own industry tag(s) first (mapped via SECTOR_TAG_MAP), then a
    keyword fallback on name + description. Order most->least relevant, cap 4."""
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


def parse_grid(html):
    soup = BeautifulSoup(html, "html.parser")
    items = soup.select(".portfolio_main_item")
    rows = []
    for it in items:
        logo_el = it.select_one("img.portfolio_accordion_logo")
        logo_url = logo_el.get("src") if logo_el else None

        industry_el = it.select_one('p[fs-cmsfilter-field="industry"]')
        industry = clean(industry_el.get_text()) if industry_el else None

        stage_el = it.select_one('p[fs-cmsfilter-field="stage"]')
        stage = clean(stage_el.get_text()) if stage_el else None

        desc_el = it.select_one(".portfolio_accordion_description")
        description = clean(desc_el.get_text()) if desc_el else None

        link_el = it.select_one("a.share_link[href]")
        href = link_el.get("href") if link_el else None
        company_url = href if href and href != "#" else None

        share_text_el = it.select_one(".share_link_text")
        share_text = clean(share_text_el.get_text()) if share_text_el else None

        # Prefer a clean display name: the share-link label when it isn't a
        # bare domain (e.g. "Persefoni", "Sandbox AQ"), else the logo filename
        # (e.g. "Figure" when the share label is just "figure.ai"), else fall
        # back to the domain-like share label itself (e.g. "x.ai", the
        # company's actual stylized name) rather than leaving it null.
        logo_name = name_from_logo(logo_url)
        if share_text and not looks_like_domain(share_text):
            name = share_text
        elif logo_name:
            name = logo_name
        else:
            name = share_text

        rows.append({
            "name": name,
            "logo_url": logo_url,
            "industry": industry,
            "stage": stage,
            "description": description,
            "company_url": company_url,
        })
    return rows


def fetch_detail(name):
    """Try the deterministic /portfolio/<slug> case-study page. Returns a dict
    of extra fields or None if there's no detail page (404) or the returned
    page's <title> doesn't match the company name (safety gate against a wrong
    match -- never fabricate by guessing further slug variants)."""
    slug = slugify(name)
    if not slug:
        return None
    r = fetch(DETAIL_URL.format(slug=slug))
    if r.status_code == 404:
        return None
    if r.status_code != 200:
        print(f"  ! unexpected status {r.status_code} for detail page of '{name}'", file=sys.stderr)
        return None

    soup = BeautifulSoup(r.text, "html.parser")
    title = soup.title.get_text(strip=True) if soup.title else ""
    title_name = re.sub(r"\s*-\s*Parkway VC\s*$", "", title, flags=re.I).strip()
    n, t = name.lower().strip(), title_name.lower().strip()
    # Confident-match gate: exact, or one is a prefix of the other (handles
    # Parkway truncating "Sandbox AQ" -> title "Sandbox"). Anything else is
    # treated as "not this company" and skipped -- never fabricate by trying
    # further slug guesses.
    if not t or not (n == t or n.startswith(t) or t.startswith(n)):
        return None

    main = soup.select_one("main")
    if main is None:
        return None

    tagline_el = main.select_one("h1.case_header_title")
    tagline = None
    if tagline_el and "w-dyn-bind-empty" not in (tagline_el.get("class") or []):
        tagline = clean(tagline_el.get_text())

    year_el = main.select_one(".case_detail_year_wrap .case_detail_year")
    founded_year = None
    if year_el and "w-dyn-bind-empty" not in (year_el.get("class") or []):
        yr = clean(year_el.get_text())
        founded_year = int(yr) if yr and yr.isdigit() else None

    # "Team" here is Parkway's own deal-team members involved with this
    # investment -- NOT the company's founders (Parkway doesn't publish those).
    team = []
    for t in main.select(".case_team_item"):
        nm = t.select_one(".case_team_name")
        ti = t.select_one(".case_team_title")
        nm = clean(nm.get_text()) if nm else None
        ti = clean(ti.get_text()) if ti else None
        if nm:
            entry = {"name": nm, "title": ti}
            if entry not in team:
                team.append(entry)

    desc_el = main.select_one(".case_details_right .u-rich-text")
    long_description = clean(desc_el.get_text(" ")) if desc_el else None

    site_el = main.select_one("a.share_link[href]")
    href = site_el.get("href") if site_el else None
    detail_company_url = href if href and href != "#" else None

    return {
        "case_study_url": DETAIL_URL.format(slug=slug),
        "tagline": tagline,
        "founded_year": founded_year,
        "parkway_deal_team": team,
        "long_description": long_description,
        "detail_company_url": detail_company_url,
    }


def main():
    limit = int(sys.argv[sys.argv.index("--limit") + 1]) if "--limit" in sys.argv else None

    print(f"Fetching {PORTFOLIO_URL}")
    r = fetch(PORTFOLIO_URL)
    rows = parse_grid(r.text)
    print(f"  found {len(rows)} portfolio grid entries")

    if limit:
        rows = rows[:limit]

    scraped_at = datetime.now(timezone.utc).isoformat()
    out = []
    for row in rows:
        name = row["name"]
        if not name:
            print(f"  ! skipping an entry with no derivable name (logo={row['logo_url']!r})", file=sys.stderr)
            continue

        detail = fetch_detail(name)
        time.sleep(0.5)

        company_url = row["company_url"] or (detail.get("detail_company_url") if detail else None)
        description = row["description"] or (detail.get("long_description") if detail else None)

        sectors = [row["industry"]] if row["industry"] else []

        out.append({
            "company_name": name,
            "description": description,
            "tagline": detail.get("tagline") if detail else None,
            "company_url": company_url,
            "case_study_url": detail.get("case_study_url") if detail else None,
            "logo_url": row["logo_url"],
            "sectors": sectors,
            "stage": row["stage"],
            "founded_year": detail.get("founded_year") if detail else None,
            "parkway_deal_team": detail.get("parkway_deal_team") if detail else [],
            "everywhere_tags": everywhere_tags(name, description, sectors),
            "source_url": SOURCE_URL,
            "scraped_at": scraped_at,
        })

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2)

    # ---- summary ----
    n = len(out)
    print(f"\nWrote {n} companies -> {OUT}")
    for field in ("description", "tagline", "company_url", "case_study_url", "logo_url", "founded_year"):
        miss = sum(1 for r in out if not r[field])
        print(f"  {field:18s} missing: {miss}/{n}")
    print(f"  sectors empty:     {sum(1 for r in out if not r['sectors'])}/{n}")
    print(f"  deal-team empty:   {sum(1 for r in out if not r['parkway_deal_team'])}/{n}")
    from collections import Counter
    by_stage = Counter(r["stage"] for r in out)
    print("  by stage:", dict(by_stage))
    untagged = [r["company_name"] for r in out if not r["everywhere_tags"]]
    print(f"  untagged:          {len(untagged)}/{n}" + (f" -> {untagged}" if untagged else ""))
    by_tag = Counter(t for r in out for t in r["everywhere_tags"])
    print("  by everywhere_tag:")
    for t, k in by_tag.most_common():
        print(f"    {k:3d}  {t}")


if __name__ == "__main__":
    main()
