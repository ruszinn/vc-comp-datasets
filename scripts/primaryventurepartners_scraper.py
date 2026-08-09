# AUTO-GENERATED scraper (Claude API) — passed static guard + sandboxed
# validation before commit. Regenerate rather than hand-edit heavily.
import requests
import bs4
import json
import re
import time
from typing import Optional
from collections import defaultdict


def scrape() -> list[dict]:
    """
    Scrape Primary Venture Partners portfolio companies from their specialization page.
    Returns a list of portfolio company dictionaries with available metadata.
    """
    source_url = "https://primary.vc/portfolio/specialization"
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    })
    
    companies = []
    seen = set()
    
    try:
        resp = session.get(source_url, timeout=20)
        resp.raise_for_status()
        soup = bs4.BeautifulSoup(resp.text, "html.parser")
        
        # Find all company items in the CMS list
        company_items = soup.find_all("div", class_="companies_cms_item")
        
        for item in company_items:
            try:
                company_data = _parse_company_item(item, source_url, session)
                if company_data and company_data.get("company_name"):
                    company_key = company_data.get("company_name", "").lower().strip()
                    if company_key not in seen:
                        companies.append(company_data)
                        seen.add(company_key)
                time.sleep(0.3)
            except Exception:
                continue
        
    except Exception:
        pass
    
    return companies


def _parse_company_item(item: bs4.element.Tag, source_url: str, session: requests.Session) -> Optional[dict]:
    """Parse a single company item from the portfolio list."""
    company = {
        "company_name": None,
        "company_url": None,
        "description": None,
        "founders": [],
        "sectors": [],
        "stage": None,
        "status": None,
        "profile_url": None,
        "invested_year": None,
        "headquarters": None,
        "investors": [],
        "founded_year": None,
        "everywhere_tags": [],
        "source_url": source_url,
    }
    
    # Extract company name from the expand section title
    expand_section = item.find("div", class_="companies_expand")
    if expand_section:
        title_div = expand_section.find("div", class_="companies_expand_title")
        if title_div:
            heading = title_div.find(["p", "h4", "h3", "h5"])
            if heading:
                company_name = heading.get_text(strip=True)
                if company_name:
                    company["company_name"] = company_name
    
    # Fallback: extract from main row logo alt text
    if not company["company_name"]:
        logo_img = item.find("img", class_="companies_row_img")
        if logo_img and logo_img.get("alt"):
            alt_text = logo_img.get("alt", "").strip()
            if alt_text:
                company["company_name"] = alt_text
    
    # Extract description from companies_row_description
    description_elem = item.find("div", class_="companies_row_description")
    if description_elem:
        desc_p = description_elem.find("p")
        if desc_p:
            desc_text = desc_p.get_text(strip=True)
            if desc_text:
                company["description"] = desc_text
    
    # Fallback: extract description from expand section
    if not company["description"] and expand_section:
        desc_div = expand_section.find("div", class_="companies_expand_title")
        if desc_div:
            desc_elements = desc_div.find_all("p")
            if len(desc_elements) > 1:
                company["description"] = desc_elements[1].get_text(strip=True)
    
    # Extract founders from companies_row_founders_text
    founders_elem = item.find("div", class_="companies_row_founders_text")
    if founders_elem:
        founders_text = founders_elem.get_text(separator="\n", strip=True)
        founder_list = [f.strip() for f in founders_text.split("\n") if f.strip()]
        company["founders"] = founder_list
    
    # Fallback: extract founders from expand section
    if not company["founders"] and expand_section:
        details_section = expand_section.find("div", class_="companies_expand_details")
        if details_section:
            for item_div in details_section.find_all("div", class_="companies_expand_details_item"):
                label = item_div.find(class_="u-weight-bold")
                if label and "Founders" in label.get_text():
                    founders_text = item_div.get_text(strip=True).replace("Founders", "", 1).strip()
                    if founders_text:
                        founder_list = [f.strip() for f in re.split(r'[,\n]+', founders_text) if f.strip()]
                        company["founders"] = founder_list
                    break
    
    # Extract invested year from companies_row_date
    date_elem = item.find("div", class_="companies_row_date")
    if date_elem:
        date_text = date_elem.get_text(strip=True)
        year_match = re.search(r"(\d{4})", date_text)
        if year_match:
            company["invested_year"] = int(year_match.group(1))
    
    # Fallback: extract invested year from expand section
    if not company["invested_year"] and expand_section:
        details_section = expand_section.find("div", class_="companies_expand_details")
        if details_section:
            for item_div in details_section.find_all("div", class_="companies_expand_details_item"):
                label = item_div.find(class_="u-weight-bold")
                if label and "Invested" in label.get_text():
                    year_text = item_div.get_text(strip=True).replace("Invested", "", 1).strip()
                    year_match = re.search(r"(\d{4})", year_text)
                    if year_match:
                        company["invested_year"] = int(year_match.group(1))
                    break
    
    # Extract stage from companies_row_stage
    stage_elem = item.find("div", class_="companies_row_stage")
    if stage_elem:
        stage_text = stage_elem.get_text(strip=True)
        if stage_text and stage_text.lower() != "stage":
            company["stage"] = stage_text
            if stage_text.lower() == "exited":
                company["status"] = "exited"
    
    # Fallback: extract stage from expand section
    if not company["stage"] and expand_section:
        details_section = expand_section.find("div", class_="companies_expand_details")
        if details_section:
            for item_div in details_section.find_all("div", class_="companies_expand_details_item"):
                label = item_div.find(class_="u-weight-bold")
                if label and "Stage" in label.get_text():
                    stage_text = item_div.get_text(strip=True).replace("Stage", "", 1).strip()
                    if stage_text:
                        company["stage"] = stage_text
                        if stage_text.lower() == "exited":
                            company["status"] = "exited"
                    break
    
    # Extract founded year from expand section
    if expand_section:
        details_section = expand_section.find("div", class_="companies_expand_details")
        if details_section:
            for item_div in details_section.find_all("div", class_="companies_expand_details_item"):
                label = item_div.find(class_="u-weight-bold")
                if label and "Founded" in label.get_text():
                    year_text = item_div.get_text(strip=True).replace("Founded", "", 1).strip()
                    year_match = re.search(r"(\d{4})", year_text)
                    if year_match:
                        company["founded_year"] = int(year_match.group(1))
                    break
    
    # Extract headquarters from expand section
    if expand_section:
        details_section = expand_section.find("div", class_="companies_expand_details")
        if details_section:
            for item_div in details_section.find_all("div", class_="companies_expand_details_item"):
                label = item_div.find(class_="u-weight-bold")
                if label and "HQ" in label.get_text():
                    hq_text = item_div.get_text(strip=True).replace("HQ", "", 1).strip()
                    if hq_text:
                        company["headquarters"] = hq_text
                    break
    
    # Extract investors from expand section
    if expand_section:
        details_section = expand_section.find("div", class_="companies_expand_details")
        if details_section:
            for item_div in details_section.find_all("div", class_="companies_expand_details_item"):
                label = item_div.find(class_="u-weight-bold")
                if label and "Investor" in label.get_text():
                    investor_div = item_div.find("div", class_="w-dyn-list")
                    if investor_div:
                        investor_items = investor_div.find_all("div", class_="w-dyn-item")
                        investors = [inv.get_text(strip=True) for inv in investor_items]
                        company["investors"] = investors
                    break
    
    # Extract sectors/tags from expand section
    if expand_section:
        tags_section = expand_section.find("div", class_="tags_cms_wrap")
        if tags_section:
            tag_items = tags_section.find_all("div", class_="tags_cms_item")
            sectors = [tag.get_text(strip=True) for tag in tag_items]
            company["sectors"] = sectors
    
    # Extract profile URL (company page link)
    profile_link = item.find("a", class_="companies_row_founders_link")
    if profile_link and profile_link.get("href"):
        href = profile_link.get("href", "").strip()
        if href:
            if not href.startswith("http"):
                href = "https://primary.vc" + href
            company["profile_url"] = href
    
    # Extract company website URL from expand section buttons
    if expand_section:
        all_links = expand_section.find_all("a", href=True)
        for link in all_links:
            href = link.get("href", "").strip()
            # Check if it's an external company website (not primary.vc)
            if href and "primary.vc" not in href and not href.startswith("/"):
                company["company_url"] = href
                break
    
    return company if company.get("company_name") else None


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
                         "..", "data", "primaryventurepartners_companies.json")
    with open(_out, "w") as _f:
        _json.dump(_records, _f, indent=2, ensure_ascii=False)
        _f.write("\n")
    print(f"wrote {len(_records)} records")
