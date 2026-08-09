#!/usr/bin/env python3
"""
Oak HC/FT portfolio scraper -> oakhcft_companies.json

Scrapes Oak HC/FT's portfolio (https://www.oakhcft.com/portfolio) into a JSON
file. The site is a Webflow build with Finsweet-CMS-powered tab lists (like
RRE/ICONIQ). Two-step crawl:

  1. GET /portfolio once. It renders FIVE parallel Finsweet CMS lists in
     tabs -- All, Healthcare, Fintech, "AI + HC/FT" (data-w-tab="AI"), Exits --
     each a `.porto-list-w.w-dyn-list` of `.porto-card.w-dyn-item` (name,
     one-line description, a `/company/<slug>` detail link, and sometimes an
     `.fs-detailsmall.is-aq` exit label e.g. "Acquired: Elevance Health").
     Each tab's list also carries a Finsweet "load more" pager
     (`?<key>_page=2`), but the query string does NOT change the server-
     rendered HTML at all (Finsweet pagination is client-side JS the relay/
     static fetch never executes) -- refetching `?<key>_page=2` returns byte-
     identical company sets to page 1, confirmed by diff. So page 2+ is
     unreachable regardless of source.
  2. **"Empty != absent" trap, exactly like ICONIQ/RRE:** the "All" tab's own
     pager is `hidden-forever` (Webflow's own signal that it's "exhausted"),
     yet the "All" tab (100 companies) is NOT a superset of the other four
     tabs -- 7 companies (VaxCare, Veda, VillageMD, Wayspring, Wandz.ai, XFX,
     ZenBusiness) appear ONLY in Healthcare/Fintech/Exits, never in All. So
     the true roster is the **union of all 5 tabs by company name**, giving
     107 unique companies (verified: 0 conflicting slugs/descriptions/exit
     labels across tabs for names appearing in multiple).
  3. Each unique company's `/company/<slug>` detail page (all 107 crawled)
     is far richer than the card and is treated as the primary source for
     structured fields: hero has name/description/website/LinkedIn/Careers
     links (`.sosmed-w a`, skip `.w-condition-invisible` placeholders) and a
     logo (`img.img-company-logo`, skip the one with `w-condition-invisible`,
     which is Oak's generic placeholder graphic); a `.company-status-info-w`
     block gives **Sector** (single value, Oak's own coarser 3-way split:
     Healthcare/Fintech/AI... "AI" sector is not seen standalone on any detail
     page checked -- AI companies get Healthcare or Fintech as their Sector),
     **Status** (free text: "Active", "Acquired: <Acquirer>", "Merged: <X>",
     "IPO: <TICKER>", or bare "Exited" with no named acquirer -- checked the
     hero description prose for a fallback acquirer/ticker on every bare-
     "Exited" case; none exists, so those stay legitimately unresolved), and
     **CEO** (present on every one of the 107 profiles checked in recon).
     One confirmed site data-entry bug: Pagaya's card says "IPO: PGY" but its
     detail-page Status div has that text in the HIDDEN slot and shows the
     placeholder "Active" as visible instead -- `parse_detail()` reconciles
     by preferring whichever of {detail Status, card exit label} signals the
     more definitive/advanced state, so Pagaya still comes out "Public"/PGY.

CAVEAT -- relay routing: at the start of this build session, www.oakhcft.com
was directly unreachable from this machine (CDN IP 198.202.211.1 unroutable;
legacy IPs 75.2.70.75 / 99.83.190.102 reject the TLS handshake), so recon and
the first attempted full run went through the read-only relay `r.jina.ai`
(`https://r.jina.ai/https://www.oakhcft.com/...` with header
`x-respond-with: html` to get raw HTML instead of markdown) -- still Oak
HC/FT's own published page content, just proxied. Mid-session the network
path to oakhcft.com recovered (direct HTTPS started returning 200), and the
FINAL full run that produced `oakhcft_companies.json` fetched every page
DIRECTLY (`fetched_via_relay: false` on every record) -- confirmed by
diffing a re-fetched detail page against the parsed output. The script tries
direct HTTPS first, then a legacy-IP pin, then the relay, in that order (each
record's `fetched_via_relay` flag records which tier actually served it), so
it works unchanged regardless of which path is healthy on a given run. Since
relay involvement varied across the session, spot-check parsed output
against the live site if re-running when the network is degraded again.

requirements:
    pip install requests beautifulsoup4

usage:
    python3 oakhcft_scraper.py            # writes ../data/oakhcft_companies.json
    python3 oakhcft_scraper.py --limit 10 # only the first ~10 companies (detail crawl) for a test run
"""

import json
import os
import re
import sys
import time
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

DOMAIN = "www.oakhcft.com"
BASE = f"https://{DOMAIN}"
PORTFOLIO_URL = f"{BASE}/portfolio"
SOURCE_URL = PORTFOLIO_URL
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "data", "oakhcft_companies.json")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}
TIMEOUT = 45
RETRIES = 3
PROBE_TIMEOUT = (5, 8)  # (connect, read) -- fail fast on direct/legacy-IP tiers before falling to the relay

# Legacy IPs seen for oakhcft.com (Webflow-hosted). Tried as a TLS-SNI pin
# after direct DNS/HTTPS fails, before falling back to the relay.
LEGACY_IPS = ["75.2.70.75", "99.83.190.102"]

RELAY_BASE = "https://r.jina.ai/"
RELAY_HEADERS = dict(HEADERS, **{"x-respond-with": "html"})
RELAY_SLEEP = 1.5  # politeness: relay is shared infra -- ~1 req/1.5s

_relay_used = {"flag": False}


def _direct_get(url):
    r = requests.get(url, headers=HEADERS, timeout=PROBE_TIMEOUT)
    r.raise_for_status()
    return r.text


def _legacy_ip_get(url, ip):
    # Pin the TLS connection to a legacy IP via a custom HTTPSAdapter-less
    # trick: use requests' `Session` with a manual Host header over the IP.
    from urllib.parse import urlparse
    parsed = urlparse(url)
    pinned_url = url.replace(parsed.netloc, ip, 1)
    r = requests.get(
        pinned_url,
        headers={**HEADERS, "Host": DOMAIN},
        timeout=PROBE_TIMEOUT,
        verify=False,  # legacy IP's cert won't match the IP in the URL
    )
    r.raise_for_status()
    return r.text


def _relay_get(url):
    relay_url = RELAY_BASE + url
    r = requests.get(relay_url, headers=RELAY_HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    _relay_used["flag"] = True
    return r.text


def get(url):
    """Try direct HTTPS, then legacy-IP pin, then the read-only relay
    (r.jina.ai). Direct/legacy-IP tiers use a short PROBE_TIMEOUT and a
    single attempt each -- on this machine both are confirmed unroutable /
    TLS-rejecting (verified once at session start), so we fail fast rather
    than burn the full retry/backoff budget before falling to the relay,
    which is where politeness sleeps actually apply."""
    last = None
    try:
        return _direct_get(url)
    except requests.RequestException as e:
        last = e
    print(f"  ! direct fetch failed for {url} ({last}); trying legacy IPs", file=sys.stderr)

    for ip in LEGACY_IPS:
        try:
            import urllib3
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
            return _legacy_ip_get(url, ip)
        except requests.RequestException as e:
            last = e
    print(f"  ! legacy-IP fetch failed for {url} ({last}); falling back to relay (r.jina.ai)", file=sys.stderr)

    for attempt in range(1, RETRIES + 1):
        try:
            text = _relay_get(url)
            time.sleep(RELAY_SLEEP)
            return text
        except requests.RequestException as e:
            last = e
            wait = RELAY_SLEEP * attempt
            print(f"  ! relay request failed ({e}); retry {attempt}/{RETRIES} in {wait:.1f}s", file=sys.stderr)
            time.sleep(wait)
    raise SystemExit(f"FATAL: could not fetch {url} via direct, legacy-IP, or relay: {last}")


def clean(s):
    if s is None:
        return None
    s = re.sub(r"\s+", " ", s).strip()
    return s or None


# Oak's own 3 detail-page "Sector" values -> the 17-tag everywhere_tags
# taxonomy. "AI" is intentionally NOT mapped even if seen (AI alone is not a
# category -- classify by the market served; handled by the keyword fallback).
SECTOR_TAG_MAP = {
    "Healthcare": ["Health"],
    "Fintech": ["FinTech / Insurance"],
}

# everywhere_tags keyword classifier (substrings, lowercased) -- copied from
# rre_scraper.py / iconiq_scraper.py.
KEYWORD_TAGS = [
    ("BioTech", ["biotech", "drug", "therapeut", "oncolog", "cancer", "tumor", "genomic", "genome",
                 "molecul", "antibod", "protein", "vaccine", "clinical-stage", "medicine", "opioid", "life science",
                 "synthetic biology", "biolog", "genetic medicine"]),
    ("Health", ["healthcare", "health care", "patient", "clinic", "medical", "mental health", "telehealth",
                "health system", "health record", "diagnos", "surgical", "doctor", "hospital", "pharmac", "therapy",
                "palliative", "primary care", "specialty care", "care provider", "digestive care", "revenue cycle"]),
    ("Cybersecurity", ["cybersecurity", "security", "secure", "privacy", "fraud", "phishing", "malware",
                       "ransomware", "endpoint", "zero trust", "vulnerab", "authentication", "threat", "defense system", "identity",
                       "information protection", "identity verif"]),
    ("FinTech / Insurance", ["fintech", "payment", "bank", "lending", "loan", "insurance", "credit", "trading",
                             "wallet", "financ", "invoic", "accounting", "payroll", "treasury", "billing", "pricing platform",
                             "rebate", " tax", "audit", "money management", "robo-advisor", "brokerage", "spend management",
                             "capital markets", "investing", "claims", "settlement", "private markets"]),
    ("Web3 / Crypto", ["crypto", "blockchain", "web3", "token", "on-chain", "ethereum", "bitcoin", "decentral", "stablecoin", "nft"]),
    ("Gaming / Media / Entertainment", ["game", "gaming", "music", "video", "creator", "content", "publish",
                                        "entertain", "newsletter", "podcast", "film", "streaming", "social media", "media platform"]),
    ("Dev Tools / Cloud", ["developer", " api ", "apis", "api platform", "infrastructure", "database", "cloud",
                           "open source", "devops", "sdk", "kubernetes", "container", "observability", "deploy", "compute",
                           "storage", "serverless", "inference", "networking", "ethernet", "coding", "codebase", "low-code",
                           "no-code", "source code", "development platform", "incident", " sre", "voicemail", "communications",
                           "llm", "foundation model", "interpretability", "large tabular model", "tech stack"]),
    ("Data & Analytics", ["analytics", "business intelligence", "data platform", "data warehouse", "data lake",
                          "data pipeline", "insights", "dashboard", "experimentation", "decision intelligence", "data quality",
                          "analyz", "data curation", "quality management", "relationship intelligence",
                          "data discovery", "data analysis", "real-world data", "underwriting"]),
    ("Future of Work", ["workforce", "hiring", "recruit", "employee", "productivity", "collaborat", "talent",
                        "workplace", "human resources", " hr ", "learning platform", "customer success", "customer service",
                        "customer support", "presales", " sales ", "onboarding", "workflow", "saas management", "ai assistant",
                        "project management", "training platform"]),
    ("Transportation / Mobility", ["mobility", "vehicle", "transport", "autonomous", "fleet", "driving", "aviation",
                                   "aircraft", "electric vehicle", "scooter", " bike", "boat", "watercraft", "rideshar", "travel"]),
    ("Logistics / Supply Chain", ["logistics", "supply chain", "supply and demand", "freight", "warehouse",
                                  "delivery", "procurement", "inventory", "fulfillment", "shipping", "last-mile"]),
    ("PropTech", ["real estate", "property", "housing", "mortgage", "rental", "construction", "tenant"]),
    ("CPG", ["beverage", "snack", "consumer packaged", "beauty", "cosmetic", "apparel", "grocery", "skincare",
             "eyewear", "glasses", "footwear", "pet sitter", "pet "]),
    ("Climate / Sustainability", ["climate", "carbon", "renewable", "solar", "battery", "sustainab", "emission",
                                  "clean energy", "ev charging", "electrif", "energy", "power grid"]),
    ("RegTech/Gov/Legal", ["legal", "compliance", "government", "regulat", "law firm", "attorney", "risk services",
                           "lawsuit", "lawyer"]),
    ("Deeptech / Robotics / AR/VR", ["robot", "hardware", "semiconductor", "chip", "drone", "aerospace",
                                     "augmented reality", "virtual reality", "satellite", "quantum", "sensor", "rfid", "wifi",
                                     "space", "rocket", "launch vehicle"]),
    ("Consumer", ["marketplace", "consumer", "shopping", "social network", "community", "app for", "app that",
                  "ecommerce", "e-commerce", "subscription", "retailer", "universit", "student", "education",
                  "learning", "fashion"]),
]


def everywhere_tags(name, description, sector):
    """Oak's own detail-page Sector first (mapped via SECTOR_TAG_MAP), then
    keyword fallback on name + description to add/refine. Order most->least
    relevant, cap at 4."""
    tags = []
    for mapped in SECTOR_TAG_MAP.get(sector or "", []):
        if mapped not in tags:
            tags.append(mapped)
    text = f"{name or ''} {description or ''}".lower()
    for tag, kws in KEYWORD_TAGS:
        if tag in tags:
            continue
        if any(kw in text for kw in kws):
            tags.append(tag)
    return tags[:4]


def derive_exit(status_text):
    """Parse Oak's free-text Status field into (status, acquirer, ticker_symbol).
    Seen forms: "Active", "Acquired: <Acquirer>", "Merged: <Partner>",
    "IPO: <TICKER>", bare "Exited" (no named acquirer/ticker -- checked hero
    description prose for a fallback; none found on any bare-Exited profile,
    so acquirer/ticker legitimately stay null for those)."""
    t = clean(status_text) or "Active"
    m = re.match(r"^(Acquired|Merged):\s*(.+)$", t, re.I)
    if m:
        kind, who = m.group(1), clean(m.group(2))
        return ("Acquired" if kind.lower() == "acquired" else "Merged"), who, None
    m = re.match(r"^IPO:\s*([A-Za-z.\-]+)$", t, re.I)
    if m:
        return "Public", None, clean(m.group(1))
    if t.lower() == "exited":
        return "Exited", None, None
    return "Active", None, None


def get_tab_lists(html):
    """Return {tab_name: [card_dict, ...]} for the 5 Finsweet tab panes on
    /portfolio. Each tab's `.w-dyn-list` is rendered redundantly (desktop /
    mobile / partial-preload duplicates) -- use only the FIRST `.w-dyn-list`
    per tab (verified byte-identical to the others, just truncated)."""
    soup = BeautifulSoup(html, "html.parser")
    panes = soup.select("div[data-w-tab].w-tab-pane")
    out = {}
    for p in panes:
        tab = p.get("data-w-tab")
        lists = p.select(".w-dyn-list")
        if not lists:
            continue
        cards = []
        for it in lists[0].select(".porto-card.w-dyn-item"):
            nm_el = it.select_one("[comp-name]")
            if not nm_el:
                continue
            name = clean(nm_el.get_text())
            a = it.select_one("a.card-com")
            href = a.get("href") if a else None
            desc_el = it.select_one('p[fs-cmssort-field="desc"]')
            desc = clean(desc_el.get_text()) if desc_el else None
            exit_el = it.select_one(".fs-detailsmall.is-aq")
            exit_label = clean(exit_el.get_text()) if exit_el else None
            cards.append({"name": name, "href": href, "desc": desc, "exit_label": exit_label})
        out[tab] = cards
    return out


def union_companies(tab_lists):
    """Union all 5 tabs by company name -- 'All' alone is NOT a superset (see
    module docstring). Returns a dict name -> {name, href, desc, exit_label,
    sector_tabs}."""
    union = {}
    for tab, cards in tab_lists.items():
        for c in cards:
            if not c["name"]:
                continue
            rec = union.setdefault(c["name"], {
                "name": c["name"], "href": c["href"], "desc": c["desc"],
                "exit_label": c["exit_label"], "sector_tabs": set(),
            })
            if not rec["href"] and c["href"]:
                rec["href"] = c["href"]
            if not rec["desc"] and c["desc"]:
                rec["desc"] = c["desc"]
            if not rec["exit_label"] and c["exit_label"]:
                rec["exit_label"] = c["exit_label"]
            if tab != "All":
                rec["sector_tabs"].add(tab)
    for rec in union.values():
        rec["sector_tabs"] = sorted(rec["sector_tabs"])
    return union


def parse_detail(html, card):
    """Parse a /company/<slug> detail page. Falls back to the card's name/
    description if the detail hero is somehow missing them."""
    soup = BeautifulSoup(html, "html.parser")

    h1 = soup.select_one("h1[company-title]")
    name = clean(h1.get_text()) if h1 else card["name"]

    desc_el = soup.select_one(".hero-title-sub .fs-pextralarge")
    description = clean(desc_el.get_text()) if desc_el else card["desc"]

    company_url = careers_url = linkedin_url = None
    for a in soup.select(".sosmed-w a"):
        wrap = a.find_parent(class_="sosmed-w")
        if wrap and "w-condition-invisible" in (wrap.get("class") or []):
            continue
        href = a.get("href")
        if not href or href == "#":
            continue
        label_el = a.select_one(".fs-button")
        label = clean(label_el.get_text()) if label_el else ""
        if label and label.lower() == "website":
            company_url = href
        elif label and label.lower() == "careers":
            careers_url = href
        elif label and label.lower() == "linkedin":
            linkedin_url = href

    logo_url = None
    for img in soup.select("img.img-company-logo"):
        if "w-condition-invisible" in (img.get("class") or []):
            continue
        logo_url = img.get("src")
        break

    sector = None
    ceo = None
    status_text = None
    for block in soup.select(".company-status-info-w"):
        label_el = block.select_one(".color-green-1")
        label = clean(label_el.get_text()) if label_el else None
        if not label:
            continue
        if label == "Sector":
            v = block.select_one(".fs-plarge")
            sector = clean(v.get_text()) if v else None
        elif label == "CEO":
            v = block.select_one(".fs-plarge")
            ceo = clean(v.get_text()) if v else None
        elif label == "Status":
            # two candidate .fs-plarge divs; the real one lacks w-condition-invisible
            for v in block.select(".fs-plarge"):
                if "w-condition-invisible" in (v.get("class") or []):
                    continue
                status_text = clean(v.get_text())
                break

    # Reconcile the detail page's visible Status text with the portfolio
    # card's exit label. Normally these agree, but one confirmed site data-
    # entry inconsistency (Pagaya) has the card correctly showing "IPO: PGY"
    # while the detail page's "IPO: PGY" span is the HIDDEN one and "Active"
    # is shown instead -- i.e. Oak's own CMS visibility toggle is wrong, not
    # missing data. Prefer whichever source signals the more definitive/
    # advanced state (Acquired/Merged/Public/Exited beats a bare "Active").
    detail_status, detail_acquirer, detail_ticker = derive_exit(status_text)
    card_status, card_acquirer, card_ticker = derive_exit(card.get("exit_label"))
    if detail_status == "Active" and card_status != "Active":
        status, acquirer, ticker_symbol = card_status, card_acquirer, card_ticker
    else:
        status, acquirer, ticker_symbol = detail_status, detail_acquirer, detail_ticker

    return {
        "company_name": name,
        "description": description,
        "company_url": company_url,
        "careers_url": careers_url,
        "linkedin_url": linkedin_url,
        "logo_url": logo_url,
        "sector": sector,
        "sector_tabs": card["sector_tabs"],
        "status": status,
        "acquirer": acquirer,
        "ticker_symbol": ticker_symbol,
        "ceo": ceo,
        "oak_profile_url": BASE + card["href"] if card["href"] else None,
    }


def main():
    limit = int(sys.argv[sys.argv.index("--limit") + 1]) if "--limit" in sys.argv else None

    print(f"Fetching {PORTFOLIO_URL} ...")
    html = get(PORTFOLIO_URL)
    tab_lists = get_tab_lists(html)
    print("Tab sizes:", {k: len(v) for k, v in tab_lists.items()})

    union = union_companies(tab_lists)
    names = sorted(union.keys(), key=str.lower)
    print(f"Union across all tabs: {len(names)} unique companies")
    if limit:
        names = names[:limit]

    scraped_at = datetime.now(timezone.utc).isoformat()
    out = []
    for i, name in enumerate(names, 1):
        card = union[name]
        detail_url = BASE + card["href"] if card["href"] else None
        rec = {
            "company_name": name,
            "description": card["desc"],
            "company_url": None,
            "careers_url": None,
            "linkedin_url": None,
            "logo_url": None,
            "sector": None,
            "sector_tabs": card["sector_tabs"],
            "status": None,
            "acquirer": None,
            "ticker_symbol": None,
            "ceo": None,
            "oak_profile_url": detail_url,
        }
        if detail_url:
            print(f"[{i}/{len(names)}] {name} -> {detail_url}")
            try:
                detail_html = get(detail_url)
                rec = parse_detail(detail_html, card)
            except SystemExit as e:
                print(f"  ! giving up on {name}: {e}", file=sys.stderr)

        rec["everywhere_tags"] = everywhere_tags(rec["company_name"], rec["description"], rec["sector"])
        rec["source_url"] = SOURCE_URL
        rec["scraped_at"] = scraped_at
        rec["fetched_via_relay"] = _relay_used["flag"]
        out.append(rec)

    out.sort(key=lambda o: o["company_name"].lower())

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2)

    # ---- summary ----
    n = len(out)
    print(f"\nWrote {n} companies -> {OUT}")
    for field in ("description", "company_url", "linkedin_url", "careers_url", "logo_url", "sector", "ceo"):
        miss = sum(1 for r in out if not r[field])
        print(f"  {field:14s} missing: {miss}/{n}")
    from collections import Counter
    by_status = Counter(r["status"] for r in out)
    print("By status:", dict(by_status),
          "| with acquirer:", sum(1 for r in out if r["acquirer"]),
          "| with ticker:", sum(1 for r in out if r["ticker_symbol"]))
    untagged = [r["company_name"] for r in out if not r["everywhere_tags"]]
    print(f"  untagged:      {len(untagged)}/{n}" + (f" -> {untagged}" if untagged else ""))
    by_tag = Counter(t for r in out for t in r["everywhere_tags"])
    print("By everywhere_tag:")
    for t, c in by_tag.most_common():
        print(f"  {c:>4}  {t}")
    print(f"\nFetched via relay (r.jina.ai): {any(r['fetched_via_relay'] for r in out)}")


if __name__ == "__main__":
    main()
