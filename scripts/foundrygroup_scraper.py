# AUTO-GENERATED scraper (Claude API) — passed static guard + sandboxed
# validation before commit. Regenerate rather than hand-edit heavily.
"""
Foundry Group portfolio scraper.

Scrapes portfolio companies from https://foundrygroup.com/portfolio
Fetches individual company detail pages to enrich descriptions.
"""

import re
import time
from typing import Any
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup


def scrape() -> list[dict[str, Any]]:
    """
    Scrape Foundry Group portfolio companies.
    
    Returns:
        List of portfolio company dictionaries with rich descriptions.
    """
    portfolio_url = "https://foundrygroup.com/portfolio"
    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    })
    
    try:
        response = session.get(portfolio_url, timeout=20)
        response.raise_for_status()
    except requests.RequestException:
        return []
    
    soup = BeautifulSoup(response.text, 'html.parser')
    
    companies = []
    seen_names = set()
    
    # Find all portfolio cards with data-bucket="company"
    cards = soup.select('#pf-cards .card[data-bucket="company"]')
    
    for idx, card in enumerate(cards):
        try:
            # Extract company name from data-name attribute
            company_name_raw = card.get('data-name', '')
            if not company_name_raw:
                continue
            
            # Normalize the name (title case)
            company_name = ' '.join(word.capitalize() for word in company_name_raw.split())
            
            # Deduplicate by name
            if company_name.lower() in seen_names:
                continue
            seen_names.add(company_name.lower())
            
            # Extract company URL from href
            company_url = None
            if card.name == 'a':
                company_url = card.get('href', '')
                if company_url and not company_url.startswith('http'):
                    company_url = urljoin(portfolio_url, company_url)
            
            # Extract location from .loc span
            location = None
            loc_span = card.select_one('.meta .loc')
            if loc_span:
                location = loc_span.get_text(strip=True)
            
            # Extract logo image URL to construct potential detail page URL
            img = card.select_one('img')
            detail_page_url = None
            description = None
            
            if img:
                img_src = img.get('src', '')
                # Image paths are like /portfolio/company-slug/logo.png
                match = re.search(r'/portfolio/([^/]+)/', img_src)
                if match:
                    company_slug = match.group(1)
                    # Try to fetch the detail page at /portfolio/company-slug/
                    detail_page_url = f"https://foundrygroup.com/portfolio/{company_slug}/"
                    
                    # Polite rate limiting
                    if idx > 0:
                        time.sleep(0.3)
                    
                    try:
                        detail_response = session.get(detail_page_url, timeout=20)
                        if detail_response.status_code == 200:
                            detail_soup = BeautifulSoup(detail_response.text, 'html.parser')
                            
                            # Try to extract description from common locations
                            # Look for meta description
                            meta_desc = detail_soup.find('meta', attrs={'name': 'description'})
                            if meta_desc and meta_desc.get('content'):
                                description = meta_desc.get('content').strip()
                            
                            # Look for article or main content
                            if not description:
                                article = detail_soup.find('article')
                                if article:
                                    # Get first paragraph
                                    first_p = article.find('p')
                                    if first_p:
                                        description = first_p.get_text(strip=True)
                            
                            # Look for .content or .description class
                            if not description:
                                content_div = detail_soup.select_one('.content, .description, .about')
                                if content_div:
                                    first_p = content_div.find('p')
                                    if first_p:
                                        description = first_p.get_text(strip=True)
                            
                            # Try main element
                            if not description:
                                main = detail_soup.find('main')
                                if main:
                                    first_p = main.find('p')
                                    if first_p:
                                        description = first_p.get_text(strip=True)
                        else:
                            # Detail page doesn't exist, use company website for description
                            if company_url and company_url.startswith('http'):
                                time.sleep(0.3)
                                try:
                                    company_response = session.get(company_url, timeout=20)
                                    if company_response.status_code == 200:
                                        company_soup = BeautifulSoup(company_response.text, 'html.parser')
                                        
                                        # Try meta description first
                                        meta_desc = company_soup.find('meta', attrs={'name': 'description'})
                                        if meta_desc and meta_desc.get('content'):
                                            description = meta_desc.get('content').strip()
                                        
                                        # Try og:description
                                        if not description:
                                            og_desc = company_soup.find('meta', attrs={'property': 'og:description'})
                                            if og_desc and og_desc.get('content'):
                                                description = og_desc.get('content').strip()
                                except:
                                    pass
                    except:
                        pass
            
            # If still no description, try fetching from company website
            if not description and company_url and company_url.startswith('http'):
                if idx > 0:
                    time.sleep(0.3)
                try:
                    company_response = session.get(company_url, timeout=20)
                    if company_response.status_code == 200:
                        company_soup = BeautifulSoup(company_response.text, 'html.parser')
                        
                        # Try meta description first
                        meta_desc = company_soup.find('meta', attrs={'name': 'description'})
                        if meta_desc and meta_desc.get('content'):
                            description = meta_desc.get('content').strip()
                        
                        # Try og:description
                        if not description:
                            og_desc = company_soup.find('meta', attrs={'property': 'og:description'})
                            if og_desc and og_desc.get('content'):
                                description = og_desc.get('content').strip()
                except:
                    pass
            
            # Build company record
            company = {
                'company_name': company_name,
                'company_url': company_url,
                'description': description,
                'location': location,
                'founders': [],
                'sectors': [],
                'stage': None,
                'status': 'Active',
                'everywhere_tags': [],
                'source_url': portfolio_url
            }
            
            companies.append(company)
            
        except Exception:
            continue
    
    return companies


# --- auto-appended runner (trusted template, not LLM output) -----------------
if __name__ == "__main__":
    import json as _json, os as _os, sys as _sys
    from datetime import datetime as _dt, timezone as _tz
    _records = scrape()
    _now = _dt.now(_tz.utc).replace(microsecond=0).isoformat()
    for _r in _records:
        _r.setdefault("everywhere_tags", [])
        _r.setdefault("scraped_at", _now)
    _out = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)),
                         "..", "data", "foundrygroup_companies.json")
    with open(_out, "w") as _f:
        _json.dump(_records, _f, indent=2, ensure_ascii=False)
        _f.write("\n")
    print(f"wrote {len(_records)} records")
