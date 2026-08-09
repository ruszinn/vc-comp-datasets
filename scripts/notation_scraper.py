# AUTO-GENERATED scraper (Claude API) — passed static guard + sandboxed
# validation before commit. Regenerate rather than hand-edit heavily.
import requests
import time
from bs4 import BeautifulSoup
from typing import List, Dict

def scrape() -> List[Dict]:
    """
    Scrape Notation Capital's portfolio companies from https://notation.vc/companies
    """
    portfolio_url = "https://notation.vc/companies"
    companies = []
    seen_names = set()
    
    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    })
    
    try:
        resp = session.get(portfolio_url, timeout=20)
        resp.raise_for_status()
    except Exception:
        return []
    
    soup = BeautifulSoup(resp.content, 'html.parser')
    
    # Find all company rows in the portfolio listing
    company_rows = soup.find_all('div', class_='company-row')
    
    for row in company_rows:
        try:
            company_div = row.find('div', class_='company')
            if not company_div:
                continue
            
            # Get the link element - both <a> and <span> variants exist
            link_elem = company_div.find(['a', 'span'], class_='toggle-link')
            if not link_elem:
                continue
            
            # Extract company name
            name_span = link_elem.find('span', class_='company-name')
            if not name_span or not name_span.get_text(strip=True):
                continue
            
            company_name = name_span.get_text(strip=True)
            
            # Deduplicate by company name
            if company_name in seen_names:
                continue
            seen_names.add(company_name)
            
            # Extract description
            desc_span = link_elem.find('span', class_='listing-text')
            description = desc_span.get_text(strip=True) if desc_span else None
            
            # Extract company URL - only if it's an <a> tag with href
            company_url = None
            if link_elem.name == 'a':
                href = link_elem.get('href')
                if href and href.strip() and not href.startswith('#'):
                    company_url = href.strip()
            
            # Extract sectors from row classes (e.g., type-finance, type-blockchain)
            sectors = []
            row_classes = row.get('class', [])
            
            # Map class names to display names
            sector_map = {
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
            
            for cls in row_classes:
                if cls in sector_map:
                    sector_name = sector_map[cls]
                    if sector_name not in sectors:
                        sectors.append(sector_name)
            
            # Determine status from row classes
            status = None
            if 'is-inactive' in row_classes:
                status = 'inactive'
            else:
                status = 'active'
            
            # Build company record with only fields that exist on the site
            company_record = {
                'company_name': company_name,
                'company_url': company_url,
                'description': description,
                'sectors': sectors,
                'status': status,
                'everywhere_tags': [],
                'source_url': portfolio_url
            }
            
            companies.append(company_record)
            
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
                         "..", "data", "notation_companies.json")
    with open(_out, "w") as _f:
        _json.dump(_records, _f, indent=2, ensure_ascii=False)
        _f.write("\n")
    print(f"wrote {len(_records)} records")
