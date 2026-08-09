# AUTO-GENERATED scraper (Claude API) — passed static guard + sandboxed
# validation before commit. Regenerate rather than hand-edit heavily.
import requests
import json
import re
import time
from bs4 import BeautifulSoup
from typing import List, Dict
from collections import defaultdict

def scrape() -> List[Dict]:
    """
    Scrapes Gradient Ventures portfolio from https://gradient.com/portfolio
    Returns a list of portfolio company dictionaries.
    """
    portfolio_url = "https://gradient.com/portfolio"
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
    
    soup = BeautifulSoup(resp.text, 'html.parser')
    
    # Find all portfolio company rows - both featured and hidden ones
    company_rows = soup.find_all('div', {'data-portfolio-row': 'true'})
    
    for row in company_rows:
        try:
            # Extract company name from link
            name_elem = row.find('a', {'class': lambda x: x and '_companyName' in x})
            if not name_elem:
                continue
            
            company_name_text = name_elem.get_text(strip=True)
            if not company_name_text:
                continue
            
            # Remove any trailing acquisition/status indicators
            company_name = company_name_text.split('\n')[0].strip()
            if not company_name or company_name in seen_names:
                continue
            seen_names.add(company_name)
            
            # Extract company URL
            company_url = name_elem.get('href', '').strip()
            if not company_url or company_url == '' or company_url == '#':
                company_url = None
            
            # Extract status (acquired, etc.)
            status = None
            status_em = row.find('em')
            if status_em:
                acquired_text = status_em.get_text(strip=True)
                parent_text = row.get_text(strip=True)
                if 'Acquired by' in parent_text:
                    status = f"Acquired by {acquired_text}"
            
            # Extract description - find all body-2 divs and pick the longest non-metadata text
            description = None
            body2_divs = row.find_all('div', {'class': lambda x: x and '_size:body-2' in x})
            for div in body2_divs:
                text = div.get_text(strip=True)
                # Skip empty, "Partnered YYYY", and "Acquired by" lines
                if text and 'Partnered' not in text and 'Acquired by' not in text and len(text) > 10:
                    description = text
                    break
            
            # Extract partnership year from link column
            partnership_year = None
            link_col = row.find('div', {'class': lambda x: x and '_linkColumn' in x})
            if link_col:
                link_text = link_col.get_text(strip=True)
                year_match = re.search(r'Partnered\s+(\d{4})', link_text)
                if year_match:
                    partnership_year = int(year_match.group(1))
            
            # Check if featured
            featured = row.get('data-featured') == 'true'
            
            company_record = {
                'company_name': company_name,
                'company_url': company_url,
                'description': description,
                'founders': [],
                'sectors': [],
                'stage': None,
                'status': status,
                'year_founded': None,
                'profile_url': None,
                'partnership_year': partnership_year,
                'everywhere_tags': [],
                'source_url': portfolio_url,
            }
            
            # Attempt to enrich from company URL if available
            if company_url and company_url.startswith('http'):
                try:
                    time.sleep(0.3)
                    detail_resp = session.get(company_url, timeout=20)
                    if detail_resp.status_code == 200:
                        detail_soup = BeautifulSoup(detail_resp.text, 'html.parser')
                        
                        # Try to find founders, sectors from meta tags or structured data
                        # Look for common patterns in startup websites
                        all_text = detail_soup.get_text()
                        
                        # Simple heuristic: look for "Founded by" or "Founders:"
                        founder_match = re.search(r'[Ff]ounded\s+by\s*:?\s*([^.;\n]+)', all_text)
                        if founder_match:
                            founders_str = founder_match.group(1)
                            # Split by common delimiters
                            founders_list = re.split(r',\s*(?:and\s+)?', founders_str)
                            company_record['founders'] = [f.strip() for f in founders_list if f.strip()]
                        
                        # Look for year founded
                        year_match = re.search(r'[Ff]ounded\s+(?:in\s+)?(\d{4})', all_text)
                        if year_match:
                            company_record['year_founded'] = int(year_match.group(1))
                
                except Exception:
                    pass
            
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
                         "..", "data", "gradientventures_companies.json")
    with open(_out, "w") as _f:
        _json.dump(_records, _f, indent=2, ensure_ascii=False)
        _f.write("\n")
    print(f"wrote {len(_records)} records")
