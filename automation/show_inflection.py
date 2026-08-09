"""Print the raw Airtable fields for the Inflection row — a one-off diagnostic."""
import json
import os

import requests

base = os.environ["AIRTABLE_BASE_ID"]
url = f"https://api.airtable.com/v0/{base}/{requests.utils.quote('Private Comps')}"
h = {"Authorization": f"Bearer {os.environ['AIRTABLE_PAT']}"}
r = requests.get(url, headers=h,
                 params={"filterByFormula": "{Data file}='inflection_companies.json'"},
                 timeout=30)
r.raise_for_status()
recs = r.json()["records"]
print(f"{len(recs)} row(s) with Data file = inflection_companies.json\n")
for rec in recs:
    print("record id:", rec["id"])
    print(json.dumps(rec["fields"], indent=2, ensure_ascii=False))
    print()
