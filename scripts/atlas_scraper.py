#!/usr/bin/env python3
"""
Atlas Venture portfolio scraper -> atlas_companies.json

Scrapes Atlas Venture's portfolio (https://atlasventure.com/portfolio/) into a
JSON file. WordPress site using the "Search & Filter Pro" plugin -- the
portfolio grid is **fully server-rendered in the static HTML**, one page, no
pagination: `GET https://atlasventure.com/portfolio/` returns all 79
`article.company-tile` records at once.

The page also exposes a genuine server-side taxonomy filter,
`?_sft_company_status=active` / `=exited` (Atlas's own `company_status`
taxonomy, radio-button facet counts 34 / 45 = 79 total, a clean partition of
the unfiltered set) -- fetched once each and unioned by name to derive a real
`status` field, the same technique used for USV's industry/status filters.

Per `article.company-tile`:
  - `.title` (hidden div)                 -> company name
  - `.categories .cat img[data-src]`       -> Atlas's own involvement tags,
    de-duped (each icon renders twice: lazyload `<img>` + `<noscript><img>`).
    Filenames map to the page's own legend: seeded.svg -> "Seeded",
    incubated.svg -> "Incubated", co_founded.svg -> "Co-founded". 26/79 tiles
    have none of these (older/legacy investments predating the tagging).
  - `.content img[data-src]`               -> logo
  - `.content .copy`                       -> description HTML. **Empty !=
    absent**: exit info (ticker or acquirer+year) is denormalized here, not in
    a structured field -- the copy block is "<description><br><br><exit line>"
    for every company that has exited to a public listing or acquisition (the
    45 in `_sft_company_status=exited`; a few "Active" companies are ALSO
    already public, e.g. Dyne/Korro/Kailera/Sionna/Q32/Intellia/Disc Medicine,
    meaning Atlas's Active/Exited taxonomy tracks Atlas's own
    investment/board status, not whether the company itself is still private).
    Parsed into `ticker_symbol` + `exchange` (regex on "EXCHANGE: TICK (YYYY)")
    or `acquirer` + `exit_year` (regex on "Sold to X (YYYY)" / "Merged with X
    (YYYY)"); `status` field itself comes from the _sft_ filter union, not from
    text-sniffing.
  - `.buttons a.button` (text "Visit Site" / "Careers")
                                            -> company_url / careers_url

What Atlas does NOT expose on this page (checked names + descriptions per the
"Empty != absent" rule before omitting): no founders, no per-company detail
page/profile URL (the tile IS the full record -- "Visit Site" points to the
company's own external site), no therapeutic-area/sector taxonomy (the only
`_sft_` facet on the page is `company_status`), no headquarters/location, no
founded year, no investment-stage/round field beyond the three involvement
tags above.

requirements:
    pip install requests beautifulsoup4

usage:
    python3 atlas_scraper.py            # writes ../data/atlas_companies.json
    python3 atlas_scraper.py --limit 10 # only the first ~10 for a test run
"""

import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from html import unescape

import requests
from bs4 import BeautifulSoup

BASE = "https://atlasventure.com/portfolio/"
SOURCE_URL = "https://atlasventure.com/portfolio/"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "data", "atlas_companies.json")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}
TIMEOUT = 45
RETRIES = 3

ICON_LABEL = {
    "seeded": "Seeded",
    "incubated": "Incubated",
    "co_founded": "Co-founded",
}

# Atlas is a pure biotech VC -- every company is BioTech + Health by
# construction (its own portfolio has no sector taxonomy to map from), refined
# by keyword hits on the modality/indication described in the "copy" text.
KEYWORD_TAGS = [
    ("BioTech", ["biotech", "drug", "therapeut", "oncolog", "cancer", "tumor", "genomic", "genome",
                 "molecul", "antibod", "protein", "vaccine", "clinical-stage", "clinical stage", "medicine",
                 "synthetic biology", "biolog", "biopharma", "gene therapy", "gene editing", "cell therapy",
                 "rna", "mrna", "peptide", "immuno", "biosimilar", "pharma", "degrader", "inhibitor"]),
    ("Health", ["healthcare", "health care", "patient", "clinic", "medical", "mental health", "telehealth",
                "diagnos", "surgical", "doctor", "hospital", "pharmac", "therapy", "disease", "treatment",
                "vision", "ocular", "muscle", "neuro", "immune", "metabolic", "cardiovascular", "respiratory",
                "infection", "obesity"]),
    ("Data & Analytics", ["genetics", "population genetics", "computational", "data platform", "bioinformatics",
                          "functional genomics", "artificial intelligence", "machine learning"]),
    ("Deeptech / Robotics / AR/VR", ["platform technology", "chemistry", "sensor", "diagnostic device"]),
]


def get(url, params=None):
    last = None
    for attempt in range(1, RETRIES + 1):
        try:
            r = requests.get(url, headers=HEADERS, params=params, timeout=TIMEOUT)
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
    s = re.sub(r"\s+", " ", unescape(s)).strip()
    return s or None


# "<description><br><br>[NASDAQ|NYSE|SWX|XETRA|Nasdaq]: TICK (YYYY)" or
# "<description><br><br>Sold to X (YYYY)" / "Merged with X (YYYY)"
TICKER_RE = re.compile(
    r"^(?:NASDAQ|NYSE(?: American)?|Nasdaq|SWX|XETRA)\s*:\s*([A-Z][A-Z0-9.]{0,7})\s*\((\d{4})\)",
    re.I,
)
ACQUIRED_RE = re.compile(r"^Sold to\s+(.+?)\s*(?:\(|in\s+)(\d{4})\)?\.?\s*$", re.I)
MERGED_RE = re.compile(r"^Merged with\s+(.+?)\s*\((\d{4})\)\.?\s*$", re.I)
# a small number of records name TWO acquirers/years for two separate spinout
# deals in one string (e.g. "Sold to BMS (2017); Novartis (2019)") -- keep the
# acquirer text verbatim (with its own embedded year) rather than dropping the
# first deal; exit_year is left null since there's no single correct answer.
MULTI_ACQUIRED_RE = re.compile(r"^Sold to\s+(.+\(\d{4}\).*\(\d{4}\))\.?\s*$", re.I)


def parse_exit(copy_html):
    """The `.copy` block is '<description>' optionally followed by a blank
    line (one or two <br>) and a trailing exit line. Split on 2+ consecutive
    <br> tags; parse the tail into ticker/exchange or acquirer/exit_year."""
    parts = re.split(r"(?:<br\s*/?>\s*){2,}", copy_html, flags=re.I)
    description = clean(re.sub(r"<[^>]+>", " ", parts[0]))
    ticker_symbol = exchange = acquirer = exit_year = None
    if len(parts) > 1:
        tail = clean(re.sub(r"<[^>]+>", " ", parts[-1]))
        if tail:
            m = TICKER_RE.match(tail)
            if m:
                # re-match with a capture group around the exchange name too
                m2 = re.match(r"^(NASDAQ|NYSE(?: American)?|Nasdaq|SWX|XETRA)\s*:\s*([A-Z][A-Z0-9.]{0,7})\s*\((\d{4})\)", tail, re.I)
                if m2:
                    exchange = m2.group(1).upper()
                    ticker_symbol = m2.group(2)
                    exit_year = m2.group(3)
            else:
                m = MULTI_ACQUIRED_RE.match(tail)
                if m:
                    acquirer, exit_year = clean(m.group(1)), None
                else:
                    m = ACQUIRED_RE.match(tail)
                    if m:
                        acquirer, exit_year = clean(m.group(1)), m.group(2)
                    else:
                        m = MERGED_RE.match(tail)
                        if m:
                            acquirer, exit_year = f"merged with {clean(m.group(1))}", m.group(2)
    return description, ticker_symbol, exchange, acquirer, exit_year


def fetch_status_names(status):
    """Fetch the server-side `_sft_company_status` filter result and return
    the set of company names it contains (Atlas's own active/exited facet)."""
    html = get(BASE, params={"_sft_company_status": status})
    soup = BeautifulSoup(html, "html.parser")
    names = set()
    for art in soup.select("article.company-tile"):
        t = art.select_one(".title")
        if t:
            n = clean(t.get_text())
            if n:
                names.add(n)
    return names


def everywhere_tags(name, description):
    tags = ["BioTech", "Health"]  # Atlas invests exclusively in biotech/health
    text = f"{name or ''} {description or ''}".lower()
    for tag, kws in KEYWORD_TAGS:
        if tag in tags:
            continue
        if any(kw in text for kw in kws):
            tags.append(tag)
    return tags[:4]


def parse_tile(art):
    title_el = art.select_one(".title")
    name = clean(title_el.get_text()) if title_el else None
    if not name:
        return None

    # involvement tags (Seeded / Incubated / Co-founded); each icon appears
    # twice (lazyload img + noscript img) -> dedupe via a set, keep legend order
    cats_el = art.select_one(".categories")
    icons_seen = set()
    if cats_el:
        for img in cats_el.select("img[data-src]"):
            m = re.search(r"/([a-z_]+)\.svg", img.get("data-src") or "")
            if m:
                icons_seen.add(m.group(1))
    involvement = [ICON_LABEL[i] for i in ("seeded", "incubated", "co_founded") if i in icons_seen]

    content_el = art.select_one(".content")
    logo_url = None
    if content_el:
        img = content_el.select_one("img[data-src]")
        if img and img.get("data-src"):
            logo_url = clean(img["data-src"])

    copy_el = art.select_one(".content .copy")
    copy_html = copy_el.decode_contents() if copy_el else ""
    description, ticker_symbol, exchange, acquirer, exit_year = parse_exit(copy_html)

    company_url = careers_url = None
    for a in art.select(".buttons a.button"):
        href = clean(a.get("href"))
        label = clean(a.get_text())
        if not href:
            continue
        if label == "Careers":
            careers_url = href
        elif label == "Visit Site":
            company_url = href

    return {
        "company_name": name,
        "description": description,
        "company_url": company_url,
        "careers_url": careers_url,
        "logo_url": logo_url,
        "involvement": involvement,
        "status": None,  # filled in main() from the _sft_company_status union
        "ticker_symbol": ticker_symbol,
        "exchange": exchange,
        "acquirer": acquirer,
        "exit_year": exit_year,
        "everywhere_tags": everywhere_tags(name, description),
        "source_url": SOURCE_URL,
    }


def main():
    limit = int(sys.argv[sys.argv.index("--limit") + 1]) if "--limit" in sys.argv else None

    html = get(BASE)
    soup = BeautifulSoup(html, "html.parser")
    tiles = soup.select("article.company-tile")

    active_names = fetch_status_names("active")
    time.sleep(0.5)
    exited_names = fetch_status_names("exited")

    scraped_at = datetime.now(timezone.utc).isoformat()
    out = []
    for art in tiles[:limit] if limit else tiles:
        rec = parse_tile(art)
        if not rec:
            continue
        if rec["company_name"] in active_names:
            rec["status"] = "Active"
        elif rec["company_name"] in exited_names:
            rec["status"] = "Exited"
        rec["scraped_at"] = scraped_at
        out.append(rec)

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2)

    # ---- summary ----
    n = len(out)
    print(f"wrote {n} companies -> {OUT}")
    for field in ("description", "company_url", "careers_url", "logo_url", "status"):
        miss = sum(1 for r in out if not r[field])
        print(f"  {field:14s} missing: {miss}/{n}")
    print(f"  involvement empty: {sum(1 for r in out if not r['involvement'])}/{n}")
    by_status = {}
    for r in out:
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1
    print(f"  by status: {by_status}")
    tickers = [(r["company_name"], r["ticker_symbol"], r["exchange"]) for r in out if r["ticker_symbol"]]
    print(f"  tickers found ({len(tickers)}): {tickers}")
    acquired = [(r["company_name"], r["acquirer"], r["exit_year"]) for r in out if r["acquirer"]]
    print(f"  acquired/merged found ({len(acquired)}): {acquired}")
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
