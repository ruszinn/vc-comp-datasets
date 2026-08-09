# AUTO-GENERATED scraper (Claude API) — passed static guard + sandboxed
# validation before commit. Regenerate rather than hand-edit heavily.
import requests
import time
from bs4 import BeautifulSoup
from typing import List, Dict, Optional
from urllib.parse import urljoin

def scrape() -> List[Dict]:
    """
    Scrapes Crosslink Capital's portfolio companies from their portfolio page.
    Returns a list of portfolio company records with available data.
    """
    portfolio_url = "https://www.crosslinkcapital.com/portfolio"
    companies = []
    seen_names = set()
    
    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    })
    
    try:
        response = session.get(portfolio_url, timeout=20)
        response.raise_for_status()
    except Exception:
        return []
    
    soup = BeautifulSoup(response.content, 'html.parser')
    
    # Find all company boxes
    company_boxes = soup.find_all('div', class_='each-box')
    
    for box in company_boxes:
        try:
            # Extract company name from h3 in overlay
            overlay = box.find('div', class_='overlay')
            if not overlay:
                continue
            
            client_desc = overlay.find('div', class_='client-desc')
            if not client_desc:
                continue
            
            name_elem = client_desc.find('h3')
            if not name_elem:
                continue
            
            company_name = name_elem.get_text(strip=True)
            
            # Skip duplicates
            if company_name in seen_names:
                continue
            seen_names.add(company_name)
            
            # Extract description
            # The description is in the first <p> tag, which may contain multiple lines
            # including acquisition/exit info separated by <br>
            description = None
            desc_elem = client_desc.find('p')
            if desc_elem:
                # Get all text, replacing <br> with spaces
                desc_parts = []
                for content in desc_elem.children:
                    if hasattr(content, 'name') and content.name == 'br':
                        continue  # Skip the br tags themselves
                    else:
                        text = str(content).strip() if isinstance(content, str) else content.get_text(strip=True)
                        if text:
                            desc_parts.append(text)
                description = ' '.join(desc_parts)
            
            # Extract company website URL
            company_url = None
            links_div = client_desc.find('div', class_='links')
            if links_div:
                web_link_div = links_div.find('div', class_='web-link')
                if web_link_div:
                    web_link = web_link_div.find('a')
                    if web_link and web_link.get('href'):
                        company_url = web_link.get('href')
            
            # Determine sectors from CSS classes
            sectors = []
            box_classes = box.get('class', []) or []
            
            if 'enterprise' in box_classes:
                sectors.append('Enterprise')
            if 'consumer' in box_classes:
                sectors.append('Consumer')
            
            # Determine status from CSS classes
            status = None
            if 'exits' in box_classes:
                status = 'Exited'
            elif 'current' in box_classes:
                status = 'Current'
            
            # Check if featured
            is_featured = 'featured' in box_classes
            
            # Build tags
            tags = []
            if is_featured:
                tags.append('Featured')
            
            # Build company record - only include fields that are actually present on the site
            # The site does NOT expose: founders, stage, or profile_url
            company = {
                'company_name': company_name,
                'company_url': company_url,
                'description': description,
                'sectors': sectors,
                'status': status,
                'everywhere_tags': tags,
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
                         "..", "data", "crosslinkcapital_companies.json")
    with open(_out, "w") as _f:
        _json.dump(_records, _f, indent=2, ensure_ascii=False)
        _f.write("\n")
    print(f"wrote {len(_records)} records")
