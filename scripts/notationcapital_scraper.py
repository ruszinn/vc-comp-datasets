# AUTO-GENERATED scraper (Claude API) — passed static guard + sandboxed
# validation before commit. Regenerate rather than hand-edit heavily.
import requests
import json
import re
import time
from bs4 import BeautifulSoup
from typing import List, Dict
from urllib.parse import urljoin

def scrape() -> List[Dict]:
    """
    Scrape portfolio companies from Notation Capital.
    Returns a list of company dicts with available metadata.
    """
    companies = []
    seen = set()
    
    portfolio_url = "https://notationcapital.com/companies/#alice"
    
    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    })
    
    try:
        resp = session.get(portfolio_url, timeout=20)
        resp.raise_for_status()
    except Exception:
        return []
    
    soup = BeautifulSoup(resp.text, 'html.parser')
    
    # Find all company rows - these contain the structured data
    company_rows = soup.find_all('div', class_='company-row')
    
    for row in company_rows:
        try:
            # Extract company ID from the row
            company_id = row.get('id')
            if not company_id:
                continue
            
            # Skip duplicates
            if company_id in seen:
                continue
            seen.add(company_id)
            
            # Get the company link and info
            company_div = row.find('div', class_='company')
            if not company_div:
                continue
            
            link_elem = company_div.find('a', class_='toggle-link')
            if not link_elem:
                continue
            
            # Extract company name
            name_span = link_elem.find('span', class_='company-name')
            company_name = name_span.get_text(strip=True) if name_span else None
            
            if not company_name:
                continue
            
            # Extract description
            description_span = link_elem.find('span', class_='listing-text')
            description = description_span.get_text(strip=True) if description_span else None
            
            # Extract company URL
            company_url = link_elem.get('href', '').strip()
            if not company_url or not company_url.startswith('http'):
                company_url = None
            
            # Extract sectors from row classes
            sectors = []
            classes = row.get('class', [])
            sector_mapping = {
                'type-finance': 'Better Money',
                'type-blockchain': 'Blockchain',
                'type-climate': 'Climate',
                'type-creativity': 'Creativity',
                'type-developer-tools': 'Developer Tools',
                'type-health': 'Health',
                'type-infrastructure': 'Infrastructure',
                'type-education': 'Learning',
                'type-logistics': 'Logistics',
                'type-ml': 'Machine Learning',
                'type-open-source': 'Open Source',
                'type-productivity': 'Productivity',
                'type-science': 'Science',
                'type-security': 'Security'
            }
            
            for cls in classes:
                if cls in sector_mapping:
                    sectors.append(sector_mapping[cls])
            
            # Determine status based on is-inactive class
            status = 'inactive' if 'is-inactive' in classes else 'active'
            
            company_dict = {
                'company_name': company_name,
                'company_url': company_url,
                'description': description,
                'sectors': sectors,
                'founders': [],
                'stage': None,
                'status': status,
                'profile_url': None,
                'everywhere_tags': [],
                'source_url': portfolio_url
            }
            
            companies.append(company_dict)
            time.sleep(0.3)
        
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
                         "..", "data", "notationcapital_companies.json")
    with open(_out, "w") as _f:
        _json.dump(_records, _f, indent=2, ensure_ascii=False)
        _f.write("\n")
    print(f"wrote {len(_records)} records")
