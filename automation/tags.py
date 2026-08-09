"""The everywhere-venture tag taxonomy: counting + shared keyword classifier.

The 17 tags are fixed (verbatim from CLAUDE.md). `count_tags` counts, for one
firm's records, how many companies carry each tag. A company with several tags is
counted once per tag — deliberate double-counting across columns, per the spec —
so the per-firm counts are a sector-exposure profile, not a partition.

THE TAGS ARE THE PRODUCT (user, 2026-07-27): the manager's dashboard agent builds
comps by looking up everywhere_tags, so an untagged dataset is dead weight to the
system's main consumer however many companies it holds. `classify`/`fill_empty`
give factory-generated and generic-extractor output the same keyword tagging the
hand-written scrapers do. The keyword map is lifted verbatim from
lererhippeau_scraper.py — the most recently tuned copy of the map the bespoke
scrapers share — minus its firm-specific supplement. Same conventions as always:
no LLM, intentionally coarse, a few untagged stragglers acceptable, cap 4.
"""
from __future__ import annotations

TAGS = [
    "BioTech", "Health", "Cybersecurity", "Dev Tools / Cloud", "Consumer",
    "Future of Work", "Transportation / Mobility", "FinTech / Insurance",
    "RegTech/Gov/Legal", "Deeptech / Robotics / AR/VR", "Data & Analytics",
    "Logistics / Supply Chain", "Web3 / Crypto", "PropTech",
    "Gaming / Media / Entertainment", "CPG", "Climate / Sustainability",
]
_TAGSET = set(TAGS)


def count_tags(records: list[dict] | None) -> dict:
    """{tag: count} across a firm's company records. Unknown/None tags ignored."""
    counts = {t: 0 for t in TAGS}
    for rec in records or []:
        for t in (rec.get("everywhere_tags") or []):
            if t in _TAGSET:
                counts[t] += 1
    return counts


# Keyword map: (tag, substrings) in rough precedence order. List order stands in
# for relevance ordering, as in the scrapers. Checked substrings, not words —
# hence guards like the machine-learning neutralization in classify().
_KEYWORD_TAGS = [
    ("BioTech", ["biotech", "drug", "therapeut", "oncolog", "cancer", "tumor",
                 "genomic", "genome", "molecul", "antibod", "protein", "vaccine",
                 "clinical-stage", "medicine", "opioid", "life science",
                 "synthetic biology", "biolog", "biomedical"]),
    ("Health", ["healthcare", "health care", "patient", "clinic", "medical",
                "mental health", "telehealth", "health system", "health record",
                "diagnos", "surgical", "doctor", "hospital", "pharmac", "therapy",
                "health plan", "prior authorization", "health assistant",
                "health data"]),
    ("Cybersecurity", ["cybersecurity", "security", "secure", "privacy", "fraud",
                       "phishing", "malware", "ransomware", "endpoint",
                       "zero trust", "vulnerab", "authentication", "threat",
                       "defense system", "identity", "information protection",
                       "kyb", "compliance for ai"]),
    ("FinTech / Insurance", ["fintech", "payment", "bank", "lending", "loan",
                             "insurance", "insurtech", "credit", "trading",
                             "wallet", "financ", "invoic", "accounting",
                             "payroll", "treasury", "billing", "pricing platform",
                             "rebate", " tax", "audit", "money management",
                             "robo-advisor", "brokerage", "spend management",
                             "capital markets", "investing", "claims",
                             "coverage plans", "underwriting",
                             # added 2026-07-27 (EarnIn: "waiting for payday"
                             # tagged Consumer-only via "app that")
                             "payday", "paycheck", "earned wage"]),
    ("Web3 / Crypto", ["crypto", "blockchain", "web3", "token", "on-chain",
                       "ethereum", "bitcoin", "decentral", "stablecoin", "nft"]),
    ("Gaming / Media / Entertainment", ["game", "gaming", "music", "video",
                                        "creator", "content", "publish",
                                        "entertain", "newsletter", "podcast",
                                        "film", "streaming", "social media",
                                        "media platform", "sports network",
                                        "filmmaker", "motion graphics",
                                        "audio file"]),
    ("Dev Tools / Cloud", ["developer", " api ", "apis", "api platform",
                           "infrastructure", "database", "cloud", "open source",
                           "devops", "sdk", "kubernetes", "container",
                           "observability", "deploy", "compute", "storage",
                           "serverless", "inference", "networking", "ethernet",
                           "coding", "codebase", "low-code", "no-code",
                           "source code", "development platform", "incident",
                           " sre", "voicemail", "communications", "llm",
                           "foundation model", "interpretability",
                           "code-automation", "event-driven", "log management",
                           "file sharing", "tech stack", "voice agent",
                           "appliance software", "text to speech",
                           "operationalize ai", "notifications for engineering",
                           "spreadsheet", "data importer"]),
    ("Data & Analytics", ["analytics", "business intelligence", "data platform",
                          "data warehouse", "data lake", "data pipeline",
                          "insights", "dashboard", "experimentation",
                          "decision intelligence", "data quality", "analyz",
                          "data curation", "quality management",
                          "relationship intelligence", "data discovery",
                          "data analysis", "edge-data", "complex data",
                          "real-world data", "analyst", "data intelligence",
                          "data transformation", "data integration",
                          "data management", "buyer intent",
                          "curated coding data"]),
    ("Future of Work", ["workforce", "hiring", "recruit", "employee",
                        "productivity", "collaborat", "talent", "workplace",
                        "human resources", " hr ", "learning platform",
                        "customer success", "customer service",
                        "customer support", "presales", " sales ", "onboarding",
                        "workflow", "saas management", "ai assistant",
                        "project management", "partnerships platform",
                        "partnership", "teamwork", "scheduling",
                        "work assistant", "sales engineer", "sales teams",
                        "for managers", "team wiki", "cleaning companies",
                        "call center", "answering service", "coaching",
                        "well-being benefits", "presentation", "email", "inbox",
                        "your notes"]),
    ("Transportation / Mobility", ["mobility", "vehicle", "transport",
                                   "autonomous", "fleet", "driving", "aviation",
                                   "aircraft", "electric vehicle", "scooter",
                                   " bike", "boat", "watercraft", "rideshar",
                                   "travel", "automotive"]),
    ("Logistics / Supply Chain", ["logistics", "supply chain",
                                  "supply and demand", "freight", "warehouse",
                                  "delivery", "procurement", "inventory",
                                  "fulfillment", "shipping", "container trucking",
                                  "last-mile", "distribution", "global supply"]),
    ("PropTech", ["real estate", "property", "housing", "mortgage", "rental",
                  "construction", "tenant", "home construction", "renovation",
                  "rent"]),
    ("CPG", ["beverage", "snack", "consumer packaged", "beauty", "cosmetic",
             "apparel", "grocery", "skincare", "eyewear", "glasses", "footwear",
             "pet sitter", "pet ", "fashion brand", "secondhand"]),
    ("Climate / Sustainability", ["climate", "carbon", "renewable", "solar",
                                  "battery", "sustainab", "emission",
                                  "clean energy", "ev charging", "electrif",
                                  "energy", "power is produced", "power grid"]),
    ("RegTech/Gov/Legal", ["legal", "compliance", "government", "regulat",
                           "law firm", "attorney", "risk services", "lawsuit",
                           "lawyer", "legal space", "ip protection",
                           "prior authorization"]),
    ("Deeptech / Robotics / AR/VR", ["robot", "hardware", "semiconductor",
                                     "chip", "drone", "aerospace",
                                     "augmented reality", "virtual reality",
                                     "satellite", "quantum", "sensor", "rfid",
                                     "wifi", "space", "rocket", "launch vehicle",
                                     "optics", "defense", "warehouse automation",
                                     "wireless internet",
                                     "vertically integrated home"]),
    ("Consumer", ["marketplace", "consumer", "shopping", "social network",
                  "community", "app for", "app that", "ecommerce", "e-commerce",
                  "subscription", "retailer", "universit", "student",
                  "education", "learning", "fashion", "parents", "creators",
                  "fitness", "gig economy", "discounts", "tutor"]),
]


def classify(name, description, sectors=None) -> list:
    """Up to 4 taxonomy tags for one company, keyword-classified from name +
    description + the firm's own sector labels (folded into the same text, so a
    label like "Fintech" lands through the same keyword route — no second map).
    The AI rule from CLAUDE.md is enforced structurally: there is no AI tag, and
    "machine/deep learning" is neutralized so an ML company classifies by the
    market it serves, not by the word "learning" (which belongs to education)."""
    if isinstance(sectors, str):
        sectors = [sectors]
    text = " ".join(str(x) for x in [name, description, *(sectors or [])]
                    if x).lower()
    text = text.replace("machine learning", "ai").replace("deep learning", "ai")
    out = [tag for tag, kws in _KEYWORD_TAGS if any(kw in text for kw in kws)]
    return out[:4]


def _match_key(rec: dict) -> str:
    """Identity of a company across two scrapes of the same firm: normalized
    company_url first (names get restyled; domains rarely do), else name."""
    url = (rec.get("company_url") or rec.get("url") or "").lower()
    url = url.split("//")[-1]
    url = url[4:] if url.startswith("www.") else url
    url = url.rstrip("/")
    if url:
        return "u:" + url
    name = (rec.get("company_name") or rec.get("name") or "").strip().lower()
    return ("n:" + name) if name else ""


def carry_forward(old_records, new_records) -> int:
    """Fresh records with EMPTY tags inherit the previous dataset's tags for
    the same company. This is what makes one-off enrichment (the LLM backfill,
    a Wikidata fill, a hand fix) durable: a weekly refresh rebuilds records
    from scratch, so without inheritance every tag not re-derivable from that
    night's scrape text would be silently wiped. A fresh record that already
    carries tags wins — the scraper's own tagging is newer information.
    Returns how many records inherited."""
    prev = {}
    for r in old_records or []:
        k = _match_key(r)
        if k and r.get("everywhere_tags"):
            prev[k] = r["everywhere_tags"]
    n = 0
    for r in new_records or []:
        if not r.get("everywhere_tags"):
            t = prev.get(_match_key(r))
            if t:
                r["everywhere_tags"] = list(t)
                n += 1
    return n


def fill_empty(records) -> int:
    """Fill everywhere_tags on records where it is empty or missing; NEVER
    overwrites a non-empty list, so a hand-written scraper's own (usually
    firm-sector-informed) tagging always wins. Returns how many records were
    newly tagged. Tolerates both name-field conventions and str/list sectors."""
    n = 0
    for rec in records or []:
        if not rec.get("everywhere_tags"):
            t = classify(rec.get("company_name") or rec.get("name"),
                         rec.get("description"),
                         rec.get("sectors") or rec.get("sector"))
            if t:
                rec["everywhere_tags"] = t
                n += 1
    return n
