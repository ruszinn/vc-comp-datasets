#!/usr/bin/env python3
"""
ARCH Venture Partners portfolio scraper -> arch_companies.json

Scrapes ARCH Venture Partners' portfolio (https://www.archventure.com/portfolio/)
into a JSON file. The site is WordPress and the entire company list is
server-rendered into ONE page: a `#company-list` > `#active-exited-companies`
container with a `.company` block per company (name, external website link,
logo, one-line description, and a location string). No API, no pagination,
no per-company detail page -- one HTTP request gets everything.

ARCH publishes no structured status/exit/ticker field. Two things are
denormalized instead (checked per PLAYBOOK's "Empty != absent"):
  - The company NAME carries a literal "(Acquired)" suffix for exited
    companies (14/128) -- no other suffix variants ("(IPO)", "(Merged)",
    "(Inactive)") appear anywhere in the 128 titles.
  - The LOCATION string sometimes carries a trailing "| NASDAQ: TICK" or
    "| HKEX: NNNN" for companies that went public (33/128) -- this can
    co-occur with "(Acquired)" (i.e. IPO'd, later acquired).
No acquirer name or exit year is published anywhere (checked descriptions
too) -- those stay null. No sector/vertical tags, no founders, no founded
year, no partners are exposed on the page -- everywhere_tags is therefore
entirely keyword-derived (ARCH is a pure biotech/deep-science fund, so the
classifier is dominated by BioTech/Health/Deeptech keywords).

requirements:
    pip install requests beautifulsoup4

usage:
    python3 arch_scraper.py            # writes ../data/arch_companies.json
    python3 arch_scraper.py --limit 20 # only the first 20 for a test run
"""

import json
import os
import re
import sys
import time
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

URL = "https://www.archventure.com/portfolio/"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "data", "arch_companies.json")
SOURCE_URL = "https://www.archventure.com/portfolio/"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}
TIMEOUT = 45
RETRIES = 3

# everywhere_tags keyword classifier (substrings, lowercased) -- copied/trimmed
# from menlo_scraper.py / rre_scraper.py. ARCH is a biotech/deep-science VC,
# so BioTech/Health/Deeptech keywords are listed first and are the most-hit;
# the rest of the 17-tag taxonomy is kept for the handful of non-bio (compute,
# materials, energy) portfolio companies ARCH also backs.
KEYWORD_TAGS = [
    ("BioTech", ["biotech", "drug", "therapeut", "oncolog", "cancer", "tumor", "genomic", "genome",
                 "molecul", "antibod", "protein", "vaccine", "clinical-stage", "medicine", "opioid",
                 "life science", "synthetic biology", "biolog", "gene editing", "gene therapy",
                 "gene profiling", "gene delivery", "gene transplant", "rna ", "dna ", "cell therapy",
                 "immunotherap", "immunolog", "pharma", "diagnos", "sequencing", "biomolecular",
                 "biopharma", "chemistry", "chemical", "stem cell", "epigenetic", "nucleic acid",
                 "microfluidic", "optogenetic", "synthetic cell", "single cell", "organ transplant",
                 "reprogramming", "public health", "microscopy", "cone optogenetics", "vision restoration"]),
    ("Health", ["healthcare", "health care", "patient", "clinic", "medical", "mental health", "telehealth",
                "health system", "health record", "surgical", "doctor", "hospital", "pharmac", "therapy",
                "aesthetic medicine", "metabolic disease", "cardiovascular disease", "home health",
                "digital health", "accountable care", "neuroscience", "vision", "wellbeing", "better lives"]),
    ("Deeptech / Robotics / AR/VR", ["robot", "hardware", "semiconductor", "chip", "drone", "aerospace",
                                     "augmented reality", "virtual reality", "satellite", "quantum", "sensor",
                                     "rfid", "materials", "manufacturing", "automation", "data science",
                                     "computing platform", "compute platform", "device"]),
    ("Data & Analytics", ["analytics", "data platform", "data science", "machine learning platform",
                          "computational", "algorithm"]),
    ("Climate / Sustainability", ["climate", "carbon", "renewable", "solar", "battery", "sustainab", "emission",
                                  "clean energy", "electrif", "agricultur", "food"]),
    ("Dev Tools / Cloud", ["developer", "infrastructure", "cloud", "software platform", "computing"]),
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


def derive_status_ticker(raw_name, location):
    """Return (company_name, status, ticker_symbol).
    - Name suffix "(Acquired)" -> status "Acquired" (stripped from company_name).
    - Location suffix "| EXCH: TICK" -> ticker_symbol set; if not acquired,
      status "Public". A company can be both (IPO'd, later acquired) --
      ticker_symbol is still recorded from the location string.
    - Otherwise -> status "Active".
    """
    name = raw_name
    acquired = bool(re.search(r"\(Acquired\)\s*$", raw_name, re.I))
    if acquired:
        name = clean(re.sub(r"\(Acquired\)\s*$", "", raw_name, flags=re.I))

    ticker_symbol = None
    m = re.search(r"\|\s*([A-Za-z]{2,6}):\s*([A-Za-z0-9.\-]{1,8})\s*$", location or "")
    if m:
        ticker_symbol = f"{m.group(1).upper()}: {m.group(2).upper()}"

    if acquired:
        status = "Acquired"
    elif ticker_symbol:
        status = "Public"
    else:
        status = "Active"

    return name, status, ticker_symbol


def strip_ticker_from_location(location):
    """Location field is 'City, ST' or 'City, ST | EXCH: TICK' -- return just
    the place part (ticker is pulled separately into ticker_symbol)."""
    if not location:
        return None
    place = re.split(r"\s*\|\s*[A-Za-z]{2,6}:\s*[A-Za-z0-9.\-]{1,8}\s*$", location)[0]
    return clean(place)


def everywhere_tags(name, description):
    text = f"{name or ''} {description or ''}".lower()
    tags = []
    for tag, kws in KEYWORD_TAGS:
        if any(kw in text for kw in kws) and tag not in tags:
            tags.append(tag)
    return tags[:4]


def parse(html):
    soup = BeautifulSoup(html, "html.parser")
    container = soup.select_one("#active-exited-companies")
    blocks = container.select(".company") if container else []
    companies = []
    scraped_at = datetime.now(timezone.utc).isoformat()

    for b in blocks:
        title_el = b.select_one(".co-title")
        raw_name = clean(title_el.get_text()) if title_el else None
        if not raw_name:
            continue

        link = b.select_one(".co-logo a[href]")
        company_url = clean(link.get("href")) if link else None

        img = b.select_one(".co-logo img")
        logo_url = clean(img.get("src")) if img else None
        if logo_url and "no_logo" in logo_url.lower():
            logo_url = None  # site's own placeholder image, not a real logo

        desc_el = b.select_one(".co-desc")
        description = clean(desc_el.get_text()) if desc_el else None

        loc_el = b.select_one(".co-location")
        raw_location = clean(loc_el.get_text()) if loc_el else None

        company_name, status, ticker_symbol = derive_status_ticker(raw_name, raw_location or "")
        location = strip_ticker_from_location(raw_location)

        companies.append({
            "company_name": company_name,
            "description": description,
            "company_url": company_url,
            "logo_url": logo_url,
            "location": location,
            "status": status,
            "ticker_symbol": ticker_symbol,
            "acquirer": None,   # published nowhere on the page (checked description prose too)
            "exit_year": None,  # published nowhere on the page
            "everywhere_tags": everywhere_tags(company_name, description),
            "source_url": SOURCE_URL,
            "scraped_at": scraped_at,
        })
    return companies


def main():
    limit = None
    if "--limit" in sys.argv:
        limit = int(sys.argv[sys.argv.index("--limit") + 1])

    print(f"Fetching {URL}")
    companies = parse(get(URL))

    seen, out = set(), []
    for c in companies:
        k = c["company_name"].strip().lower()
        if k in seen:
            print(f"  ! duplicate '{c['company_name']}' — keeping first", file=sys.stderr)
            continue
        seen.add(k)
        out.append(c)

    if limit:
        out = out[:limit]

    out.sort(key=lambda o: o["company_name"].lower())

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    from collections import Counter
    by_status = Counter(o["status"] for o in out)
    by_tag = Counter(t for o in out for t in o["everywhere_tags"])
    print(f"\nWrote {len(out)} companies -> {OUT}")
    print("By status:", dict(by_status),
          "| with ticker:", sum(1 for o in out if o["ticker_symbol"]))
    print("With website:", sum(1 for o in out if o["company_url"]),
          "| with description:", sum(1 for o in out if o["description"]),
          "| with location:", sum(1 for o in out if o["location"]),
          "| untagged:", sum(1 for o in out if not o["everywhere_tags"]))
    print("By everywhere_tag:")
    for t, c in by_tag.most_common():
        print(f"  {c:>4}  {t}")


if __name__ == "__main__":
    main()
