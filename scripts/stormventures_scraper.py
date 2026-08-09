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
    Scrapes Storm Ventures portfolio companies from their portfolio page.
    Returns a list of dictionaries with company information.
    """
    portfolio_url = "https://stormventures.com/our-portfolio"
    companies = []
    seen_companies = set()
    
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    })
    
    try:
        response = session.get(portfolio_url, timeout=20)
        response.raise_for_status()
    except requests.RequestException:
        return []
    
    soup = BeautifulSoup(response.text, "html.parser")
    
    # Find all portfolio cards in the grid
    portfolio_cards = soup.find_all("div", class_="portfolio-all_card")
    
    for card in portfolio_cards:
        try:
            # Extract company name from the logo alt text
            logo_img = card.find("img")
            company_name = None
            if logo_img and logo_img.get("alt"):
                company_name = logo_img.get("alt").strip()
            
            if not company_name:
                continue
            
            # Skip duplicates
            if company_name in seen_companies:
                continue
            seen_companies.add(company_name)
            
            # Extract company URL from the hover link
            company_url = None
            link = card.find("a", class_="all-card_hover")
            if link and link.get("href"):
                company_url = link.get("href").strip()
            
            # Extract status (Active/Exit)
            status = None
            status_elem = card.find("div", class_="status-card")
            if status_elem:
                status_text = status_elem.get_text(strip=True)
                if status_text in ["Active", "Exit"]:
                    status = status_text
            
            # Extract region
            region = None
            region_elem = card.find("div", class_="all-card_region")
            if region_elem:
                region = region_elem.get_text(strip=True)
            
            # Extract sector
            sector = None
            sector_elem = card.find("div", class_="all-card_sector")
            if sector_elem:
                sector = sector_elem.get_text(strip=True)
            
            # Extract description
            description = None
            desc_elem = card.find("div", class_="all-card_logo-description")
            if desc_elem:
                description = desc_elem.get_text(strip=True)
            
            # Build company record with all available data
            company_record = {
                "company_name": company_name,
                "company_url": company_url,
                "description": description,
                "status": status,
                "sector": sector,
                "region": region,
                "founders": [],
                "profile_url": company_url,
                "everywhere_tags": [],
                "source_url": portfolio_url
            }
            
            # Fetch company detail page for enriched data if URL exists
            if company_url:
                detail_data = _fetch_company_details(session, company_url)
                if detail_data.get("founders"):
                    company_record["founders"] = detail_data["founders"]
                if detail_data.get("additional_description"):
                    company_record["description"] = detail_data["additional_description"]
            
            companies.append(company_record)
            time.sleep(0.3)
        
        except Exception:
            continue
    
    return companies


def _fetch_company_details(session: requests.Session, url: str) -> Dict:
    """
    Fetches additional details from a company's detail page.
    Returns a dict with optional founders and enriched description.
    """
    result = {"founders": [], "additional_description": None}
    
    if not url:
        return result
    
    try:
        response = session.get(url, timeout=20)
        response.raise_for_status()
    except requests.RequestException:
        return result
    
    try:
        soup = BeautifulSoup(response.text, "html.parser")
        
        # Extract text content for pattern matching
        text_content = soup.get_text(separator=" ")
        
        # Look for founder patterns in common formats
        founders = _extract_founders(text_content)
        if founders:
            result["founders"] = founders
        
        # Try to extract a better description from first paragraph or meta
        description = None
        
        # Check for meta description
        meta_desc = soup.find("meta", attrs={"name": "description"})
        if meta_desc and meta_desc.get("content"):
            description = meta_desc.get("content").strip()
        
        # Fallback: check for first paragraph
        if not description:
            p_tags = soup.find_all("p")
            for p in p_tags:
                text = p.get_text(strip=True)
                if len(text) > 20 and len(text) < 300:
                    description = text
                    break
        
        if description:
            result["additional_description"] = description
    
    except Exception:
        pass
    
    return result


def _extract_founders(text: str) -> List[str]:
    """
    Extracts founder names from text using pattern matching.
    Returns a list of founder names.
    """
    founders = []
    
    # Pattern 1: "Founded by Name1 and Name2"
    pattern1 = r"[Ff]ounded by\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*(?:\s+(?:and|&)\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)*)"
    matches = re.findall(pattern1, text)
    if matches:
        for match in matches:
            names = re.split(r"\s+(?:and|&)\s+", match)
            for name in names:
                clean_name = name.strip()
                if clean_name and clean_name not in founders:
                    founders.append(clean_name)
    
    # Pattern 2: "Founders: Name1, Name2"
    if not founders:
        pattern2 = r"[Ff]ounders?:\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*(?:,\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)*)"
        matches = re.findall(pattern2, text)
        if matches:
            for match in matches:
                names = re.split(r",\s+", match)
                for name in names:
                    clean_name = name.strip()
                    if clean_name and clean_name not in founders:
                        founders.append(clean_name)
    
    return founders


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
                         "..", "data", "stormventures_companies.json")
    with open(_out, "w") as _f:
        _json.dump(_records, _f, indent=2, ensure_ascii=False)
        _f.write("\n")
    print(f"wrote {len(_records)} records")
