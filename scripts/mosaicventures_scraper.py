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
    Scrape Mosaic Ventures portfolio companies from https://mosaicventures.com/portfolio
    """
    portfolio_url = "https://mosaicventures.com/portfolio"
    session = requests.Session()
    
    companies = []
    seen = set()
    
    try:
        resp = session.get(portfolio_url, timeout=20)
        resp.raise_for_status()
    except Exception:
        return []
    
    soup = BeautifulSoup(resp.text, 'html.parser')
    
    # Extract portfolio items from the HTML structure
    # Looking for list items in portfolio lists
    portfolio_lists = soup.find_all('ul', id=re.compile(r'portfolio'))
    
    for ul in portfolio_lists:
        list_items = ul.find_all('li', recursive=False)
        
        for li in list_items:
            try:
                # Extract company name from heading or text
                name_elem = li.find(['h1', 'h2', 'h3', 'h4'])
                company_name = None
                company_url = None
                
                if name_elem:
                    company_name = name_elem.get_text(strip=True)
                    # Look for link in the list item
                    link_elem = li.find('a', href=True)
                    if link_elem:
                        company_url = link_elem.get('href')
                        if company_url and not company_url.startswith('http'):
                            company_url = urljoin(portfolio_url, company_url)
                
                if not company_name:
                    # Fallback: get first text
                    text = li.get_text(strip=True)
                    if text:
                        company_name = text.split('\n')[0][:100]
                
                if not company_name or company_name in seen:
                    continue
                
                seen.add(company_name)
                
                # Extract description
                desc_elem = li.find(['p', 'div'], class_=re.compile(r'description|content', re.I))
                description = None
                if desc_elem:
                    description = desc_elem.get_text(strip=True)
                
                # Extract status and sectors from data attributes
                status = li.get('data-status')
                tags_str = li.get('data-tag', '')
                
                # Parse sectors from tags string
                sectors = []
                if tags_str:
                    sectors = [t.strip() for t in tags_str.split() if t.strip()]
                
                # Look for underlined text (sectors/tags)
                underlined = li.find_all('u')
                for u_elem in underlined:
                    text = u_elem.get_text(strip=True)
                    # Split by comma
                    parts = [p.strip() for p in text.split(',')]
                    for part in parts:
                        normalized = part.lower().strip()
                        if normalized not in ['active', 'exited'] and normalized:
                            if part not in sectors:
                                sectors.append(part)
                
                company_record = {
                    "company_name": company_name,
                    "company_url": company_url,
                    "description": description,
                    "founders": [],
                    "sectors": sectors,
                    "stage": None,
                    "status": status,
                    "profile_url": None,
                    "everywhere_tags": [],
                    "source_url": portfolio_url
                }
                
                companies.append(company_record)
                time.sleep(0.3)
                
            except Exception:
                continue
    
    # If no companies found, try alternative parsing
    if not companies:
        # Look for any portfolio-related content blocks
        content_blocks = soup.find_all(['article', 'div'], class_=re.compile(r'item|card|block', re.I))
        
        for block in content_blocks:
            try:
                # Try to extract name and link
                name_elem = block.find(['h1', 'h2', 'h3', 'h4', 'a'])
                if not name_elem:
                    continue
                
                company_name = name_elem.get_text(strip=True)
                if not company_name or company_name in seen:
                    continue
                
                seen.add(company_name)
                
                company_url = None
                link = block.find('a', href=True)
                if link:
                    company_url = link.get('href')
                    if company_url and not company_url.startswith('http'):
                        company_url = urljoin(portfolio_url, company_url)
                
                description = None
                desc = block.find(['p', 'div'])
                if desc:
                    description = desc.get_text(strip=True)[:500]
                
                company_record = {
                    "company_name": company_name,
                    "company_url": company_url,
                    "description": description,
                    "founders": [],
                    "sectors": [],
                    "stage": None,
                    "status": None,
                    "profile_url": None,
                    "everywhere_tags": [],
                    "source_url": portfolio_url
                }
                
                companies.append(company_record)
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
                         "..", "data", "mosaicventures_companies.json")
    with open(_out, "w") as _f:
        _json.dump(_records, _f, indent=2, ensure_ascii=False)
        _f.write("\n")
    print(f"wrote {len(_records)} records")
