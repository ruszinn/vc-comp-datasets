#!/usr/bin/env python3
"""
Fill missing info in companies.json / menlo_companies.json / usv_companies.json.

Two sources, both faithful (never overwrite a non-empty value, never fabricate):
  1. Re-tag: assign everywhere_tags to the handful of still-untagged Menlo records
     via a small description-justified override map.
  2. External enrichment via Wikidata (free + attributable): for each company that
     has a website, find its Wikidata item, VERIFY by matching the official-website
     (P856) domain, then backfill only EMPTY fields:
        founders (P112), year_founded (P571), sectors (P452 industry), ticker_symbol (P414/P249)
     Ambiguous name-only matches (no website match) are skipped -> no wrong data.

Writes the updated JSONs in place and an enrichment_report.json (provenance).

Only fields a record ALREADY defines are ever filled -- the datasets use
site-tailored schemas, so a file that carries no `sectors`/`founders`/... column
is left alone rather than having one introduced.

requirements: pip install requests
usage:
    python3 enrich.py                     # every file in FILES, full
    python3 enrich.py --limit 30          # only first 30 companies per file (testing)
    python3 enrich.py --files a.json,b.json   # restrict to specific files; report
                                              # entries for the others are preserved
"""

import json, os, re, sys, time
from urllib.parse import urlparse
import requests

# JSON data lives in ../data relative to this script
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "data")

WD_API = "https://www.wikidata.org/w/api.php"
HEADERS = {"User-Agent": "vc-comps-enrich/1.0 (https://github.com/ruszinn/vc-comp; ruszinfilay@gmail.com)"}
SLEEP = 0.15

FILES = ["companies.json", "menlo_companies.json", "usv_companies.json",
         "lererhippeau_companies.json", "2048_companies.json", "hustlefund_companies.json"]

GENERIC_SECTORS = {"technology", "technology industry", "software", "software industry",
                   "software company", "business", "company", "internet", "service industry"}

EXCH_SHORT = {
    "New York Stock Exchange": "NYSE", "Nasdaq": "NASDAQ", "Nasdaq Stock Market": "NASDAQ",
    "NASDAQ": "NASDAQ", "London Stock Exchange": "LSE", "Toronto Stock Exchange": "TSX",
    "Euronext": "Euronext", "Tel Aviv Stock Exchange": "TASE",
}

# Menlo untagged -> tags (justified by each company's own description)
MENLO_RETAG = {
    "Aisera": ["Future of Work", "Dev Tools / Cloud"],
    "Anthropic": ["Dev Tools / Cloud"],
    "Axiom": ["Dev Tools / Cloud"],
    "Delphi": ["Consumer", "Future of Work"],
    "Envoy": ["Future of Work"],
    "FireHydrant": ["Dev Tools / Cloud"],
    "FiveStars": ["Consumer"],
    "Ndea": ["Dev Tools / Cloud"],
    "Skild AI": ["Deeptech / Robotics / AR/VR"],
    "Usermind": ["Future of Work", "Data & Analytics"],
}

KEYWORD_TAGS = [
    ("BioTech", ["biotech", "drug", "therapeut", "oncolog", "cancer", "genomic", "genome", "molecul", "antibod", "vaccine", "medicine", "life science", "synthetic biology", "biolog"]),
    ("Health", ["healthcare", "health care", "patient", "clinic", "medical", "mental health", "telehealth", "diagnos", "surgical", "doctor", "hospital", "pharmac", "therapy"]),
    ("Cybersecurity", ["cybersecurity", "security", "secure", "privacy", "fraud", "phishing", "malware", "ransomware", "endpoint", "zero trust", "vulnerab", "authentication", "threat", "identity"]),
    ("FinTech / Insurance", ["fintech", "payment", "bank", "lending", "loan", "insurance", "credit", "trading", "wallet", "financ", "invoic", "accounting", "payroll", "billing", "tax", "brokerage"]),
    ("Web3 / Crypto", ["crypto", "blockchain", "web3", "token", "on-chain", "ethereum", "bitcoin", "decentral", "stablecoin", "nft"]),
    ("Gaming / Media / Entertainment", ["game", "gaming", "music", "video", "creator", "content", "publish", "entertain", "newsletter", "podcast", "film", "streaming", "media", "advertis", "marketing"]),
    ("Dev Tools / Cloud", ["developer", " api ", "apis", "infrastructure", "database", "cloud", "open source", "devops", "sdk", "kubernetes", "container", "observability", "deploy", "compute", "serverless", "software platform", "llm", "foundation model"]),
    ("Data & Analytics", ["analytics", "business intelligence", "data platform", "data warehouse", "data pipeline", "insights", "dashboard", "machine learning", "predictive", "data management"]),
    ("Future of Work", ["workforce", "hiring", "recruit", "employee", "productivity", "collaboration", "talent", "workplace", "human resources", " hr ", "customer service", "customer support", "workflow", "operations team", "sales team"]),
    ("Transportation / Mobility", ["mobility", "vehicle", "transport", "autonomous", "fleet", "driving", "aviation", "aircraft", "electric vehicle", "scooter", " bike"]),
    ("Logistics / Supply Chain", ["logistics", "supply chain", "freight", "warehouse", "delivery", "procurement", "inventory", "fulfillment", "shipping"]),
    ("PropTech", ["real estate", "property", "housing", "mortgage", "rental", "construction", "tenant"]),
    ("CPG", ["beverage", "snack", "consumer packaged", "beauty", "cosmetic", "apparel", "grocery", "skincare", "footwear"]),
    ("Climate / Sustainability", ["climate", "carbon", "renewable", "solar", "battery", "sustainab", "emission", "clean energy", "ev charging", "electrif"]),
    ("RegTech/Gov/Legal", ["legal", "compliance", "government", "regulat", "law firm", "attorney", "public sector"]),
    ("Deeptech / Robotics / AR/VR", ["robot", "hardware", "semiconductor", "chip", "drone", "aerospace", "augmented reality", "virtual reality", "satellite", "quantum", "sensor"]),
    ("Consumer", ["marketplace", "consumer", "shopping", "social network", "community", "app for", "ecommerce", "e-commerce", "subscription", "retailer", "education", "learning"]),
]


def kw_tags(name, description, sectors):
    text = f"{name or ''} {description or ''} {' '.join(sectors or [])}".lower()
    out = []
    for tag, kws in KEYWORD_TAGS:
        if any(k in text for k in kws) and tag not in out:
            out.append(tag)
    return out[:4]


def domain_of(url):
    if not url:
        return None
    try:
        net = urlparse(url if "//" in url else "//" + url, scheme="https").netloc.lower()
    except ValueError:
        return None
    net = net.split("@")[-1].split(":")[0]
    return net[4:] if net.startswith("www.") else net or None


def api(params):
    params = {**params, "format": "json"}
    for attempt in range(3):
        try:
            r = requests.get(WD_API, params=params, headers=HEADERS, timeout=30)
            r.raise_for_status()
            return r.json()
        except requests.RequestException:
            time.sleep(1.0 * (attempt + 1))
    return {}


def wd_match(name, target_domain):
    """Return (qid, claims) for the candidate whose P856 domain == target_domain, else (None,None)."""
    res = api({"action": "wbsearchentities", "search": name, "language": "en", "type": "item", "limit": 7})
    ids = [h["id"] for h in res.get("search", [])]
    if not ids:
        return None, None
    ents = api({"action": "wbgetentities", "ids": "|".join(ids), "props": "claims"}).get("entities", {})
    for qid in ids:
        claims = ents.get(qid, {}).get("claims", {})
        for c in claims.get("P856", []):
            v = c.get("mainsnak", {}).get("datavalue", {}).get("value")
            if isinstance(v, str) and domain_of(v) == target_domain:
                return qid, claims
    return None, None


def ent_ids(claims, pid):
    out = []
    for c in claims.get(pid, []):
        v = c.get("mainsnak", {}).get("datavalue", {}).get("value")
        if isinstance(v, dict) and v.get("id"):
            out.append(v["id"])
    return out


def inception_year(claims):
    for c in claims.get("P571", []):
        t = c.get("mainsnak", {}).get("datavalue", {}).get("value", {}).get("time")
        if t:
            m = re.search(r"(\d{4})", t)
            if m and m.group(1) != "0000":
                return int(m.group(1))
    return None


def ticker_pair(claims):
    """(exchange_qid, ticker_string) from first P414 with a P249 qualifier."""
    for c in claims.get("P414", []):
        exch = c.get("mainsnak", {}).get("datavalue", {}).get("value", {}).get("id")
        for q in c.get("qualifiers", {}).get("P249", []):
            tk = q.get("datavalue", {}).get("value")
            if isinstance(tk, str) and tk.strip():
                return exch, tk.strip()
    return None, None


def get_labels(ids):
    ids = [i for i in dict.fromkeys(ids) if i]
    out = {}
    for i in range(0, len(ids), 50):
        ents = api({"action": "wbgetentities", "ids": "|".join(ids[i:i + 50]),
                    "props": "labels", "languages": "en"}).get("entities", {})
        for q, e in ents.items():
            out[q] = (e.get("labels", {}).get("en", {}) or {}).get("value")
    return out


def main():
    limit = None
    if "--limit" in sys.argv:
        limit = int(sys.argv[sys.argv.index("--limit") + 1])

    files = FILES
    if "--files" in sys.argv:
        files = [f.strip() for f in sys.argv[sys.argv.index("--files") + 1].split(",") if f.strip()]
        unknown = [f for f in files if not os.path.exists(os.path.join(DATA_DIR, f))]
        if unknown:
            raise SystemExit(f"FATAL: no such data file(s): {unknown}")

    # The report is provenance for every fill ever made, so a partial run must NOT
    # discard the entries for files it isn't touching. Carry those forward and
    # regenerate only the ones for the files in this run (keeps it idempotent).
    report_path = os.path.join(DATA_DIR, "enrichment_report.json")
    carried = []
    if os.path.exists(report_path):
        try:
            carried = [r for r in json.load(open(report_path)) if r.get("file") not in files]
        except (json.JSONDecodeError, OSError):
            carried = []
    if carried:
        print(f"carrying forward {len(carried)} report entries for files not in this run")

    report = []
    for fname in files:
        data = json.load(open(os.path.join(DATA_DIR, fname)))
        rows = data[:limit] if limit else data
        print(f"\n=== {fname} ({len(rows)} of {len(data)}) ===")

        # ---- Part 1: Menlo re-tag overrides ----
        if fname == "menlo_companies.json":
            for o in data:
                if not o.get("everywhere_tags") and o["company_name"] in MENLO_RETAG:
                    o["everywhere_tags"] = MENLO_RETAG[o["company_name"]]
                    report.append({"file": fname, "company": o["company_name"],
                                   "source": "retag", "filled": {"everywhere_tags": o["everywhere_tags"]}})

        # ---- Part 3: Wikidata enrichment ----
        pending_label_ids, pending = [], []   # collect, resolve labels in batches at end
        for i, o in enumerate(rows, 1):
            dom = domain_of(o.get("company_url"))
            if not dom:
                continue
            # Every target field is optional: the datasets use site-tailored schemas,
            # so a file may simply not carry a `sectors` / `founders` / ... column
            # (e.g. 2048_companies.json has none of them). Only ever consider a
            # field that the record actually defines -- never introduce a new column.
            has_founders = "founders" in o
            has_year = "year_founded" in o
            has_sectors = "sectors" in o
            need = ((has_founders and not o["founders"]) or (has_year and not o["year_founded"])
                    or (has_sectors and not o["sectors"])
                    or ("ticker_symbol" in o and not o["ticker_symbol"]))
            if not need:
                continue
            qid, claims = wd_match(o["company_name"], dom)
            time.sleep(SLEEP)
            if not qid:
                continue
            founders = ent_ids(claims, "P112")
            industries = ent_ids(claims, "P452")
            exch, tk = ticker_pair(claims)
            yr = inception_year(claims)
            pending.append({"o": o, "fname": fname, "qid": qid, "founders": founders,
                            "industries": industries, "exch": exch, "ticker": tk, "year": yr,
                            "has_founders": has_founders, "has_year": has_year,
                            "has_sectors": has_sectors})
            pending_label_ids += founders + industries + ([exch] if exch else [])
            if i % 50 == 0:
                print(f"  matched-scan {i}/{len(rows)}")

        labels = get_labels(pending_label_ids)

        for p in pending:
            o, filled = p["o"], {}
            if p["has_founders"] and not o["founders"] and p["founders"]:
                names = [labels.get(q) for q in p["founders"] if labels.get(q)]
                if names:
                    o["founders"] = names
                    filled["founders"] = names
            if p["has_year"] and not o["year_founded"] and p["year"]:
                o["year_founded"] = p["year"]
                filled["year_founded"] = p["year"]
            if p["has_sectors"] and not o["sectors"] and p["industries"]:
                secs = [labels.get(q) for q in p["industries"]
                        if labels.get(q) and labels[q].lower() not in GENERIC_SECTORS]
                if secs:
                    o["sectors"] = secs
                    filled["sectors"] = secs
            if "ticker_symbol" in o and not o["ticker_symbol"] and p["ticker"]:
                exch_lbl = labels.get(p["exch"]) or ""
                short = EXCH_SHORT.get(exch_lbl, exch_lbl)
                o["ticker_symbol"] = f"{short}: {p['ticker']}".strip(": ").strip()
                filled["ticker_symbol"] = o["ticker_symbol"]
            # recompute tags only if still untagged and we now have sectors/description
            if not o.get("everywhere_tags"):
                t = kw_tags(o["company_name"], o.get("description"), o.get("sectors"))
                if t:
                    o["everywhere_tags"] = t
                    filled["everywhere_tags"] = t
            if filled:
                report.append({"file": fname, "company": o["company_name"],
                               "source": "wikidata", "wikidata_id": p["qid"], "filled": filled})

        json.dump(data, open(os.path.join(DATA_DIR, fname), "w"), ensure_ascii=False, indent=2)
        print(f"  wrote {fname}; enriched {sum(1 for r in report if r['file']==fname and r['source']=='wikidata')} via Wikidata")

    json.dump(carried + report, open(report_path, "w"), ensure_ascii=False, indent=2)
    print(f"\nTotal fills this run: {len(report)} "
          f"({len(carried) + len(report)} in enrichment_report.json)")


if __name__ == "__main__":
    main()
