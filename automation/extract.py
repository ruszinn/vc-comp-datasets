"""Web extractor — turn a firm into a company list, using only its own site.

Two jobs, both requests + BeautifulSoup only (no headless browser, so the whole
pipeline stays a light cron and nothing is fetched twice):

  resolve_portfolio_url(homepage) -> url | None
      find the firm's portfolio/companies page from its homepage.

  extract_companies(url) -> (companies, confidence)
      pull a company list off that page and score how sure we are.

The scoring functions are pure (HTML in, number out) so they're unit-tested
without the network; only `resolve_portfolio_url`/`extract_companies` fetch.

Honest scope: generic extraction handles server-rendered portfolio pages. A
client-rendered (heavy-JS) site returns little to requests and will come back
low-confidence — the pipeline flags those `needs-scraper` for a bespoke
scripts/<slug>_scraper.py, exactly like the repo's normal workflow.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable, Optional
from urllib.parse import urljoin, urlsplit

import requests
from bs4 import BeautifulSoup

_UA = {"User-Agent": "vc-comps-pipeline/1.0 (+github.com/michaeleverywhere/vc-comp)"}

_KEYWORDS = (
    "portfolio", "companies", "investments", "portfolio-companies",
    "our-companies", "our companies", "founders", "rebels",
)
_GUESS_PATHS = (
    "/portfolio", "/portfolio/", "/companies", "/companies/",
    "/investments", "/investments/", "/portfolio-companies",
    "/our-companies", "/founders",
)
_SOCIAL = {
    "twitter.com", "x.com", "linkedin.com", "facebook.com", "instagram.com",
    "youtube.com", "medium.com", "github.com", "crunchbase.com", "apple.com",
    "google.com", "spotify.com", "substack.com", "t.co", "tiktok.com",
}
_MIN_COMPANIES = 15  # fewer external links than this => probably not a portfolio


def fetch(url: str, timeout: int = 20) -> Optional[str]:
    try:
        r = requests.get(url, headers=_UA, timeout=timeout)
    except requests.RequestException:
        return None
    if r.status_code == 200 and "html" in r.headers.get("content-type", ""):
        return r.text
    return None


def _reg_domain(netloc: str) -> str:
    netloc = netloc.lower().split(":")[0]
    return netloc[4:] if netloc.startswith("www.") else netloc


def _external_company_links(html: str, page_url: str) -> list[dict]:
    """Anchors pointing to external, non-social domains with visible text —
    the generic signal for 'a portfolio company'. One record per distinct domain."""
    soup = BeautifulSoup(html, "html.parser")
    home = _reg_domain(urlsplit(page_url).netloc)
    seen: set[str] = set()
    out: list[dict] = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        dom = _reg_domain(urlsplit(urljoin(page_url, href)).netloc)
        if not dom or dom == home or dom in _SOCIAL or dom in seen:
            continue
        name = " ".join(a.get_text(" ", strip=True).split())
        if not name or len(name) > 80:
            continue
        seen.add(dom)
        out.append({"company_name": name,
                    "company_url": urljoin(page_url, href)})
    return out


def score_portfolio_html(html: str, page_url: str) -> int:
    """How many distinct external company domains this page links to."""
    return len(_external_company_links(html, page_url))


def candidate_urls(homepage: str, html: str) -> list[str]:
    """Ordered URLs to check: keyword nav links first, then guessed paths."""
    base = homepage if homepage.startswith("http") else "https://" + homepage
    ordered, seen = [], set()

    def add(u: str) -> None:
        u = u.rstrip("/") or u
        if u not in seen:
            seen.add(u); ordered.append(u)

    soup = BeautifulSoup(html, "html.parser")
    for a in soup.find_all("a", href=True):
        low = a["href"].lower()
        if any(k in low for k in _KEYWORDS):
            add(urljoin(base, a["href"]))
    for p in _GUESS_PATHS:
        add(urljoin(base, p))
    return ordered


def resolve_portfolio_url(
    homepage: str,
    fetcher: Optional[Callable[[str], Optional[str]]] = None,
    min_companies: int = _MIN_COMPANIES,
) -> Optional[str]:
    """Best portfolio URL for a homepage, or None. Picks the reachable candidate
    that links out to the most companies (injectable `fetcher` for testing)."""
    fetcher = fetcher or fetch
    base = homepage if homepage.startswith("http") else "https://" + homepage
    home_html = fetcher(base)
    if not home_html:
        return None
    best_url, best = None, 0
    for url in candidate_urls(base, home_html):
        html = fetcher(url)
        if not html:
            continue
        s = score_portfolio_html(html, url)
        if s > best:
            best_url, best = url, s
    return best_url if best >= min_companies else None


def extract_companies(
    url: str,
    fetcher: Optional[Callable[[str], Optional[str]]] = None,
) -> tuple[list[dict], float]:
    """Generic-extract companies from a portfolio page. Returns (records, confidence).

    Records match the repo's minimal generic schema; confidence scales with how
    many distinct external company links the page yields (>= _MIN_COMPANIES -> 1.0).
    """
    fetcher = fetcher or fetch
    html = fetcher(url)
    if not html:
        return [], 0.0
    links = _external_company_links(html, url)
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    records = [{
        "company_name": c["company_name"],
        "company_url": c["company_url"],
        "description": None,
        "everywhere_tags": [],
        "source_url": url,
        "scraped_at": now,
    } for c in links]
    confidence = min(1.0, len(links) / _MIN_COMPANIES) if links else 0.0
    return records, round(confidence, 2)
