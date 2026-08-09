#!/usr/bin/env python3
"""
Bedrock Capital portfolio scraper -> bedrock_companies.json

Scrapes Bedrock's own site (https://www.bedrockcap.com/investments) -- there is
no /portfolio, /companies, sitemap.xml, or robots.txt on this domain (all 404).
Bedrock is a Next.js (App Router) site hosted on Vercel; the page has NO
__NEXT_DATA__ blob and NO separate JSON API. Instead each page ships React
Server Component "flight" data inline via `self.__next_f.push([1, "..."])`
script tags -- a serialized string containing escaped JSON-like tuples. The
per-company cards live inside that string as literal
`"eventProperties":{"companyName":"X","founderName":"Y",...}` objects next to
the card's `href` (external website), logo `src`, one-line description text,
and portrait image `src`.

The `/investments` page is explicitly headed "Select Investments" -- this is a
curated highlight reel, not a claim of exhaustive portfolio disclosure, but it
is the ONLY list of portfolio companies Bedrock publishes anywhere on its site
(confirmed identical set also appears on /letter; /team, /news, /contact carry
no company data; /companies and /portfolio 404; sitemap.xml and robots.txt both
404). So: 6 companies, taken verbatim, nothing invented.

Empty != absent, checked: no exit/acquirer/ticker/status is denormalized into
the name or description text for any of the 6 (Bitcoin is listed as a portfolio
"investment" the way Bedrock itself frames it -- not a VC-funded startup with a
cap table -- so status/founded/stage fields are simply not published for any
entry and are omitted from the schema rather than guessed).

requirements:
    pip install requests

usage:
    python3 bedrock_scraper.py            # writes ../data/bedrock_companies.json
    python3 bedrock_scraper.py --limit 3  # only the first ~3 for a test run
"""

import json
import os
import re
import socket
import sys
import time
from datetime import datetime, timezone

import requests

URL = "https://www.bedrockcap.com/investments"
SOURCE_URL = "https://www.bedrockcap.com/investments"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "data", "bedrock_companies.json")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}
TIMEOUT = 45
RETRIES = 3

# --- Webflow legacy-IP workaround (not needed for this Vercel-hosted site, but
# kept defensive per orchestrator note: some environments can't route to a
# CDN's current IP). Try normal DNS first; only pin a fallback IP if the normal
# connection fails AND the host looks like a known-affected CDN hostname.
_FALLBACK_IPS = {
    "cdn.webflow.com": "75.2.70.75",
}
_orig_getaddrinfo = socket.getaddrinfo


def _patched_getaddrinfo(host, *args, **kwargs):
    try:
        return _orig_getaddrinfo(host, *args, **kwargs)
    except OSError:
        ip = _FALLBACK_IPS.get(host)
        if ip:
            return _orig_getaddrinfo(ip, *args, **kwargs)
        raise


socket.getaddrinfo = _patched_getaddrinfo

# everywhere_tags keyword classifier (substrings, lowercased) -- copied from
# menlo_scraper.py / insight_scraper.py / iconiq_scraper.py. Bedrock publishes
# no sector taxonomy of its own (no filters, no category labels anywhere on the
# site), so every record is tagged purely from name + one-line description.
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
    ("Web3 / Crypto", ["crypto", "blockchain", "web3", "token", "on-chain", "ethereum", "bitcoin", "decentral", "stablecoin", "nft",
                       "digital currency"]),
    ("Gaming / Media / Entertainment", ["game", "gaming", "music", "video", "creator", "content", "publish",
                                        "entertain", "newsletter", "podcast", "film", "streaming", "social media", "media platform"]),
    ("Dev Tools / Cloud", ["developer", " api ", "apis", "api platform", "infrastructure", "database", "cloud",
                           "open source", "devops", "sdk", "kubernetes", "container", "observability", "deploy", "compute",
                           "storage", "serverless", "inference", "networking", "ethernet", "coding", "codebase", "low-code",
                           "no-code", "source code", "development platform", "incident", " sre", "voicemail", "communications",
                           "llm", "foundation model", "interpretability", "voice keyboard", "voice recognition", "frame relay",
                           "log management", "file sharing", "tech stack", "voice agent", "appliance software",
                           "text to speech", "web development", "front-end", "frontend"]),
    ("Data & Analytics", ["analytics", "business intelligence", "data platform", "data warehouse", "data lake",
                          "data pipeline", "insights", "dashboard", "experimentation", "decision intelligence", "data quality",
                          "analyz", "data curation", "quality management", "relationship intelligence",
                          "data discovery", "data analysis", "edge-data", "complex data", "real-world data", "analyst",
                          "data intelligence", "data transformation", "data integration"]),
    ("Future of Work", ["workforce", "hiring", "recruit", "employee", "productivity", "collaborat", "talent",
                        "workplace", "human resources", " hr ", "learning platform", "customer success", "customer service",
                        "customer support", "presales", " sales ", "onboarding", "workflow", "saas management", "ai assistant",
                        "project management", "partnerships platform", "partnership", "teamwork", "scheduling", "work assistant",
                        "it management"]),
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
                           "lawsuit", "lawyer", "public safety"]),
    ("Deeptech / Robotics / AR/VR", ["robot", "hardware", "semiconductor", "chip", "drone", "aerospace",
                                     "augmented reality", "virtual reality", "satellite", "quantum", "sensor", "rfid", "wifi",
                                     "space", "rocket", "launch vehicle", "optics", "defense", "defense technology",
                                     "defense tech"]),
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


def everywhere_tags(name, description):
    """Bedrock publishes no sector taxonomy at all -- classify purely by
    keyword-matching name + one-line description. Order most->least relevant,
    cap at 4. AI alone is not a category (OpenAI is tagged Dev Tools / Cloud
    for the "AI research and development" platform market it serves, not
    tagged as "AI")."""
    tags = []
    text = f"{name or ''} {description or ''}".lower()
    for tag, kws in KEYWORD_TAGS:
        if tag in tags:
            continue
        if any(kw in text for kw in kws):
            tags.append(tag)
    return tags[:4]


def extract_companies(html):
    """The page ships React Server Component flight data as several
    `self.__next_f.push([1, "<escaped-json-ish string>"])` script tags. Concat
    them, unescape \\" and \\n, then regex out each company card's
    eventProperties (name/founder) + the href immediately following it, plus
    the one-line description and logo path that follow in the same card."""
    scripts = re.findall(r"<script>(.*?)</script>", html, re.S)
    full = "\n".join(scripts)
    text = full.replace('\\"', '"').replace("\\n", "\n").replace("\\u0026", "&")

    # Preload hints (`:HL["/images/entrepreneur-X.webp","image"]`) enumerate
    # every portrait image in the same page order as the company cards. Some
    # cards reference their portrait indirectly through a numbered flight-data
    # placeholder ("$L1f") rather than inline, so this ordered list is a more
    # reliable fallback than searching the raw text after each card's match.
    portrait_hints = re.findall(r':HL\["(/images/entrepreneur-[^"]+)","image"\]', text)

    rows = []
    for i, m in enumerate(re.finditer(
        r'"companyName":"([^"]*)","founderName":"([^"]*)"[^}]*},"href":"([^"]*)"',
        text,
    )):
        name, founder, href = m.groups()
        tail = text[m.end():m.end() + 3000]

        desc_m = re.search(r'"pt-2 text-xs[^"]*","children":\["([^"]+)"', tail)
        description = clean(desc_m.group(1)) if desc_m else None

        logo_m = re.search(r'"src":"(/logos/investment-[^"]+)"', tail)
        logo_path = logo_m.group(1) if logo_m else None

        portrait_m = re.search(r'"src":"(/images/entrepreneur-[^"]+)"', tail)
        portrait_path = portrait_m.group(1) if portrait_m else (
            portrait_hints[i] if i < len(portrait_hints) else None
        )

        rows.append({
            "company_name": clean(name),
            "founder_name": clean(founder) or None,
            "description": description,
            "company_url": clean(href),
            "logo_url": ("https://www.bedrockcap.com" + logo_path) if logo_path else None,
            "founder_image_url": ("https://www.bedrockcap.com" + portrait_path) if portrait_path else None,
        })
    return rows


def main():
    limit = int(sys.argv[sys.argv.index("--limit") + 1]) if "--limit" in sys.argv else None

    html = get(URL)
    rows = extract_companies(html)
    if limit:
        rows = rows[:limit]

    scraped_at = datetime.now(timezone.utc).isoformat()
    out = []
    seen = set()
    for r in rows:
        if not r["company_name"] or r["company_name"] in seen:
            continue
        seen.add(r["company_name"])
        out.append({
            **r,
            "everywhere_tags": everywhere_tags(r["company_name"], r["description"]),
            "source_url": SOURCE_URL,
            "scraped_at": scraped_at,
        })

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2)

    # ---- summary ----
    n = len(out)
    print(f"wrote {n} companies -> {OUT}")
    for field in ("founder_name", "description", "company_url", "logo_url", "founder_image_url"):
        miss = sum(1 for r in out if not r[field])
        print(f"  {field:18s} missing: {miss}/{n}")
    untagged = [r["company_name"] for r in out if not r["everywhere_tags"]]
    print(f"  untagged:          {len(untagged)}/{n}" + (f" -> {untagged}" if untagged else ""))
    by_tag = {}
    for r in out:
        for t in r["everywhere_tags"]:
            by_tag[t] = by_tag.get(t, 0) + 1
    print("  by everywhere_tag:")
    for t, k in sorted(by_tag.items(), key=lambda x: -x[1]):
        print(f"    {k:3d}  {t}")


if __name__ == "__main__":
    main()
