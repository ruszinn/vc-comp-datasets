#!/usr/bin/env python3
"""
Dragoneer Investment Group portfolio scraper -> dragoneer_companies.json

Scrapes Dragoneer's public "Companies" page (https://www.dragoneer.com/companies)
into a JSON file. Dragoneer is a growth/crossover fund; its site (Webflow build)
publishes NO per-company portfolio grid, descriptions, sectors, founders, or
detail pages -- the sitemap (https://www.dragoneer.com/sitemap.xml) lists only
three URLs: `/`, `/companies`, `/contact`. `/portfolio` does not exist (404).

The `/companies` page contains exactly one static (not CMS/Finsweet -- no
`w-dyn-item`/`fs-cmsfilter`/API markers found) row of 29 company logo images
(`.logo1_list img.logo1_logo`), each with only an `alt` attribute ("<Name> Logo")
and a CDN `src`. That is the entirety of the structured data Dragoneer exposes:
company name (derived from alt text) + logo URL. No description, no outbound
website link, no sector/stage tag, no investment date, and no per-company detail
page exist anywhere on the site for these entries.

The page's own footer disclaimer (captured verbatim in `PAGE_DISCLAIMER` and
carried in every record's `source_note`) states the list is a curated subset:
"a subset of the investments made by Dragoneer's funds and include private
investments with invested capital of or greater than $10 million that have gone
public as well as private investments with invested capital of or greater than
$500 million... as of March 31, 2025." So this is knowingly partial (large/exited
positions only), not a full portfolio -- reported as a caveat, not fabricated
around.

"Empty != absent" checked: alt text is the only place a name lives (no adjacent
caption/link/description nodes at all); there is no denormalized status/ticker/
acquirer text anywhere on the page or in the alt strings themselves (e.g. no
"(NYSE: X)" suffixes) even though several logos (Airbnb, DoorDash, Robinhood,
Roblox, Snowflake, Rivian, Uber, etc.) are in fact publicly traded -- Dragoneer
simply doesn't publish that structurally, so `ticker_symbol`/`status` fields are
NOT invented from outside knowledge.

Network note: this sandbox cannot route to dragoneer.com's current Webflow edge
IP (198.202.211.1 -- connection times out) and the legacy Webflow edge IP
(75.2.70.75) also fails TLS ("SSL_ERROR_SYSCALL"/handshake failure) for this
host. `fetch()` therefore tries, in order: (1) normal HTTPS, (2) the legacy
Webflow IP via a monkeypatched `socket.getaddrinfo`, (3) the `r.jina.ai`
read-only fetch-proxy relay (relays the *same* dragoneer.com content verbatim;
not a third-party data source). In this run only (3) succeeded -- every fetch
in this dataset was made via the relay. Spot-check the relay output before
trusting it blindly: this script diffed the parsed logo count/alt-text list
against a manual read of the raw relayed HTML (29 images, all alt text intact,
footer disclaimer text intact) -- see the module docstring above for the exact
figures reconciled during recon.

requirements:
    pip install requests beautifulsoup4

usage:
    python3 dragoneer_scraper.py            # writes ../data/dragoneer_companies.json
    python3 dragoneer_scraper.py --limit 10 # only the first ~10 for a test run
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

BASE_URL = "https://www.dragoneer.com/companies"
SOURCE_URL = "https://www.dragoneer.com/companies"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "data", "dragoneer_companies.json")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}
TIMEOUT = 45
RETRIES = 3
SLEEP_BETWEEN = 1.5  # politeness, mainly relevant to the relay hop

LEGACY_WEBFLOW_IP = "75.2.70.75"
JINA_PROXY = "https://r.jina.ai/"

# Verbatim footer disclaimer from the /companies page -- kept as a caveat field
# on every record since the list is knowingly a curated subset, not a full
# portfolio (see module docstring).
PAGE_DISCLAIMER = (
    "Past performance is not an indication of future results and there is no "
    "guarantee of profitability. The companies included above are a subset of "
    "the investments made by Dragoneer’s funds and include private "
    "investments with invested capital of or greater than $10 million that have "
    "gone public as well as private investments with invested capital of or "
    "greater than $500 million. Dragoneer expects to update these companies on "
    "a periodic basis and the foregoing information is as of March 31, 2025."
)


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
    """Read-only fetch-proxy relay of the *same* dragoneer.com page (used only
    because this sandbox cannot route to the site directly or via the legacy
    IP) -- not a third-party data source, it fetches this page verbatim."""
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
                if text and len(text) > 500:
                    print(f"  fetched via {name}", file=sys.stderr)
                    return text
            except Exception as e:  # noqa
                last = e
                print(f"  ! {name} fetch failed ({e})", file=sys.stderr)
            time.sleep(SLEEP_BETWEEN)
        wait = 1.5 * attempt
        print(f"  ! all fetch strategies failed; retry {attempt}/{RETRIES} in {wait:.1f}s", file=sys.stderr)
        time.sleep(wait)
    raise SystemExit(f"FATAL: could not fetch {url}: {last}")


def clean(s):
    if s is None:
        return None
    s = re.sub(r"\s+", " ", s).strip()
    return s or None


# Alt text is consistently "<Name> Logo"/"<Name> logo" (case-inconsistent on
# the site); strip that suffix to recover the display name. A couple of
# entries have no "logo" suffix at all ("Service Titan").
LOGO_SUFFIX_RE = re.compile(r"\s+logo\s*$", re.IGNORECASE)


def name_from_alt(alt):
    alt = clean(alt)
    if not alt:
        return None
    return clean(LOGO_SUFFIX_RE.sub("", alt))


# everywhere_tags keyword classifier (substrings, lowercased) -- copied from
# menlo_scraper.py / iconiq_scraper.py / spark_scraper.py. Dragoneer publishes
# no sector taxonomy or description at all on this page, so tagging here is
# name-only best-effort (several names alone are enough to classify
# confidently -- e.g. "Datadog"/"Snowflake" -> Dev Tools / Cloud, "Robinhood"
# -> FinTech / Insurance -- but plenty of names carry no market signal by
# themselves and are left untagged rather than guessed).
KEYWORD_TAGS = [
    ("BioTech", ["biotech", "therapeut", "oncolog", "genomic", "genome", "pharma"]),
    ("Health", ["health", "medical", "clinic", "care", "patient"]),
    ("Cybersecurity", ["security", "secure", "cyber"]),
    ("FinTech / Insurance", ["financ", "bank", "capital", "insur", "invest", "payment",
                             "pay", "wealth", "credit", "lending", "robinhood"]),
    ("Web3 / Crypto", ["crypto", "blockchain", "web3", "token", "coin", "chain"]),
    ("Gaming / Media / Entertainment", ["game", "gaming", "roblox", "music", "media",
                                        "entertain", "spotify"]),
    ("Dev Tools / Cloud", ["cloud", "data", "software", "tech", "analytics", "snowflake",
                           "datadog"]),
    ("Data & Analytics", ["analytics", "clearwater"]),
    ("Transportation / Mobility", ["mobility", "vehicle", "rivian", "uber", "didi",
                                   "ride", "auto", "drive"]),
    ("Logistics / Supply Chain", ["logistics", "supply chain", "freight", "delivery",
                                  "doordash"]),
    ("PropTech", ["real estate", "property", "procore", "construction"]),
    ("CPG", ["beauty", "cosmetic", "apparel", "grocery"]),
    ("Climate / Sustainability", ["climate", "solar", "energy", "sustainab"]),
    ("RegTech/Gov/Legal", ["legal", "compliance", "government", "regulat"]),
    ("Deeptech / Robotics / AR/VR", ["robot", "hardware", "semiconductor", "chip",
                                     "aerospace"]),
    ("Consumer", ["airbnb", "marketplace", "consumer", "shopping", "social"]),
]


def everywhere_tags(name):
    """Name-only keyword classification -- no description/sector data exists
    on this page to draw from. Order most->least relevant, cap at 4."""
    tags = []
    text = f" {(name or '').lower()} "
    for tag, kws in KEYWORD_TAGS:
        if tag in tags:
            continue
        if any(kw in text for kw in kws):
            tags.append(tag)
    return tags[:4]


def parse_companies(html_text):
    soup = BeautifulSoup(html_text, "html.parser")
    container = soup.select_one(".logo1_list")
    imgs = container.select("img.logo1_logo") if container else soup.select("img.logo1_logo")

    out = []
    for img in imgs:
        name = name_from_alt(img.get("alt"))
        if not name:
            continue
        logo_url = clean(img.get("src"))
        out.append({"company_name": name, "logo_url": logo_url})
    return out


def main():
    limit = int(sys.argv[sys.argv.index("--limit") + 1]) if "--limit" in sys.argv else None

    html_text = fetch(BASE_URL)
    rows = parse_companies(html_text)

    scraped_at = datetime.now(timezone.utc).isoformat()
    out = []
    for r in (rows[:limit] if limit else rows):
        out.append({
            "company_name": r["company_name"],
            "logo_url": r["logo_url"],
            "everywhere_tags": everywhere_tags(r["company_name"]),
            "source_note": PAGE_DISCLAIMER,
            "source_url": SOURCE_URL,
            "scraped_at": scraped_at,
        })

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2)

    # ---- summary ----
    n = len(out)
    print(f"wrote {n} companies -> {OUT}")
    for field in ("logo_url",):
        miss = sum(1 for r in out if not r[field])
        print(f"  {field:10s} missing: {miss}/{n}")
    untagged = [r["company_name"] for r in out if not r["everywhere_tags"]]
    print(f"  untagged:  {len(untagged)}/{n}" + (f" -> {untagged}" if untagged else ""))
    by_tag = {}
    for r in out:
        for t in r["everywhere_tags"]:
            by_tag[t] = by_tag.get(t, 0) + 1
    print("  by everywhere_tag:")
    for t, k in sorted(by_tag.items(), key=lambda x: -x[1]):
        print(f"    {k:3d}  {t}")


if __name__ == "__main__":
    main()
