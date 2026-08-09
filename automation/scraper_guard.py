"""Guard for machine-generated scrapers — the checks that replace human review.

Three independent layers; ALL must pass before a generated scraper is committed:

1. static_check(code)  — AST allowlist. Rejects env access, subprocess, eval/exec,
   open()/file I/O, sockets, and any import off the whitelist. This blocks the
   "generated code exfiltrates tokens" class outright.
2. run_sandboxed(path) — executes scrape() in a subprocess with a SCRUBBED
   environment (no tokens exist in it even if layer 1 were beaten) and a timeout.
3. validate_output(records, baseline) — the data must look like a real, RICH
   portfolio: enough records vs the generic baseline, near-total names, solid
   URL + description coverage, low duplication. Catches empty, thin, and
   structurally-wrong results. (Honest limit: cannot catch subtly-wrong values.)
"""
from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import sys
from pathlib import Path

_ALLOWED_IMPORTS = {
    "requests", "bs4", "json", "re", "time", "datetime", "urllib",
    "urllib.parse", "html", "collections", "itertools", "typing",
    "math", "string", "unicodedata",
}
_BANNED_CALLS = {"eval", "exec", "compile", "__import__", "open", "input",
                 "breakpoint", "globals", "locals", "vars", "getattr", "setattr"}
_BANNED_ATTRS = {"environ", "getenv", "putenv", "system", "popen", "spawn",
                 "fork", "socket", "connect_ex"}


def static_check(code: str) -> list[str]:
    """Return a list of violations (empty = pass)."""
    problems: list[str] = []
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return [f"syntax error: {exc}"]
    # ast.parse is not the whole of "does this compile". Some errors are raised
    # only when the tree is turned into bytecode — duplicate keyword arguments,
    # for one, which is exactly what signalfire's generated scraper hit on
    # 2026-07-27: it passed this gate and blew up inside the sandbox instead.
    # The sandbox caught it, so nothing bad shipped, but failing here is faster
    # and gives the next try a clearer reason than a subprocess traceback.
    try:
        compile(code, "<candidate>", "exec")
    except SyntaxError as exc:
        return [f"syntax error: {exc.msg}"]
    except ValueError as exc:                 # e.g. source containing null bytes
        return [f"uncompilable: {exc}"]

    has_scrape = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "scrape":
            has_scrape = True
        if isinstance(node, ast.Import):
            for a in node.names:
                root = a.name.split(".")[0]
                if a.name not in _ALLOWED_IMPORTS and root not in _ALLOWED_IMPORTS:
                    problems.append(f"forbidden import: {a.name}")
        if isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            if node.module not in _ALLOWED_IMPORTS and root not in _ALLOWED_IMPORTS:
                problems.append(f"forbidden import-from: {node.module}")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in _BANNED_CALLS:
                problems.append(f"forbidden call: {node.func.id}()")
        if isinstance(node, ast.Attribute) and node.attr in _BANNED_ATTRS:
            problems.append(f"forbidden attribute: .{node.attr}")
        if isinstance(node, (ast.Global, ast.Nonlocal)):
            problems.append("forbidden global/nonlocal")
    if not has_scrape:
        problems.append("no scrape() function defined")
    return sorted(set(problems))


def run_sandboxed(candidate_path: str, timeout: int | None = None) -> tuple[list | None, str]:
    """Execute the candidate's scrape() with NO credentials in the environment.
    Returns (records, "") or (None, reason)."""
    runner = str(Path(__file__).resolve().parent / "scraper_runner.py")
    env = {"PATH": "/usr/bin:/bin:/usr/local/bin", "LANG": "C.UTF-8",
           "HOME": "/tmp", "PYTHONDONTWRITEBYTECODE": "1"}   # deliberately NO tokens
    try:
        proc = subprocess.run(
            [sys.executable, runner, candidate_path],
            capture_output=True, text=True, env=env,
            timeout=timeout or int(os.environ.get("GEN_RUN_TIMEOUT", "420")),
        )
    except subprocess.TimeoutExpired:
        return None, "sandbox timeout"
    if proc.returncode != 0:
        return None, f"sandbox exit {proc.returncode}: {proc.stderr.strip()[-400:]}"
    try:
        records = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None, "sandbox produced non-JSON output"
    return (records, "") if isinstance(records, list) else (None, "not a list")


# --- name-plausibility / self-sourcing patterns (see validate_output) --------
# A regional-indicator pair renders as a country flag. Portfolio pages love
# them; company names never contain one.
_FLAG_CHAR = re.compile(r"[\U0001F1E6-\U0001F1FF]")
# A status word welded onto the end of the previous field: "...USAActive".
# Requires a lowercase letter before the capital so genuine CamelCase names
# ("ActiveCampaign") and any name that merely ENDS in the word are untouched.
_GLUED_STATUS = re.compile(r"[a-z](Active|Acquired|Exited|RIP|IPO)$")
# A 4-digit year with letters on BOTH sides: "15Five2013San". Names legitimately
# ending in a year ("Studio 2049") or starting with one are left alone.
_NAME_YEAR_RUN = re.compile(r"[A-Za-z](?:19|20)\d{2}[A-Za-z]")
# Phrases that appear on a COMPANY's own site and never in an investor's
# write-up of it — the signature of a scraper that wandered off the firm's site.
_BOILERPLATE = re.compile(
    r"\b(enable javascript|cookie(s)? (policy|settings|preferences)|"
    r"accept (all )?cookies|request a demo|book a demo|sign in|sign up|"
    r"get started|download (the )?(logo|press|brand)|privacy policy|"
    r"all rights reserved|please reload this page|there was an error|"
    r"page not found|404)\b", re.I)
# Words that appear in scraped page furniture but not in personal names. Kept
# deliberately narrow — a surname denylist would misfire, so this only lists
# business/navigation vocabulary ("Financial Calculators", "Join Our").
_NON_PERSON = re.compile(
    r"\b(join|our|your|the|platform|solutions?|calculators?|monitoring|"
    r"management|teams?|discovery|experiences?|pricing|features?|products?|"
    r"services?|company|about|contact|careers?|blog|login|demo|resources?|"
    r"support|overview|inc|llc|ltd|gmbh)\b", re.I)


def validate_output(records: list, baseline_count: int = 0) -> list[str]:
    """Return a list of failures (empty = pass). `baseline_count` is what the
    generic extractor found for this site (0 if it found nothing)."""
    fails: list[str] = []
    n = len(records)
    if n < 10:
        fails.append(f"too few records ({n})")
    if baseline_count and n < 0.5 * baseline_count:
        fails.append(f"count {n} < 50% of generic baseline {baseline_count}")
    if any(not isinstance(r, dict) for r in records):
        return fails + ["non-dict records"]

    def cov(key_alts: tuple[str, ...]) -> float:
        hit = sum(1 for r in records
                  if any(r.get(k) not in (None, "", []) for k in key_alts))
        return hit / n if n else 0.0

    names = [str(r.get("company_name") or r.get("name") or "").strip() for r in records]
    if sum(1 for x in names if x) < 0.95 * n:
        fails.append("names missing on >5% of records")
    if n and len({x.lower() for x in names if x}) < 0.7 * n:
        fails.append("heavy duplication in names")
    if cov(("company_url", "url", "website")) < 0.6:
        fails.append("company_url coverage < 60%")
    if cov(("description", "tagline", "summary")) < 0.3:
        fails.append("description coverage < 30% (not rich)")

    # name PLAUSIBILITY, not just presence. pointninecapital shipped 25/25
    # records named "15Five2013<flag>San FranciscoUSAActive" — the generated
    # scraper called get_text() on a row whose name, year, city, country and
    # status are separate elements, so every field was glued into the name.
    # Coverage was a perfect 100%, so every existing gate passed. These three
    # shapes cannot occur in a real company name and are cheap to detect:
    glued = sum(1 for x in names if x and (
        _FLAG_CHAR.search(x)                       # country flag emoji
        or _GLUED_STATUS.search(x)                 # "...USAActive", "...UKRIP"
        or _NAME_YEAR_RUN.search(x)                # "Foo2013Bar" — year mid-name
    ))
    if names and glued > 0.1 * n:
        fails.append(
            f"implausible names on {glued}/{n} records — looks like whole rows "
            "were concatenated (use each element's own text, not get_text() on "
            "the container)")

    # SELF-SOURCING: descriptions must come from the FIRM's site, not from each
    # portfolio company's own homepage. Scraping company homepages to clear the
    # 30% description bar is gate-gaming; it drags in cookie banners and nav
    # text ("Download logo pack") and it broke the firm's-own-pages rule. The
    # tell is boilerplate that no investor would ever write about a portfolio
    # company.
    boiler = sum(1 for r in records
                 if isinstance(r.get("description"), str)
                 and _BOILERPLATE.search(r["description"]))
    if boiler > 0.1 * n:
        fails.append(
            f"boilerplate descriptions on {boiler}/{n} records — these look "
            "scraped from the companies' own homepages; read only the firm's "
            "own pages")

    # STUB FIELDS: keys present on every record but empty on ALL of them.
    #
    # ONE such field is usually legitimate and CLAUDE.md says so outright —
    # ticker for a portfolio of private companies, acquirer/exit_year where
    # nothing has exited, sectors where the firm never tags. Measured across the
    # repo, single-field cases are exactly those honest ones (sequoia
    # ticker_symbol, menlo sectors, stormventures founders). Failing them would
    # retire a firm for the site's editorial choices — and under
    # bespoke-or-nothing that deletes the dataset.
    #
    # THREE or more at once is a different animal: a schema the model padded to
    # look thorough. Every dataset over the line is factory-generated
    # (matrixpartners 4, mosaicventures 5, gradientventures 4, trueventures 4),
    # and none of the hand-written ones reach it. Hence the threshold.
    _STUB_LIMIT = 3
    if n:
        always_empty = sorted(
            k for k in records[0]
            if k not in ("everywhere_tags", "scraped_at")
            and all(r.get(k) in (None, "", []) for r in records))
        if len(always_empty) >= _STUB_LIMIT:
            fails.append(
                f"{len(always_empty)} fields empty on every record: "
                + ", ".join(always_empty[:5])
                + " — drop them from the schema or read them properly")

    # FOUNDERS PLAUSIBILITY: person names, not page furniture. matrixpartners
    # returned ['Join Our'], ['Financial Calculators'], ['Product Management']
    # alongside real founders, because the scraper harvested headings off the
    # companies' own sites. Structure alone cannot separate "Bea Patricia" from
    # "Park Ave", so this tests vocabulary: business/navigation words that no
    # one has as a personal name.
    fnames = [f for r in records for f in (r.get("founders") or [])
              if isinstance(f, str) and f.strip()]
    if fnames:
        junk = sum(1 for f in fnames if _NON_PERSON.search(f))
        if junk > 0.2 * len(fnames):
            fails.append(
                f"founders look like page headings on {junk}/{len(fnames)} "
                "entries (e.g. nav labels, product names) — read the firm's own "
                "founder field or leave the list empty")

    # Names carrying trailing punctuation are a slice that cut one character
    # wide ("Rainforest,", "Lightmatter,").
    ragged = sum(1 for x in names if x and x[-1] in ",;:·-–—|")
    if ragged > 0.05 * n:
        fails.append(f"trailing punctuation on {ragged}/{n} names "
                     "— the name selector is capturing a delimiter")

    # type integrity: a string value that PARSES as a list/dict is a stringified
    # structure ("['A', 'B']") — a real field the model flattened. Feeding the
    # field names back makes the next burst try fix it.
    bad_fields: set[str] = set()
    for r in records:
        for k, v in r.items():
            if not isinstance(v, str):
                continue
            s = v.strip()
            if len(s) < 2 or s[0] not in "[{" or s[-1] not in "]}":
                continue
            try:
                parsed = ast.literal_eval(s)
            except (ValueError, SyntaxError):
                try:
                    parsed = json.loads(s)
                except json.JSONDecodeError:
                    continue
            if isinstance(parsed, (list, dict)):
                bad_fields.add(k)
    if bad_fields:
        fails.append("stringified list/dict in field(s): "
                     + ", ".join(sorted(bad_fields)[:4]))
    return fails
