# AUTO-GENERATED scraper (Claude API) — passed static guard + sandboxed
# validation before commit. Regenerate rather than hand-edit heavily.
import requests
from bs4 import BeautifulSoup
import json
import time
from typing import Any

def scrape() -> list[dict]:
    """
    Scrape Felicis portfolio companies from their portfolio page.
    
    Returns:
        list[dict]: List of portfolio company dictionaries
    """
    portfolio_url = "https://felicis.com/portfolio"
    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    })
    
    companies = []
    seen_names = set()
    
    try:
        response = session.get(portfolio_url, timeout=20)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Find JSON-LD structured data
        json_ld_scripts = soup.find_all('script', type='application/ld+json')
        
        for script in json_ld_scripts:
            try:
                data = json.loads(script.string)
                
                # Check if this is the CollectionPage with portfolio data
                if data.get('@type') == 'CollectionPage' and 'mainEntity' in data:
                    main_entity = data.get('mainEntity', {})
                    
                    if main_entity.get('@type') == 'ItemList':
                        items = main_entity.get('itemListElement', [])
                        
                        for list_item in items:
                            if list_item.get('@type') == 'ListItem':
                                item = list_item.get('item', {})
                                
                                if item.get('@type') == 'Organization':
                                    company_name = item.get('name')
                                    
                                    # Skip duplicates
                                    if company_name and company_name in seen_names:
                                        continue
                                    
                                    if company_name:
                                        seen_names.add(company_name)
                                    
                                    # Extract founders
                                    founders_list = []
                                    founders_data = item.get('founder', [])
                                    
                                    if isinstance(founders_data, dict):
                                        founders_data = [founders_data]
                                    
                                    for founder in founders_data:
                                        if isinstance(founder, dict):
                                            founder_name = founder.get('name')
                                            if founder_name:
                                                founders_list.append(founder_name)
                                    
                                    # Extract location
                                    location = None
                                    location_data = item.get('location')
                                    if isinstance(location_data, dict):
                                        location = location_data.get('name')
                                    
                                    # Build company record
                                    company = {
                                        'company_name': company_name,
                                        'company_url': item.get('url') or None,
                                        'description': item.get('description') or None,
                                        'founders': founders_list,
                                        'location': location,
                                        'everywhere_tags': [],
                                        'source_url': portfolio_url
                                    }
                                    
                                    companies.append(company)
            
            except (json.JSONDecodeError, KeyError, TypeError):
                continue
    
    except requests.RequestException:
        pass
    
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
                         "..", "data", "felicis_companies.json")
    with open(_out, "w") as _f:
        _json.dump(_records, _f, indent=2, ensure_ascii=False)
        _f.write("\n")
    print(f"wrote {len(_records)} records")
