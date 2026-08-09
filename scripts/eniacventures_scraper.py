# AUTO-GENERATED scraper (Claude API) — passed static guard + sandboxed
# validation before commit. Regenerate rather than hand-edit heavily.
import requests
import bs4
import time
import re
from typing import Optional
from collections import defaultdict
from urllib.parse import urljoin


def scrape() -> list[dict]:
    """
    Scrape Eniac Ventures portfolio companies from https://eniac.vc/companies
    
    Returns:
        list[dict]: List of portfolio company dictionaries with standardized schema
    """
    portfolio_url = "https://eniac.vc/companies"
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    })
    
    companies = []
    seen_names = set()
    
    try:
        # Fetch main portfolio page
        response = session.get(portfolio_url, timeout=20)
        response.raise_for_status()
    except Exception as e:
        return []
    
    soup = bs4.BeautifulSoup(response.text, "html.parser")
    
    # Find all company item containers
    company_items = soup.find_all("div", {"class": "companies-item"})
    
    for item in company_items:
        try:
            # Extract company name from hidden field
            name_div = item.find("div", {"fs-cmsfilter-field": "name"})
            company_name = name_div.get_text(strip=True) if name_div else None
            
            if not company_name or company_name in seen_names:
                continue
            
            seen_names.add(company_name)
            
            # Extract description from company card
            description_elem = item.find("div", {"class": "body-small is-directory"})
            description = description_elem.get_text(strip=True) if description_elem else None
            
            # Extract data from popup (contains more detailed info)
            popup = item.find("div", {"class": "company-popup"})
            
            company_url = None
            founders = []
            location = None
            sectors = []
            status = None
            
            if popup:
                # Extract website URL
                website_link = popup.find("a", href=re.compile(r"^https?://"))
                if website_link and "Visit" in website_link.get_text(strip=True):
                    company_url = website_link.get("href")
                
                # Extract location
                location_lines = popup.find_all("div", {"class": "company-popup-line"})
                for line in location_lines:
                    tag = line.find("div", {"class": "company-line-tag"})
                    if tag:
                        tag_text = tag.get_text(strip=True)
                        if tag_text == "Location":
                            location_val = line.find_all("div")
                            if len(location_val) > 1:
                                location = location_val[1].get_text(strip=True)
                        elif tag_text == "Founders":
                            founders_container = line.find("div", {"class": "company-popup-text"})
                            if founders_container:
                                founder_divs = founders_container.find_all("div")
                                founders = [d.get_text(strip=True) for d in founder_divs if d.get_text(strip=True)]
            
            # Extract categories/sectors from hidden divs
            category_divs = item.find_all("div", {"fs-cmsfilter-field": "category"})
            for cat_div in category_divs:
                cat_text = cat_div.get_text(strip=True)
                if cat_text and cat_text != "All" and cat_text not in sectors:
                    sectors.append(cat_text)
            
            # Determine status (Exited or Active)
            exited_badge = item.find("div", {"class": "companies-exited"})
            if exited_badge and "Exited" in exited_badge.get_text(strip=True):
                status = "exited"
            else:
                status = "active"
            
            # Build company record
            company_record = {
                "company_name": company_name,
                "company_url": company_url,
                "description": description,
                "founders": founders,
                "sectors": sectors,
                "location": location,
                "status": status,
                "everywhere_tags": [],
                "source_url": portfolio_url,
            }
            
            companies.append(company_record)
            time.sleep(0.3)
            
        except Exception as e:
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
                         "..", "data", "eniacventures_companies.json")
    with open(_out, "w") as _f:
        _json.dump(_records, _f, indent=2, ensure_ascii=False)
        _f.write("\n")
    print(f"wrote {len(_records)} records")
