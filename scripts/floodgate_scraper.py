# AUTO-GENERATED scraper (Claude API) — passed static guard + sandboxed
# validation before commit. Regenerate rather than hand-edit heavily.
import requests
from bs4 import BeautifulSoup
import json
import re
import time
from typing import List, Dict, Optional

def scrape() -> List[Dict]:
    """
    Scrapes Floodgate portfolio companies from https://floodgate.com/companies
    Returns a list of dicts with company data.
    """
    portfolio_url = "https://floodgate.com/companies"
    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    })
    companies = []
    seen_names = set()
    
    try:
        # Fetch the portfolio page
        resp = session.get(portfolio_url, timeout=20)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.content, "html.parser")
        
        # Look for company items in the grid
        company_items = soup.find_all("div", {"role": "listitem", "class": "company__item"})
        
        for item in company_items:
            try:
                # Extract company name from the hover state
                name_elem = item.find("div", {"class": "companies__name"})
                if not name_elem:
                    continue
                company_name = name_elem.get_text(strip=True)
                
                # Skip if we've already seen this company
                if company_name in seen_names:
                    continue
                seen_names.add(company_name)
                
                # Extract sectors/categories from filter tags
                sectors = []
                filter_wrapper = item.find("div", {"class": "company__filter-wr"})
                if filter_wrapper:
                    filter_texts = filter_wrapper.find_all("div", {"class": "company__filter-text"})
                    for ft in filter_texts:
                        text = ft.get_text(strip=True)
                        if text:
                            sectors.append(text)
                
                # Extract industry from hover state
                industry_elem = item.find("div", {"class": "companies__industry-text"})
                industry = industry_elem.get_text(strip=True) if industry_elem else None
                
                # Find the modal section for detailed info
                modal = item.find("div", {"class": "company-modal"})
                
                # Extract company URL from modal link
                company_url = None
                founders = []
                twitter_url = None
                linkedin_url = None
                
                if modal:
                    modal_link = modal.find("a", {"class": "modal__link"})
                    if modal_link:
                        raw_url = modal_link.get("href", "").strip()
                        if raw_url and raw_url != "#":
                            # Ensure it's a full URL
                            if not raw_url.startswith("http"):
                                company_url = "https://" + raw_url
                            else:
                                company_url = raw_url
                    
                    # Extract founders from modal
                    founders_elem = modal.find("div", {"class": "modal__founders-text"})
                    if founders_elem:
                        founders_text = founders_elem.get_text(strip=True)
                        if founders_text:
                            # Split founders by comma and ampersand
                            founders_raw = re.split(r',\s*|\s*&\s*', founders_text)
                            founders = [f.strip() for f in founders_raw if f.strip()]
                    
                    # Extract social links
                    social_wrapper = modal.find("div", {"class": "modal__social-wr"})
                    if social_wrapper:
                        social_links = social_wrapper.find_all("a", {"class": "modal__social-link"})
                        for link in social_links:
                            href = link.get("href", "").strip()
                            if "twitter.com" in href or "x.com" in href:
                                twitter_url = href if href != "#" else None
                            elif "linkedin.com" in href:
                                linkedin_url = href if href != "#" else None
                
                # Check for exit status (IPO, M&A, etc.)
                status = None
                tag_elem = item.find("div", {"class": "companies__tag"})
                if tag_elem:
                    # Check if it's NOT hidden
                    classes = tag_elem.get("class", [])
                    if "w-condition-invisible" not in classes:
                        tag_text_elem = tag_elem.find("div", {"class": "companies__tag-text"})
                        if tag_text_elem:
                            status_text = tag_text_elem.get_text(strip=True)
                            if status_text:
                                status = status_text
                
                # Build description from industry and sectors
                description_parts = []
                if industry:
                    description_parts.append(industry)
                if sectors:
                    description_parts.append(" | ".join(sectors))
                
                description = " - ".join(description_parts) if description_parts else None
                
                # Build company record with available fields
                record = {
                    "company_name": company_name,
                    "company_url": company_url,
                    "description": description,
                    "founders": founders,
                    "sectors": sectors,
                    "status": status,
                    "twitter_url": twitter_url,
                    "linkedin_url": linkedin_url,
                    "everywhere_tags": [],
                    "source_url": portfolio_url,
                }
                
                companies.append(record)
                
            except Exception:
                # Skip problematic items silently
                continue
        
    except Exception:
        # If scraping fails completely, return empty list
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
                         "..", "data", "floodgate_companies.json")
    with open(_out, "w") as _f:
        _json.dump(_records, _f, indent=2, ensure_ascii=False)
        _f.write("\n")
    print(f"wrote {len(_records)} records")
