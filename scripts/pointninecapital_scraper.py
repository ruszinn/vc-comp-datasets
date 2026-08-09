"""Point Nine Capital — pointnine.com/companies

Hand-written replacement for the auto-generated scraper, which produced 25
records whose every field was junk. Two failures, both worth recording so the
factory prompt can learn from them:

  1. It called get_text() on the row container. The site renders name, stage,
     year, flag, city, country and status as SEPARATE elements, so get_text()
     glued them into "15Five2013<flag>San FranciscoUSAActive". The fix is to read
     `stripped_strings` (each element's text as its own item) instead.
  2. When the listing gave it nothing usable it fetched each COMPANY'S OWN
     homepage for description and sectors — which is how "Download logo pack"
     and "There was an error while loading." ended up in the dataset. This
     scraper reads pointnine.com and nothing else; the repo's rule is the firm's
     own pages only.

It also missed 7 of 8 pages. The list is server-rendered per page via
?<hash>_page=N (Finsweet CMS list), so pagination is a plain URL walk.

Sector and country values are validated against the vocabularies the site
publishes in its own filter panel. That is what stops navigation labels being
mistaken for sectors: a string is a sector only if Point Nine says it is one.

Schema is site-tailored, per repo convention — no `founders` field, because the
listing does not publish founders, and no `profile_url`, because there are no
per-company profile pages (the link goes straight to the company's website).
"""
from __future__ import annotations

import re
import time
from urllib.parse import urljoin, urlsplit

import requests
from bs4 import BeautifulSoup

PORTFOLIO_URL = "https://www.pointnine.com/companies"
_UA = {"User-Agent": "vc-comps-pipeline/1.0 (+portfolio dataset; contact via repo)"}
_MAX_PAGES = 40          # generous ceiling; the walk stops when a page repeats
_SLEEP = 0.6

# --- the site's OWN filter vocabularies (pointnine.com/companies filter panel).
# Membership here is what makes a string a sector rather than a nav label.
_SECTORS = {
    "AdTech", "AI", "AR/VR", "B2B Marketplace", "BI Analytics",
    "Climate & Energy", "Computer Vision", "Consumer", "Creative", "CRM",
    "Crypto", "Deep Tech", "Dev Tools & Infra", "Ecommerce", "Education",
    "Enterprise", "Enterprise Software", "Finance", "Fintech", "Food",
    "Foundation Model", "Hardware", "Healthcare", "HR", "Legal", "Logistics",
    "Manufacturing", "Marketing", "Marketplace", "Mobile", "Open Source",
    "Physical AI", "PLG", "Productivity", "SaaS", "SMB SaaS", "Space",
    "Travel", "Travel & Mobility", "Vertical Software",
}
_COUNTRIES = {
    "Armenia", "Australia", "Austria", "Belgium", "Canada", "Denmark",
    "Finland", "France", "Germany", "Greece", "Hungary", "Ireland", "Italy",
    "Japan", "Latvia", "Netherlands", "New Zealand", "Nigeria", "Norway",
    "Peru", "Poland", "Portugal", "Romania", "Serbia", "Slovakia", "Slovenia",
    "Spain", "Sweden", "Switzerland", "Turkey", "UAE", "UK", "Ukraine", "USA",
}
_STAGES = {"Pre-seed", "Seed", "Series A", "Series B", "Series C",
           "Series D", "Series E", "Series F", "Later-stage"}
_STATUSES = {"Active", "Acquired", "RIP"}

_FLAG = re.compile(r"^[\U0001F1E6-\U0001F1FF]{2}$")
_YEAR = re.compile(r"^(19|20)\d{2}$")
_PAGE_PARAM = re.compile(r"[?&]([0-9a-f]{6,}_page)=(\d+)")


def _fetch(session: requests.Session, url: str) -> str | None:
    for attempt in range(3):
        try:
            r = session.get(url, timeout=25)
            if r.status_code == 200:
                return r.text
        except requests.RequestException:
            pass
        time.sleep(1.5 * (attempt + 1))
    return None


def _row_anchors(soup: BeautifulSoup) -> list:
    """Company rows are anchors whose pieces end with a status word and carry a
    flag emoji. Anchored on CONTENT, not Webflow's hashed class names, which
    change whenever the site is republished."""
    out = []
    for a in soup.find_all("a", href=True):
        parts = [s.strip() for s in a.stripped_strings if s.strip()]
        if len(parts) < 4:
            continue
        if parts[-1] in _STATUSES and any(_FLAG.match(p) for p in parts):
            out.append((a, parts))
    return out


def _parse_row(anchor, parts: list[str], page_url: str) -> dict | None:
    """parts looks like ['15Five','Seed','2013','<flag>','San Francisco','USA','Active'].
    Each piece is identified by what it IS, not by position, so an extra or
    missing element shifts nothing."""
    status = parts[-1]
    name = parts[0]
    if not name or name in _STATUSES:
        return None

    year = next((int(p) for p in parts if _YEAR.match(p)), None)
    country = next((p for p in reversed(parts) if p in _COUNTRIES), None)

    # City = the piece sitting between the flag and the country, if present.
    city = None
    try:
        fi = next(i for i, p in enumerate(parts) if _FLAG.match(p))
        tail = parts[fi + 1:-1]
        if country and country in tail:
            tail = tail[:tail.index(country)]
        city = " ".join(tail).strip() or None
    except StopIteration:
        pass

    href = (anchor.get("href") or "").strip()
    company_url = urljoin(page_url, href) if href else None
    if company_url and not urlsplit(company_url).scheme.startswith("http"):
        company_url = None

    # The row container holds the logo, the description and the sector chips.
    row = anchor.parent
    for _ in range(3):
        if row is None or row.find("img"):
            break
        row = row.parent
    row = row if row is not None else anchor

    logo_url = None
    img = row.find("img", alt=True)
    if img:
        logo_url = img.get("src") or None
        alt = (img.get("alt") or "").strip()
        # "15Five logo" is the cleanest name the page publishes — prefer it.
        if alt.lower().endswith(" logo"):
            candidate = alt[:-5].strip()
            if candidate:
                name = candidate

    row_parts = [s.strip() for s in row.stripped_strings if s.strip()]
    sectors = []
    for p in row_parts:
        if p in _SECTORS and p not in sectors:
            sectors.append(p)

    # Stage lives in the ROW, not the anchor — the anchor carries only
    # name/year/flag/city/country/status. Checked against the site's own stage
    # vocabulary either way, so position never matters.
    stage = next((p for p in parts if p in _STAGES),
                 next((p for p in row_parts if p in _STAGES), None))

    # Description = the longest prose block in the row that is not a chip value.
    known = _SECTORS | _COUNTRIES | _STAGES | _STATUSES | {"true", "false"}
    prose = [p for p in row_parts
             if p not in known and len(p) > 60 and " " in p and p != name]
    description = max(prose, key=len) if prose else None

    return {
        "company_name": name,
        "company_url": company_url,
        "description": description,
        "sectors": sectors,
        "stage": stage,
        "status": status,
        "city": city,
        "country": country,
        "founded_year": year,
        "logo_url": logo_url,
        "everywhere_tags": [],
        "source_url": PORTFOLIO_URL,
    }


def _next_page_url(soup: BeautifulSoup, current: str) -> str | None:
    """Follow pagination. The page param is a hashed Finsweet name
    (?f34b63bd_page=2), so it is read off the markup rather than hard-coded —
    republishing the site changes that hash.

    Pick the next page NUMBER, not the first matching link: page 2 onwards also
    carries a link back to page 1, and taking that first match ended the walk
    after two pages (49 records instead of the full list).
    """
    here = _PAGE_PARAM.search(current)
    cur_n = int(here.group(2)) if here else 1

    best: tuple[int, str] | None = None
    for a in soup.find_all("a", href=True):
        m = _PAGE_PARAM.search(a["href"])
        if not m:
            continue
        n = int(m.group(2))
        if n <= cur_n:
            continue
        if best is None or n < best[0]:
            best = (n, urljoin(current, a["href"]))
    return best[1] if best else None


def scrape() -> list[dict]:
    session = requests.Session()
    session.headers.update(_UA)

    records: list[dict] = []
    seen: set[str] = set()
    url = PORTFOLIO_URL
    visited: set[str] = set()

    for _ in range(_MAX_PAGES):
        if not url or url in visited:
            break
        visited.add(url)

        html = _fetch(session, url)
        if not html:
            break
        soup = BeautifulSoup(html, "html.parser")

        rows = _row_anchors(soup)
        if not rows:
            break

        new_on_page = 0
        for anchor, parts in rows:
            rec = _parse_row(anchor, parts, url)
            if not rec:
                continue
            key = (rec["company_name"] or "").lower()
            if not key or key in seen:
                continue
            seen.add(key)
            records.append(rec)
            new_on_page += 1

        if new_on_page == 0:          # same page served again: stop
            break

        url = _next_page_url(soup, url)
        time.sleep(_SLEEP)

    return records


# --- auto-appended runner (trusted template, not LLM output) -----------------
if __name__ == "__main__":
    import argparse as _argparse
    import json as _json, os as _os
    from datetime import datetime as _dt, timezone as _tz

    _ap = _argparse.ArgumentParser()
    _ap.add_argument("--limit", type=int, default=None)
    _args = _ap.parse_args()

    _records = scrape()
    if _args.limit:
        _records = _records[: _args.limit]
    _now = _dt.now(_tz.utc).replace(microsecond=0).isoformat()
    for _r in _records:
        _r.setdefault("everywhere_tags", [])
        _r.setdefault("scraped_at", _now)
    _out = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)),
                         "..", "data", "pointninecapital_companies.json")
    with open(_out, "w") as _f:
        _json.dump(_records, _f, indent=2, ensure_ascii=False)
        _f.write("\n")
    print(f"wrote {len(_records)} records")
