#!/usr/bin/env python3
"""
Menlo Ventures portfolio scraper -> menlo_companies.json

Scrapes Menlo Ventures' portfolio (https://menlovc.com/portfolio/) into a JSON
file. The whole portfolio is server-rendered into ONE page (WordPress + Tailwind);
each company is a `.js-company-block` with an optional expandable detail card that
holds the website, socials, a milestones timeline, leadership, and Menlo partners.
A single HTTP request gets everything -- no API key, no per-company crawling, no LLM.

requirements:
    pip install requests beautifulsoup4

usage:
    python3 menlo_scraper.py          # writes menlo_companies.json next to this file
"""

import json
import os
import re
import sys
import time
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

URL = "https://menlovc.com/portfolio/"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "data", "menlo_companies.json")
SOURCE_URL = "https://menlovc.com/portfolio/"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}
TIMEOUT = 45
RETRIES = 3

SOCIAL_HOSTS = ("linkedin.", "twitter.", "x.com", "instagram.", "facebook.", "github.", "youtube.")

# everywhere_tags keyword classifier (Menlo doesn't tag companies by sector in the
# page HTML, so tags are derived from name + description). Substrings, lowercased.
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
                             "rebate", " tax", "audit", "money management", "robo-advisor", "brokerage", "spend management"]),
    ("Web3 / Crypto", ["crypto", "blockchain", "web3", "token", "on-chain", "ethereum", "bitcoin", "decentral", "stablecoin", "nft"]),
    ("Gaming / Media / Entertainment", ["game", "gaming", "music", "video", "creator", "content", "publish",
                                        "entertain", "newsletter", "podcast", "film", "streaming", "social media", "media platform"]),
    ("Dev Tools / Cloud", ["developer", " api ", "apis", "api platform", "infrastructure", "database", "cloud",
                           "open source", "devops", "sdk", "kubernetes", "container", "observability", "deploy", "compute",
                           "storage", "serverless", "inference", "networking", "ethernet", "coding", "codebase", "low-code",
                           "no-code", "source code", "development platform", "incident", " sre", "voicemail", "communications",
                           "llm", "foundation model", "interpretability", "voice keyboard", "voice recognition", "frame relay"]),
    ("Data & Analytics", ["analytics", "business intelligence", "data platform", "data warehouse", "data lake",
                          "data pipeline", "insights", "dashboard", "experimentation", "decision intelligence", "data quality",
                          "analyz", "data curation", "quality management", "relationship intelligence"]),
    ("Future of Work", ["workforce", "hiring", "recruit", "employee", "productivity", "collaboration", "talent",
                        "workplace", "human resources", " hr ", "learning platform", "customer success", "customer service",
                        "customer support", "presales", " sales ", "onboarding", "workflow", "saas management", "ai assistant"]),
    ("Transportation / Mobility", ["mobility", "vehicle", "transport", "autonomous", "fleet", "driving", "aviation",
                                   "aircraft", "electric vehicle", "scooter", " bike", "boat", "watercraft", "rideshar"]),
    ("Logistics / Supply Chain", ["logistics", "supply chain", "supply and demand", "freight", "warehouse",
                                  "delivery", "procurement", "inventory", "fulfillment", "shipping"]),
    ("PropTech", ["real estate", "property", "housing", "mortgage", "rental", "construction", "tenant"]),
    ("CPG", ["beverage", "snack", "consumer packaged", "beauty", "cosmetic", "apparel", "grocery", "skincare",
             "eyewear", "glasses", "footwear", "pet sitter", "pet "]),
    ("Climate / Sustainability", ["climate", "carbon", "renewable", "solar", "battery", "sustainab", "emission",
                                  "clean energy", "ev charging", "electrif"]),
    ("RegTech/Gov/Legal", ["legal", "compliance", "government", "regulat", "law firm", "attorney"]),
    ("Deeptech / Robotics / AR/VR", ["robot", "hardware", "semiconductor", "chip", "drone", "aerospace",
                                     "augmented reality", "virtual reality", "satellite", "quantum", "sensor", "rfid", "wifi"]),
    ("Consumer", ["marketplace", "consumer", "shopping", "social network", "community", "app for", "app that",
                  "ecommerce", "e-commerce", "subscription", "retailer", "universit", "student", "education",
                  "learning", "fashion", "style companion", "dog and cat", "email service"]),
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


def first_img_url(img):
    for a in ("data-srcset", "srcset", "data-src", "src"):
        v = img.get(a)
        if v and "base64" not in v:
            return v.split()[0].strip()  # first URL of a srcset
    return None


def stage_to_type(stage):
    if not stage:
        return None
    s = stage.strip().lower()
    if s in ("seed", "pre-seed"):
        return s
    if s == "common":
        return "common"
    m = re.match(r"series\s+([a-z]+)$", s)
    if m:
        return "series-" + m.group(1)
    return None


def parse_milestones(detail):
    """Return list of (year:int|None, event:str) from the Milestones timeline."""
    if detail is None:
        return []
    years = detail.select(".portfolio-details-text.h6:not(.text-dark-blue)")
    events = detail.select(".portfolio-details-text.body-small:not(.text-dark-blue)")
    out = []
    for y, e in zip(years, events):
        yr = clean(y.get_text())
        ev = clean(e.get_text())
        if not ev:
            continue
        ev = re.sub(r"^[–-]\s*", "", ev)  # strip leading dash
        out.append((int(yr) if yr and yr.isdigit() else None, ev))
    return out


def parse_people(detail):
    """Return (founders, partners). Founders = leadership whose title has 'Founder'.
    Partners = Menlo team members (links to /team/)."""
    partners, founders = [], []
    if detail is None:
        return founders, partners
    for a in detail.select('a[href*="/team/"]'):
        nm = clean(a.get_text())
        if nm and nm not in partners:
            partners.append(nm)
    # leadership: name (h6) + title (body-small), both text-dark-blue, NOT inside /team/ link
    names = [s for s in detail.select(".portfolio-details-text.text-dark-blue.h6")
             if not s.find_parent("a", href=re.compile("/team/"))]
    titles = [s for s in detail.select(".portfolio-details-text.text-dark-blue.body-small")
              if not s.find_parent("a", href=re.compile("/team/"))]
    for nm_el, ti_el in zip(names, titles):
        nm, ti = clean(nm_el.get_text()), clean(ti_el.get_text()) or ""
        if nm and re.search(r"founder", ti, re.I) and nm not in founders:
            founders.append(nm)
    return founders, partners


def derive_status(milestones, status_text):
    """Return (status, exit_type, acquirer, ticker_symbol, exit_detail)."""
    # Prefer the milestones timeline; fall back to the summary status string.
    exit_event = None
    for yr, ev in milestones:
        if re.search(r"^(acquired by|merged with|ipo|public)\b", ev, re.I) or re.match(r"^[A-Z]{2,6}:\s*[A-Za-z.\-]+$", ev):
            exit_event = ev
    candidate = exit_event or (status_text or "")
    c = candidate.strip()
    if not c:
        return "Current", None, None, None, None

    m = re.search(r"^(?:Acquired by|Merged with)\s+(.*)$", c, re.I)
    if m:
        et = "Merged" if c.lower().startswith("merged") else "Acquired"
        return ("Merged" if et == "Merged" else "Acquired"), et, clean(m.group(1)), None, c
    tick = re.search(r"\b([A-Z]{2,6}):\s*([A-Za-z.\-]{1,8})\b", c)
    if "ipo" in c.lower() or tick:
        ticker = f"{tick.group(1)}: {tick.group(2)}" if tick else None
        return "Public", "IPO" if "ipo" in c.lower() else "Public", None, ticker, c
    return "Current", None, None, None, None


def everywhere_tags(name, description):
    text = f"{name or ''} {description or ''}".lower()
    tags = []
    for tag, kws in KEYWORD_TAGS:
        if any(kw in text for kw in kws) and tag not in tags:
            tags.append(tag)
    return tags[:4]


def parse(html):
    soup = BeautifulSoup(html, "html.parser")
    blocks = soup.select(".js-company-block")
    companies = []
    for b in blocks:
        name = clean(b.get("data-title")) or clean(
            b.select_one("h3").get_text() if b.select_one("h3") else None)
        if not name:
            continue
        summary = b.select_one(".js-company-card[tabindex]") or b
        desc_el = summary.select_one(".body-small:not(.wysiwyg-section)")
        description = clean(desc_el.get_text()) if desc_el else None
        status_el = summary.select_one(".wysiwyg-section")
        status_text = clean(status_el.get_text()) if status_el else None

        detail = b.select_one(".detail-portfolio-card")

        # website (eyebrow link), profile (View more), socials
        site = b.select_one(".portfolio-details-link[href]")
        company_url = site.get("href") if site else None
        prof = b.select_one('a[href*="/portfolio/"]')
        profile_url = prof.get("href") if prof else None
        socials = []
        for a in b.select("a[href]"):
            href = a.get("href") or ""
            if any(h in href for h in SOCIAL_HOSTS) and href not in socials:
                socials.append(href)

        # logo + hero image from lazy data-srcset (skip headshots & the *_graphic hero)
        logo_url = image_url = None
        for im in b.select("img"):
            u = first_img_url(im)
            if not u:
                continue
            alt = (im.get("alt") or "").lower()
            if "graphic" in u.lower() or "graphic" in alt:
                image_url = image_url or u
            elif "headshot" in alt or "/team/" in (im.find_parent("a").get("href") if im.find_parent("a") else ""):
                continue
            elif logo_url is None:
                logo_url = u

        milestones = parse_milestones(detail)
        founders, partners = parse_people(detail)
        year_founded = next((yr for yr, ev in milestones if re.search(r"founded", ev, re.I)), None)
        fp_year = fp_stage = None
        for yr, ev in milestones:
            m = re.search(r"partnered,?\s*(.*)$", ev, re.I)
            if m:
                fp_year = yr
                fp_stage = clean(m.group(1)) or None
                break
        status, exit_type, acquirer, ticker, exit_detail = derive_status(milestones, status_text)

        companies.append({
            "company_name": name,
            "description": description,
            "company_url": company_url,
            "company_profile_url": profile_url,
            "logo_url": logo_url,
            "image_url": image_url,
            "founders": founders,
            "partners": partners,
            "year_founded": year_founded,
            "first_partnered_year": fp_year,
            "first_partnered_stage": fp_stage,
            "initial_investment_type": stage_to_type(fp_stage),
            "status": status,
            "exit_type": exit_type,
            "exit_detail": exit_detail,
            "acquirer": acquirer,
            "ticker_symbol": ticker,
            "sectors": [],
            "everywhere_tags": everywhere_tags(name, description),
            "social_urls": socials,
            "source_url": SOURCE_URL,
            "scraped_at": datetime.now(timezone.utc).isoformat(),
        })
    return companies


def main():
    print(f"Fetching {URL}")
    companies = parse(get(URL))
    # de-dupe by normalized name, keep first
    seen, out = set(), []
    for c in companies:
        k = c["company_name"].strip().lower()
        if k in seen:
            print(f"  ! duplicate '{c['company_name']}' — keeping first", file=sys.stderr)
            continue
        seen.add(k)
        out.append(c)
    out.sort(key=lambda o: o["company_name"].lower())

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    from collections import Counter
    by_status = Counter(o["status"] for o in out)
    by_tag = Counter(t for o in out for t in o["everywhere_tags"])
    print(f"\nWrote {len(out)} companies -> {OUT}")
    print("By status:", dict(by_status))
    print("With founders:", sum(1 for o in out if o["founders"]),
          "| with partners:", sum(1 for o in out if o["partners"]),
          "| with website:", sum(1 for o in out if o["company_url"]),
          "| untagged:", sum(1 for o in out if not o["everywhere_tags"]))
    print("By everywhere_tag:")
    for t, c in by_tag.most_common():
        print(f"  {c:>4}  {t}")


if __name__ == "__main__":
    main()
