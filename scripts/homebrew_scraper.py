# AUTO-GENERATED scraper (Claude API) — passed static guard + sandboxed
# validation before commit. Regenerate rather than hand-edit heavily.
import requests
from bs4 import BeautifulSoup
from typing import Any
import time

def scrape() -> list[dict[str, Any]]:
    """
    Scrape portfolio companies from Homebrew VC.
    
    Returns:
        List of dictionaries containing portfolio company information.
    """
    source_url = "https://homebrew.co#Portfolio"
    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    })
    
    companies = []
    seen_names = set()
    
    try:
        response = session.get("https://homebrew.co", timeout=20)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Find all portfolio company items
        # First, get featured companies (larger grid items)
        featured_items = soup.find_all('div', class_='featured-collection-item')
        for item in featured_items:
            link_tag = item.find('a', class_='collection-link')
            if not link_tag:
                continue
            
            company_url = link_tag.get('href', '').strip()
            if not company_url or company_url == '#':
                company_url = None
            
            name_tag = item.find('h3', class_='heading-3')
            company_name = name_tag.get_text(strip=True) if name_tag else None
            
            if company_name and company_name not in seen_names:
                seen_names.add(company_name)
                companies.append({
                    'company_name': company_name,
                    'company_url': company_url,
                    'description': None,
                    'everywhere_tags': [],
                    'source_url': source_url
                })
        
        # Get regular portfolio companies (smaller grid items with descriptions)
        portfolio_items = soup.find_all('div', class_='collection-item')
        for item in portfolio_items:
            link_tag = item.find('a', class_='collection-link')
            if not link_tag:
                continue
            
            company_url = link_tag.get('href', '').strip()
            if not company_url or company_url == '#':
                company_url = None
            
            name_tag = item.find('h4', class_='portfolio-name')
            company_name = name_tag.get_text(strip=True) if name_tag else None
            
            desc_tag = item.find('p', class_='description')
            description = desc_tag.get_text(strip=True) if desc_tag else None
            
            if company_name and company_name not in seen_names:
                seen_names.add(company_name)
                companies.append({
                    'company_name': company_name,
                    'company_url': company_url,
                    'description': description,
                    'everywhere_tags': [],
                    'source_url': source_url
                })
        
        # Also scrape exits section to avoid missing any data
        # Exits are in a separate section but don't have URLs or descriptions
        exits_section = False
        for heading in soup.find_all('h2'):
            if heading.get_text(strip=True) == 'Exits':
                exits_section = True
                # Find the next portfolio grid after this heading
                next_grid = heading.find_next('div', class_='portfolio-grid')
                if next_grid:
                    exit_items = next_grid.find_all('div', class_='collection-item')
                    for item in exit_items:
                        name_tag = item.find('h4', class_='portfolio-name')
                        company_name = name_tag.get_text(strip=True) if name_tag else None
                        
                        desc_tag = item.find('p', class_='description')
                        description = desc_tag.get_text(strip=True) if desc_tag else None
                        
                        if company_name and company_name not in seen_names:
                            seen_names.add(company_name)
                            companies.append({
                                'company_name': company_name,
                                'company_url': None,
                                'description': description,
                                'everywhere_tags': [],
                                'source_url': source_url
                            })
                break
        
    except requests.exceptions.RequestException as e:
        # Return empty list on network errors
        return []
    
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
                         "..", "data", "homebrew_companies.json")
    with open(_out, "w") as _f:
        _json.dump(_records, _f, indent=2, ensure_ascii=False)
        _f.write("\n")
    print(f"wrote {len(_records)} records")
