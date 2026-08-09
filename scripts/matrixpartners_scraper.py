# AUTO-GENERATED scraper (Claude API) — passed static guard + sandboxed
# validation before commit. Regenerate rather than hand-edit heavily.
import requests
from bs4 import BeautifulSoup
import json
import re
import time
from typing import List, Dict
from urllib.parse import urljoin

def scrape() -> List[Dict]:
    """
    Scrapes Matrix Partners portfolio companies from their website.
    Returns a list of dictionaries with portfolio company information.
    """
    portfolio_url = "https://matrixpartners.com#portfolio"
    base_url = "https://matrixpartners.com"
    
    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    })
    
    companies = []
    seen_companies = {}  # Track by URL to dedupe
    
    try:
        # Fetch the main portfolio page
        response = session.get(base_url, timeout=20)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.content, 'html.parser')
        
        # Extract companies from team members' project lists
        # This is the primary data source - companies linked in team member profiles
        team_items = soup.find_all('div', {'class': 'team-layout-item'})
        
        for team_item in team_items:
            projects_section = team_item.find('div', {'class': 'projects-rt'})
            if projects_section:
                company_links = projects_section.find_all('a', href=True)
                for link in company_links:
                    href = link.get('href', '').strip()
                    company_name = link.get_text(strip=True)
                    
                    # Normalize and skip empty or malformed entries
                    if not href or not company_name or len(company_name) < 2:
                        continue
                    
                    if not href.startswith('http'):
                        continue
                    
                    # Dedupe by URL
                    if href in seen_companies:
                        continue
                    
                    seen_companies[href] = company_name
        
        # Process each unique company
        for company_url, company_name in seen_companies.items():
            company_record = {
                'company_name': company_name,
                'company_url': company_url,
                'description': None,
                'founders': [],
                'sectors': [],
                'stage': None,
                'status': None,
                'profile_url': None,
                'everywhere_tags': [],
                'source_url': base_url
            }
            
            # Fetch company details from their website
            try:
                time.sleep(0.3)  # Be polite between requests
                company_response = session.get(company_url, timeout=20)
                company_response.raise_for_status()
                
                company_soup = BeautifulSoup(company_response.content, 'html.parser')
                
                # Try to extract description from meta tags
                description_meta = company_soup.find('meta', {'name': 'description'})
                if description_meta and description_meta.get('content'):
                    company_record['description'] = description_meta.get('content').strip()
                
                # Fallback: look for og:description
                if not company_record['description']:
                    og_desc = company_soup.find('meta', {'property': 'og:description'})
                    if og_desc and og_desc.get('content'):
                        company_record['description'] = og_desc.get('content').strip()
                
                # Fallback: first paragraph in main content
                if not company_record['description']:
                    main = company_soup.find('main') or company_soup.find('article')
                    if main:
                        first_p = main.find('p')
                        if first_p:
                            text = first_p.get_text(strip=True)
                            if text and len(text) > 10:
                                company_record['description'] = text
                
                # Try to extract founders from common patterns
                # Look for "Founders:", "Founded by:", "Team" sections
                team_section = company_soup.find(text=re.compile(r'founder|team|leadership', re.I))
                if team_section:
                    parent = team_section.find_parent()
                    if parent:
                        # Look for names near founder text
                        names = re.findall(r'\b[A-Z][a-z]+ [A-Z][a-z]+\b', parent.get_text())
                        if names:
                            company_record['founders'] = list(set(names))[:5]  # Limit to 5
                
            except (requests.RequestException, Exception):
                # If we can't fetch the company page, continue with what we have
                pass
            
            companies.append(company_record)
        
        return companies
    
    except requests.RequestException as e:
        # Return empty list if main page fetch fails
        return []


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
                         "..", "data", "matrixpartners_companies.json")
    with open(_out, "w") as _f:
        _json.dump(_records, _f, indent=2, ensure_ascii=False)
        _f.write("\n")
    print(f"wrote {len(_records)} records")
