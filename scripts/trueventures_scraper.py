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
    """Scrape True Ventures portfolio companies from https://trueventures.com/portfolio"""
    
    portfolio_url = "https://trueventures.com/portfolio"
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    })
    
    companies_dict = {}
    
    try:
        response = session.get(portfolio_url, timeout=20)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        
        # Extract portfolio data from Next.js script tags
        scripts = soup.find_all("script")
        portfolio_data = None
        
        for script in scripts:
            if script.string:
                script_text = script.string
                # Look for the embedded JSON with company data
                if '"highlights":' in script_text and '"exits":' in script_text and '"all":' in script_text:
                    try:
                        # Extract JSON object containing highlights, exits, and all
                        match = re.search(r'\{"highlights":\[(.*?)\],"exits":\[(.*?)\],"all":\[(.*?)\]\}', script_text, re.DOTALL)
                        if match:
                            json_str = '{"highlights":[' + match.group(1) + '],"exits":[' + match.group(2) + '],"all":[' + match.group(3) + ']}'
                            portfolio_data = json.loads(json_str)
                            break
                    except (json.JSONDecodeError, AttributeError):
                        pass
        
        # If we found portfolio data in scripts, process it
        if portfolio_data:
            all_companies = portfolio_data.get('highlights', []) + portfolio_data.get('exits', []) + portfolio_data.get('all', [])
            
            for company in all_companies:
                name = company.get('name')
                if not name:
                    continue
                
                # Deduplicate by company name
                if name.lower() not in companies_dict:
                    companies_dict[name.lower()] = {
                        "company_name": name,
                        "company_url": None,
                        "description": company.get('description'),
                        "founders": [],
                        "sectors": company.get('sectors', []),
                        "stage": None,
                        "status": None,
                        "profile_url": None,
                        "everywhere_tags": [],
                        "source_url": portfolio_url
                    }
        
        # Also extract from visible portfolio links (cards with company links)
        portfolio_links = soup.find_all('a', {'aria-label': re.compile(r'.*\(opens in new tab\)')})
        for link in portfolio_links:
            href = link.get('href')
            aria_label = link.get('aria-label', '')
            company_name = aria_label.replace(' (opens in new tab)', '').strip()
            
            if company_name and href:
                key = company_name.lower()
                if key not in companies_dict:
                    companies_dict[key] = {
                        "company_name": company_name,
                        "company_url": href if href.startswith('http') else urljoin('https://trueventures.com', href),
                        "description": None,
                        "founders": [],
                        "sectors": [],
                        "stage": None,
                        "status": None,
                        "profile_url": None,
                        "everywhere_tags": [],
                        "source_url": portfolio_url
                    }
                else:
                    # Update URL if we found one
                    if not companies_dict[key].get('company_url') and href:
                        companies_dict[key]['company_url'] = href if href.startswith('http') else urljoin('https://trueventures.com', href)
        
        # Fetch detail pages for companies to enrich with more info
        for key, company in companies_dict.items():
            if company.get('company_url'):
                try:
                    time.sleep(0.3)  # Rate limiting
                    detail_response = session.get(company['company_url'], timeout=20)
                    detail_response.raise_for_status()
                    detail_soup = BeautifulSoup(detail_response.text, 'html.parser')
                    
                    # Try to extract founders, stage, or other metadata from detail page
                    # Look for common patterns in startup detail pages
                    meta_desc = detail_soup.find('meta', {'name': 'description'})
                    if meta_desc and not company.get('description'):
                        company['description'] = meta_desc.get('content')
                    
                    # Look for founders in structured data or text
                    founders_section = detail_soup.find(re.compile('^(h[1-6]|div|p)$'), string=re.compile(r'founder|team', re.I))
                    if founders_section and not company.get('founders'):
                        # Try to extract names from following content
                        next_content = founders_section.find_next(['ul', 'div', 'p'])
                        if next_content:
                            names = re.findall(r'\b[A-Z][a-z]+ [A-Z][a-z]+\b', next_content.get_text())
                            if names:
                                company['founders'] = list(set(names))[:3]  # Limit to 3 founders
                    
                except Exception:
                    # Continue on error for individual company detail pages
                    pass
        
        # Convert to list and ensure no duplicates by company name
        result = []
        seen_names = set()
        for company in companies_dict.values():
            name_lower = company['company_name'].lower()
            if name_lower not in seen_names:
                seen_names.add(name_lower)
                result.append(company)
        
        return result
    
    except Exception:
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
                         "..", "data", "trueventures_companies.json")
    with open(_out, "w") as _f:
        _json.dump(_records, _f, indent=2, ensure_ascii=False)
        _f.write("\n")
    print(f"wrote {len(_records)} records")
