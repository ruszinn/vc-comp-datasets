# AUTO-GENERATED scraper (Claude API) — passed static guard + sandboxed
# validation before commit. Regenerate rather than hand-edit heavily.
import requests
from bs4 import BeautifulSoup
from typing import List, Dict
import time
import re

def scrape() -> List[Dict]:
    """
    Scrape portfolio companies from Wing VC.
    Returns a list of dictionaries, one per portfolio company.
    """
    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    })
    
    portfolio_url = "https://wing.vc/companies"
    companies = []
    seen_names = set()
    
    try:
        response = session.get(portfolio_url, timeout=20)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Find all company containers
        company_containers = soup.find_all('div', {'role': 'listitem', 'class': lambda c: c and 'companies-list_container' in c})
        
        for container in company_containers:
            try:
                company = parse_company(container, portfolio_url)
                if company and company.get('company_name'):
                    # Dedupe by company name
                    if company['company_name'] not in seen_names:
                        companies.append(company)
                        seen_names.add(company['company_name'])
            except Exception as e:
                # Silently skip problematic companies
                continue
        
    except Exception as e:
        # Return what we have, even if incomplete
        pass
    
    return companies


def parse_company(container, source_url: str) -> Dict:
    """Parse a single company container and return a dictionary."""
    company = {
        'company_name': None,
        'company_url': None,
        'description': None,
        'stage': None,
        'status': None,
        'year': None,
        'logo_url': None,
        'founders': [],
        'domains': [],
        'prior_work': None,
        'everywhere_tags': [],
        'source_url': source_url
    }
    
    # Company name
    name_elem = container.find('div', {'fs-cmsfilter-field': 'company-name'})
    if name_elem:
        company['company_name'] = name_elem.get_text(strip=True)
    
    # Company URL and logo from the dropdown section
    dropdown = container.find('div', class_=lambda c: c and 'is-dropdown' in c)
    if dropdown:
        # Company URL
        url_link = dropdown.find('a', href=True, target='_blank')
        if url_link:
            href = url_link.get('href', '').strip()
            if href and href != '#':
                company['company_url'] = href
        
        # Logo URL
        logo_img = dropdown.find('img', class_=lambda c: c and 'companies_logo' in c)
        if logo_img:
            logo_src = logo_img.get('src', '').strip()
            if logo_src and not logo_src.endswith('w-dyn-bind-empty'):
                company['logo_url'] = logo_src
    
    # Description/tagline
    descr_wrapper = container.find('div', class_=lambda c: c and 'companies-list-descr-wrapper' in c)
    if descr_wrapper:
        descr_elems = descr_wrapper.find_all('div', class_='heading-style-h5', recursive=False)
        for elem in descr_elems:
            # Skip conditional invisible elements
            classes = elem.get('class', [])
            if 'w-condition-invisible' not in classes:
                text = elem.get_text(strip=True)
                if text:
                    company['description'] = text
                    break
    
    # Initial investment stage and year
    investment_wrapper = container.find('div', class_=lambda c: c and 'companies-list-initial-investment-wrapper' in c)
    if investment_wrapper:
        # Stage
        stage_elem = investment_wrapper.find('div', {'fs-cmsfilter-field': 'initlal-invesment'})
        if not stage_elem:
            stage_elem = investment_wrapper.find('div', {'fs-cmsfilter-field': 'initial-investment'})
        if stage_elem:
            classes = stage_elem.get('class', [])
            if 'w-condition-invisible' not in classes:
                stage_text = stage_elem.get_text(strip=True)
                if stage_text and stage_text != '–':
                    company['stage'] = stage_text
        
        # Year
        year_wrapper = investment_wrapper.find('div', class_=lambda c: c and 'companies-list_year-wrapper' in c)
        if year_wrapper:
            year_elems = year_wrapper.find_all('div', class_='heading-style-h5')
            for ye in year_elems:
                text = ye.get_text(strip=True)
                if text and text.isdigit() and len(text) == 4:
                    company['year'] = int(text)
                    break
    
    # Status
    status_wrapper = container.find('div', class_=lambda c: c and 'companies-list-status-wrapper' in c)
    if status_wrapper:
        status_elems = status_wrapper.find_all('div', {'fs-cmsfilter-field': 'status'})
        for elem in status_elems:
            classes = elem.get('class', [])
            if 'w-condition-invisible' not in classes:
                text = elem.get_text(strip=True)
                if text and text != '–':
                    company['status'] = text
                    break
    
    # Founders
    if dropdown:
        founder_list = dropdown.find('div', class_=lambda c: c and 'founder-list_grid' in c)
        if founder_list:
            founder_items = founder_list.find_all('div', {'role': 'listitem'})
            for item in founder_items:
                name_elem = item.find('div', class_='article-grid_author-name')
                role_elem = item.find('div', class_='article-grid_date-text')
                if name_elem:
                    founder_name = name_elem.get_text(strip=True)
                    founder_role = role_elem.get_text(strip=True) if role_elem else None
                    founder_dict = {'name': founder_name}
                    if founder_role:
                        founder_dict['role'] = founder_role
                    company['founders'].append(founder_dict)
    
    # Domains (categories)
    hidden_filters = container.find('div', class_='hidden-filters')
    if hidden_filters:
        domain_elems = hidden_filters.find_all('div', {'fs-cmsfilter-field': 'domain'})
        for elem in domain_elems:
            domain_text = elem.get_text(strip=True)
            if domain_text and domain_text not in company['domains']:
                company['domains'].append(domain_text)
    
    # Prior work (Wing Companies or Prior Work)
    if hidden_filters:
        prior_elem = hidden_filters.find('div', {'fs-cmsfilter-field': 'prior_work'})
        if prior_elem:
            prior_text = prior_elem.get_text(strip=True)
            if prior_text:
                company['prior_work'] = prior_text
    
    return company


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
                         "..", "data", "wingvc_companies.json")
    with open(_out, "w") as _f:
        _json.dump(_records, _f, indent=2, ensure_ascii=False)
        _f.write("\n")
    print(f"wrote {len(_records)} records")
