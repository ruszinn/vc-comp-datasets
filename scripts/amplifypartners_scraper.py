# AUTO-GENERATED scraper (Claude API) — passed static guard + sandboxed
# validation before commit. Regenerate rather than hand-edit heavily.
"""
Amplify Partners portfolio scraper.
Extracts company data from https://amplifypartners.com/portfolio/company
"""

import requests
from bs4 import BeautifulSoup
import time
import re
from typing import Optional

def scrape() -> list[dict]:
    """
    Scrape Amplify Partners portfolio companies.
    
    Returns:
        List of dictionaries, one per portfolio company.
    """
    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    })
    
    portfolio_url = 'https://amplifypartners.com/portfolio/company'
    companies = []
    
    try:
        resp = session.get(portfolio_url, timeout=20)
        resp.raise_for_status()
    except Exception as e:
        print(f"Error fetching portfolio page: {e}")
        return []
    
    soup = BeautifulSoup(resp.text, 'html.parser')
    
    # Find all company cards - they have class "co-card" and id starting with "co-"
    company_cards = soup.find_all('div', class_='co-card', id=re.compile(r'^co-'))
    
    for card in company_cards:
        try:
            company_data = {
                'company_name': None,
                'company_url': None,
                'description': None,
                'sector': None,
                'status': None,
                'team': [],
                'founded_year': None,
                'partnered_year': None,
                'linkedin_url': None,
                'twitter_url': None,
                'related_articles': [],
                'logo_url': None,
                'everywhere_tags': [],
                'source_url': portfolio_url
            }
            
            # Extract company name from the spotlight-co-name span
            name_elem = card.find('span', class_='spotlight-co-name')
            if name_elem:
                company_data['company_name'] = name_elem.get_text(strip=True)
            
            # Extract sector and status from co-meta spans
            meta_texts = card.find_all('span', class_='co-meta-text')
            if len(meta_texts) >= 1:
                company_data['sector'] = meta_texts[0].get_text(strip=True)
            if len(meta_texts) >= 2:
                company_data['status'] = meta_texts[1].get_text(strip=True)
            
            # Extract data from the expanded content section
            content_div = card.find('div', class_='co-content-inner')
            if content_div:
                # Extract logo
                logo_img = content_div.find('img', class_='co-logo-img')
                if logo_img and logo_img.get('src'):
                    logo_src = logo_img['src']
                    if logo_src.startswith('/'):
                        logo_src = f"https://amplifypartners.com{logo_src}"
                    company_data['logo_url'] = logo_src
                
                # Extract links (LinkedIn, Twitter/X, company website)
                expand_links = content_div.find('div', class_='co-expand-links')
                if expand_links:
                    for link in expand_links.find_all('a', class_='co-expand-link'):
                        href = link.get('href', '')
                        text = link.get_text(strip=True).lower()
                        
                        if 'linkedin.com' in href:
                            company_data['linkedin_url'] = href
                        elif 'twitter.com' in href or 'x.com' in href or text == 'x':
                            company_data['twitter_url'] = href
                        elif 'co-expand-link--pill' in link.get('class', []):
                            # This is the main company website
                            company_data['company_url'] = href
                
                # Extract description
                about_col = content_div.find('div', class_='co-expand-col')
                if about_col:
                    about_label = about_col.find('span', class_='co-expand-label')
                    if about_label and 'About' in about_label.get_text(strip=True):
                        desc_p = about_col.find('p', class_='co-expand-text')
                        if desc_p:
                            company_data['description'] = desc_p.get_text(strip=True)
                
                # Extract team members
                team_cols = content_div.find_all('div', class_='co-expand-col')
                for col in team_cols:
                    label = col.find('span', class_='co-expand-label')
                    if label and 'Team' in label.get_text(strip=True):
                        team_div = col.find('div', class_='co-expand-text')
                        if team_div:
                            for member_div in team_div.find_all('div'):
                                member_name = member_div.get_text(strip=True)
                                if member_name:
                                    company_data['team'].append(member_name)
                
                # Extract milestones (founded/partnered years)
                for col in team_cols:
                    label = col.find('span', class_='co-expand-label')
                    if label and 'Milestones' in label.get_text(strip=True):
                        milestone_div = col.find('div', class_='co-expand-text')
                        if milestone_div:
                            for milestone in milestone_div.find_all('div'):
                                text = milestone.get_text(strip=True)
                                # Extract year from "Founded YYYY" or "Partnered YYYY"
                                if 'Founded' in text:
                                    match = re.search(r'\d{4}', text)
                                    if match:
                                        company_data['founded_year'] = int(match.group())
                                elif 'Partnered' in text:
                                    match = re.search(r'\d{4}', text)
                                    if match:
                                        company_data['partnered_year'] = int(match.group())
                
                # Extract related articles
                related_col = content_div.find('div', class_='co-expand-col--related')
                if related_col:
                    articles_div = related_col.find('div', class_='co-expand-articles')
                    if articles_div:
                        for article_link in articles_div.find_all('a', class_='co-expand-article'):
                            article_title_span = article_link.find('span')
                            if article_title_span:
                                article_title = article_title_span.get_text(strip=True)
                                article_url = article_link.get('href', '')
                                if article_title and article_url:
                                    company_data['related_articles'].append({
                                        'title': article_title,
                                        'url': article_url
                                    })
            
            # Only add if we have at least a company name
            if company_data['company_name']:
                companies.append(company_data)
        
        except Exception as e:
            print(f"Error parsing company card: {e}")
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
                         "..", "data", "amplifypartners_companies.json")
    with open(_out, "w") as _f:
        _json.dump(_records, _f, indent=2, ensure_ascii=False)
        _f.write("\n")
    print(f"wrote {len(_records)} records")
