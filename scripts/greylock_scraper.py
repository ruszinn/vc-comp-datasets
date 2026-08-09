#!/usr/bin/env python3
"""
Greylock portfolio scraper -> greylock_companies.json

Scrapes Greylock's portfolio (https://greylock.com/portfolio/) into a JSON file.
The page is WordPress; the visible "All Companies" grid card is populated
client-side (empty `<div class="data_block_portfolio">` placeholder), but the
per-company detail is fully server-rendered as a set of hidden
`.portfolio-modal-box` elements later in the same HTML document -- one GET
gets everything (logo, tagline, description, socials/website, domain tags,
first-partnered stage, current status, Greylock investors, leadership, HQ).
No API, no pagination, no per-company crawl needed.

Note on company name: Greylock's modal markup has no dedicated "name" text
node (the <h2> is a tagline, not the name). The reliable name source is the
logo <img alt="..."> text (cleaned of "Logo"/color-variant suffixes), which
we verified against the company's own website domain and description. The
container's `id="<slug>"` is a legacy/rebrand artifact in a few cases (e.g.
slug "rabbithole" -> current company "Boost") so it is NOT used for the name,
only kept as `company_profile_url` anchor.

requirements:
    pip install requests beautifulsoup4

usage:
    python3 greylock_scraper.py            # writes greylock_companies.json
    python3 greylock_scraper.py --limit 20 # quick test run
"""

import json
import os
import re
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

URL = "https://greylock.com/portfolio/"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "data", "greylock_companies.json")
SOURCE_URL = "https://greylock.com/portfolio/"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}
TIMEOUT = 45
RETRIES = 3
SLEEP_BETWEEN = 0.5

# Greylock's own "DOMAIN" sector tags -> everywhere_tags taxonomy.
# "AI", "Infrastructure", "SaaS", "Marketplace & Commerce" are left to the
# keyword classifier: AI alone isn't a category; the other three each span
# multiple of the 17 tags depending on what the company actually does.
SECTOR_TAG_MAP = {
    "cybersecurity": ["Cybersecurity"],
    "fintech & crypto": ["FinTech / Insurance", "Web3 / Crypto"],
    "consumer": ["Consumer"],
}

# everywhere_tags keyword classifier (fallback when Greylock's own DOMAIN tags
# don't fully resolve a tag, e.g. "AI", "SaaS", "Infrastructure", "Marketplace
# & Commerce"). Substrings, lowercased; copied/adapted from menlo_scraper.py.
KEYWORD_TAGS = [
    ("BioTech", ["biotech", "drug", "therapeut", "oncolog", "cancer", "tumor", "genomic", "genome",
                 "molecul", "antibod", "protein", "vaccine", "clinical-stage", "life science", "biolog"]),
    ("Health", ["healthcare", "health care", "patient", "clinic", "medical", "mental health", "telehealth",
                "health system", "health record", "diagnos", "surgical", "doctor", "hospital", "pharmac", "therapy"]),
    ("Cybersecurity", ["cybersecurity", "security", "secure", "privacy", "fraud", "phishing", "malware",
                       "ransomware", "endpoint", "zero trust", "vulnerab", "authentication", "threat", "identity"]),
    ("FinTech / Insurance", ["fintech", "payment", "bank", "lending", "loan", "insurance", "credit", "trading",
                             "wallet", "financ", "invoic", "accounting", "payroll", "treasury", "billing",
                             "rebate", " tax", "audit", "brokerage", "spend management", "remittance"]),
    ("Web3 / Crypto", ["crypto", "blockchain", "web3", "token", "on-chain", "onchain", "ethereum", "bitcoin",
                       "decentral", "stablecoin", "nft"]),
    ("Gaming / Media / Entertainment", ["game", "gaming", "music", "video", "creator", "content", "publish",
                                        "entertain", "newsletter", "podcast", "film", "streaming", "social media",
                                        "media platform", "media company"]),
    ("Dev Tools / Cloud", ["developer", " api ", "apis", "api platform", "infrastructure", "database", "cloud",
                           "open source", "devops", "sdk", "kubernetes", "container", "observability", "deploy",
                           "compute", "storage", "serverless", "inference", "networking", "coding", "codebase",
                           "low-code", "no-code", "source code", "development platform", "incident",
                           "foundation model", "llm", "machine learning platform"]),
    ("Data & Analytics", ["analytics", "business intelligence", "data platform", "data warehouse", "data lake",
                          "data pipeline", "insights", "dashboard", "experimentation", "decision intelligence",
                          "data quality", "analyz", "synthetic data"]),
    ("Future of Work", ["workforce", "hiring", "recruit", "employee", "productivity", "collaboration", "talent",
                        "workplace", "human resources", " hr ", "learning platform", "customer success",
                        "customer service", "customer support", "presales", " sales ", "onboarding", "workflow",
                        "staffing", "team"]),
    ("Transportation / Mobility", ["mobility", "vehicle", "transport", "autonomous", "fleet", "driving", "aviation",
                                   "aircraft", "electric vehicle", "scooter", " bike", "self-driving", "driverless"]),
    ("Logistics / Supply Chain", ["logistics", "supply chain", "freight", "warehouse", "delivery", "procurement",
                                  "inventory", "fulfillment", "shipping"]),
    ("PropTech", ["real estate", "property", "housing", "mortgage", "rental", "construction", "tenant"]),
    ("CPG", ["beverage", "snack", "consumer packaged", "beauty", "cosmetic", "apparel", "grocery", "skincare",
             "eyewear", "footwear"]),
    ("Climate / Sustainability", ["climate", "carbon", "renewable", "solar", "battery", "sustainab", "emission",
                                  "clean energy", "ev charging", "electrif"]),
    ("RegTech/Gov/Legal", ["legal", "compliance", "government", "regulat", "law firm", "attorney"]),
    ("Deeptech / Robotics / AR/VR", ["robot", "hardware", "semiconductor", "chip", "drone", "aerospace",
                                     "augmented reality", "virtual reality", "satellite", "quantum", "sensor"]),
    ("Consumer", ["marketplace", "consumer", "shopping", "social network", "community", "ecommerce", "e-commerce",
                  "subscription", "retailer", "fashion", "neighborhood", "travel"]),
]

SOCIAL_HOST_MAP = (
    ("twitter.com", "twitter"), ("x.com", "twitter"), ("business.twitter.com", "twitter"),
    ("linkedin.com", "linkedin"),
    ("youtube.com", "youtube"),
    ("instagram.com", "instagram"),
    ("facebook.com", "facebook"),
)

NAME_NOISE_WORDS = re.compile(
    r"\b(Logo|logo|Grey|NEW|New|Reverse|Dark|Black|White|Color|Canvas|F|Final|Icon)\b"
)


def fetch(url):
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


def clean_name(alt_text, slug):
    """Derive the display company name from the logo alt text (cleaned of
    'Logo'/color-variant noise words). Falls back to a title-cased slug when
    alt text is blank (rare).

    Greylock's own alt-text authoring is inconsistent: ~42/159 logos have an
    all-lowercase alt (e.g. "airbnb logo", "facebook logo", "linkedin logo")
    while the rest are properly cased. Since these are unambiguously well-
    known proper nouns misrendered by an authoring artifact -- not a
    deliberate brand style -- we title-case a string only when it is fully
    lowercase, as a mechanical casing fix (not inventing new information)."""
    if alt_text:
        s = NAME_NOISE_WORDS.sub("", alt_text)
        s = re.sub(r"\s+", " ", s).strip()
        if s:
            if s == s.lower():
                s = s.title()
            return s
    # fallback: derive from slug (e.g. "cylake" -> "Cylake")
    return slug.replace("-", " ").title() if slug else None


def domain_of(url):
    try:
        netloc = urlparse(url).netloc.lower()
        return netloc[4:] if netloc.startswith("www.") else netloc
    except Exception:
        return ""


def parse_social_links(container):
    """Return (website_url, social_urls dict) from a .social-link block.
    The external company website is marked by the icon-link-dark.svg <img>
    (not an <i> icon font); everything else is classified by link domain."""
    website = None
    socials = {}
    block = container.select_one(".social-link")
    if not block:
        return None, {}
    for a in block.select("a[href]"):
        href = a.get("href", "").strip()
        if not href:
            continue
        if a.select_one('img[alt="icon link"]'):
            website = href
            continue
        host = domain_of(href)
        matched = False
        for needle, key in SOCIAL_HOST_MAP:
            if needle in host:
                socials[key] = href
                matched = True
                break
        if not matched and host:
            # unknown social/media host (e.g. a youtube-mislabeled twitter icon
            # already handled by domain check above) -- keep under generic key
            socials.setdefault("other", []).append(href) if isinstance(socials.get("other"), list) else socials.update(
                {"other": [href]}
            )
    return website, socials


def parse_leadership(right_box):
    """Return list of {'name':..., 'title':...} from the LEADERSHIP text-box."""
    people = []
    for box in right_box.select(".text-box"):
        h5 = box.select_one("h5")
        if not h5 or clean(h5.get_text()) != "LEADERSHIP":
            continue
        for p in box.select("p"):
            txt = clean(p.get_text())
            if not txt:
                continue
            if "," in txt:
                name, title = txt.split(",", 1)
                people.append({"name": clean(name), "title": clean(title)})
            else:
                people.append({"name": txt, "title": None})
    return people


def field_text(right_box, label):
    for box in right_box.select(".text-box"):
        h5 = box.select_one("h5")
        if h5 and clean(h5.get_text()) == label:
            p = box.select_one("p")
            return clean(p.get_text()) if p else None
    return None


def split_list(text):
    if not text:
        return []
    return [clean(x) for x in text.split(",") if clean(x)]


def everywhere_tags(name, tagline, description, sectors):
    tags = []
    for s in sectors:
        for t in SECTOR_TAG_MAP.get(s.lower(), []):
            if t not in tags:
                tags.append(t)
    text = f"{name or ''} {tagline or ''} {description or ''}".lower()
    for tag, kws in KEYWORD_TAGS:
        if len(tags) >= 4:
            break
        if tag in tags:
            continue
        if any(kw in text for kw in kws):
            tags.append(tag)
    return tags[:4]


def parse(html):
    soup = BeautifulSoup(html, "html.parser")
    boxes = soup.select(".portfolio-modal-box.cropped_modal")
    companies = []
    for box in boxes:
        slug = box.get("id")
        logo_img = box.select_one(".logo-box img")
        logo_url = logo_img.get("src") if logo_img else None
        alt_text = clean(logo_img.get("alt")) if logo_img else None
        name = clean_name(alt_text, slug)
        if not name:
            continue

        h2 = box.select_one(".left-box h2")
        tagline = clean(h2.get_text()) if h2 else None

        desc_p = box.select_one(".left-box p.l")
        description = clean(desc_p.get_text()) if desc_p else None

        website, social_urls = parse_social_links(box)

        right_box = box.select_one(".right-box")
        domain_text = field_text(right_box, "DOMAIN") if right_box else None
        sectors = split_list(domain_text)
        stage = field_text(right_box, "FIRST PARTNERED") if right_box else None
        status = field_text(right_box, "CURRENT STATUS") if right_box else None
        investors_text = field_text(right_box, "INVESTORS") if right_box else None
        investors = split_list(investors_text)
        leadership = parse_leadership(right_box) if right_box else []
        hq = field_text(right_box, "HQ") if right_box else None

        companies.append({
            "company_name": name,
            "tagline": tagline,
            "description": description,
            "company_url": website,
            "company_profile_url": f"{SOURCE_URL}#{slug}" if slug else None,
            "logo_url": logo_url,
            "location": hq,
            "sectors": sectors,
            "first_partnered_stage": stage,
            "status": status,
            "leadership": leadership,
            "greylock_investors": investors,
            "social_urls": social_urls,
            "everywhere_tags": everywhere_tags(name, tagline, description, sectors),
            "source_url": SOURCE_URL,
            "scraped_at": None,  # filled in main() with the real run timestamp
        })
    return companies


def main():
    limit = None
    if "--limit" in sys.argv:
        limit = int(sys.argv[sys.argv.index("--limit") + 1])

    print(f"Fetching {URL}")
    html = fetch(URL)
    time.sleep(SLEEP_BETWEEN)

    companies = parse(html)

    # de-dupe by slug (each modal box id is already unique, but be defensive)
    seen, out = set(), []
    for c in companies:
        key = c["company_profile_url"]
        if key in seen:
            print(f"  ! duplicate '{c['company_name']}' ({key}) — keeping first", file=sys.stderr)
            continue
        seen.add(key)
        out.append(c)

    if limit:
        out = out[:limit]

    scraped_at = datetime.now(timezone.utc).isoformat()
    for c in out:
        c["scraped_at"] = scraped_at

    out.sort(key=lambda o: o["company_name"].lower())

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    by_status = Counter(o["status"] or "(blank)" for o in out)
    by_tag = Counter(t for o in out for t in o["everywhere_tags"])
    n = len(out)
    print(f"\nWrote {n} companies -> {OUT}")
    print("By status:", dict(by_status))
    print(f"With website: {sum(1 for o in out if o['company_url'])}/{n}")
    print(f"With logo: {sum(1 for o in out if o['logo_url'])}/{n}")
    print(f"With sectors (DOMAIN): {sum(1 for o in out if o['sectors'])}/{n}")
    print(f"With leadership: {sum(1 for o in out if o['leadership'])}/{n}")
    print(f"With greylock_investors: {sum(1 for o in out if o['greylock_investors'])}/{n}")
    print(f"With location (HQ): {sum(1 for o in out if o['location'])}/{n}")
    print(f"With description: {sum(1 for o in out if o['description'])}/{n}")
    print(f"With tagline: {sum(1 for o in out if o['tagline'])}/{n}")
    print(f"Untagged (everywhere_tags empty): {sum(1 for o in out if not o['everywhere_tags'])}/{n}")
    print("By everywhere_tag:")
    for t, c in by_tag.most_common():
        print(f"  {c:>4}  {t}")


if __name__ == "__main__":
    main()
