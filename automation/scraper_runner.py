"""Sandbox target: import a generated scraper module, call scrape(), print JSON.

Run BY scraper_guard in a subprocess whose environment is scrubbed of all
tokens — so even hostile generated code cannot read credentials. Usage:
    python3 scraper_runner.py /path/to/candidate_scraper.py
Prints the records as JSON on stdout; exits non-zero on any failure.
"""
from __future__ import annotations

import importlib.util
import json
import sys


def main() -> int:
    path = sys.argv[1]
    spec = importlib.util.spec_from_file_location("candidate_scraper", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)          # imports; network happens in scrape()
    records = mod.scrape()
    if not isinstance(records, list):
        print("scrape() did not return a list", file=sys.stderr)
        return 2
    json.dump(records, sys.stdout, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
