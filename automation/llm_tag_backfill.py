"""Tag backfill for companies with NO text signal: fetch-first, LLM last.

Context (2026-07-27, user decisions): the tags are the product — the dashboard
agent builds comps from everywhere_tags, and "if the EV tags are missing, I do
not need the data." After the shared keyword tagger ran over every dataset,
~1,900 companies remained untaggable because their records carry no description
or sectors (coatue, generalcatalyst, ribbit, wingvc, signalfire…).

But nearly all of them carry a company_url — the answer's address is already in
the record. So the pass is three stages, cheapest first:

  1. FETCH each untagged company's own homepage (house fetcher, one request per
     company, $0) and distill a text signal: title + meta/og description + the
     first stretch of visible text. Guarded against the two ways a homepage
     lies: parked/for-sale domains (a dead startup's domain gets squatted, and
     "buy this domain" must not classify as anything) and JS shells that serve
     no text.
  2. KEYWORD-classify the fetched text with tags.classify — free, resolves the
     majority.
  3. LLM (Haiku) for the leftovers, now grounded in the fetched text when there
     is any, falling back to the model's own knowledge of the company when
     there isn't. This is the ONE sanctioned exception to the no-LLM tagging
     convention (user decision).

Honesty rules:
  * fetched homepage text is used ONLY as classification input and logged to
    the report — it is never written into the dataset's `description`, which
    stays "what the VC firm's site publishes" (the provenance line the
    matrixpartners scraper blurred);
  * the model must return [] for companies it cannot confidently classify —
    empty is correct, guessed is an error; every returned tag is validated
    verbatim against the taxonomy, cap 4;
  * fills ONLY records whose everywhere_tags is still empty at write time;
  * every fill is logged to data/llm_tag_report.json with how it was derived
    (homepage-keywords | llm+homepage | llm-name-only) and the snippet used;
  * LLM spend is booked to the ledger (data/spend.json, source "tag-backfill").

Keyword fills are banked to disk before the LLM stage starts, so a crash or a
budget refusal keeps the free gains. Survives weekly refreshes via
tags.carry_forward. Idempotent: re-runs touch only still-empty tags.

Run from the repo root ON A MACHINE WITH NETWORK ACCESS (the Cowork sandbox can
reach neither company sites nor api.anthropic.com):

    python3 automation/llm_tag_backfill.py --limit 40   # smoke test
    python3 automation/llm_tag_backfill.py              # full pass (~45 min,
                                                        #  LLM cost ~$0.1-0.3)

Reads ANTHROPIC_API_KEY from the environment or automation/.env.
--skip-fetch restores the name-only behaviour if fetching is ever unwanted.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import budget  # noqa: E402
import extract  # noqa: E402
import tags  # noqa: E402

_DATA = Path(__file__).resolve().parent.parent / "data"
_REPORT = _DATA / "llm_tag_report.json"
_BATCH = 40
_SNIPPET = 220          # chars of homepage text shown to the model / report
_MIN_SIGNAL = 30        # shorter than this = a JS shell, not a description
_MODEL_DEFAULT = os.environ.get("LLM_TAG_MODEL", "claude-haiku-4-5")

# A squatted domain must classify as NOTHING. These phrases are registrar
# boilerplate, checked as substrings of the distilled signal (lowercased).
_PARKED = ("domain is for sale", "domain may be for sale", "buy this domain",
           "this website is for sale", "domain parking", "parked free",
           "godaddy", "sedo", "hugedomains", "afternic", "dan.com",
           "checkout the full domain details", "make an offer on this domain")

_PROMPT = """You classify venture-backed companies into a fixed tag taxonomy.

The ONLY allowed tags (verbatim):
{taxonomy}

Rules:
- 0 to 4 tags per company, most relevant first; most companies need 1-2.
- AI is not a category: classify an AI company by the market it serves.
- Lines may include a quoted snippet from the company's own homepage — that is
  your best evidence. The investing firm in parentheses is weak context only.
- If there is no snippet and you do not confidently recognize the company,
  return [] for it. An empty answer is correct and welcome; a guessed tag is
  an error.

Companies:
{companies}

Reply with ONLY a JSON object mapping each number to its tag list, e.g.
{{"1": ["FinTech / Insurance"], "2": []}}"""


def _load_env() -> None:
    p = Path(__file__).resolve().parent / ".env"
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _url(rec: dict) -> str:
    u = (rec.get("company_url") or rec.get("url") or "").strip()
    if u and not u.startswith("http"):
        u = "https://" + u
    return u


def page_signal(html: str | None) -> str:
    """Distill one homepage into classification text: title + meta/og
    description + the first stretch of visible body text. Returns "" for
    anything that must not classify: no HTML, a parked/for-sale page, or a
    client-rendered shell with no real text."""
    if not html:
        return ""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    for t in soup(["script", "style", "noscript"]):
        t.decompose()
    parts = []
    if soup.title and soup.title.get_text(strip=True):
        parts.append(soup.title.get_text(strip=True))
    for sel in ('meta[name="description"]', 'meta[property="og:description"]'):
        m = soup.select_one(sel)
        if m and m.get("content"):
            parts.append(m["content"].strip())
    body = re.sub(r"\s+", " ", soup.get_text(" ", strip=True))
    parts.append(body[:400])
    text = re.sub(r"\s+", " ", " ".join(parts)).strip()
    if len(text) < _MIN_SIGNAL:
        return ""                                   # JS shell / empty page
    low = text.lower()
    if any(p in low for p in _PARKED):
        return ""                                   # squatted domain
    return text


def collect() -> list[tuple[Path, int, dict]]:
    """(file, record-index, record) for every untagged company, stable order."""
    out = []
    for p in sorted(_DATA.glob("*_companies.json")):
        recs = json.loads(p.read_text())
        for i, r in enumerate(recs):
            if not r.get("everywhere_tags") and (
                    r.get("company_name") or r.get("name")):
                out.append((p, i, r))
    return out


def parse_reply(text: str, n: int) -> dict[int, list[str]]:
    """Strict-ish parse: first JSON object in the reply; every tag must be
    verbatim taxonomy or it is dropped; cap 4; unknown keys ignored."""
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return {}
    try:
        raw = json.loads(m.group(0))
    except json.JSONDecodeError:
        return {}
    tagset = set(tags.TAGS)
    out: dict[int, list[str]] = {}
    for k, v in (raw.items() if isinstance(raw, dict) else []):
        try:
            i = int(k)
        except (TypeError, ValueError):
            continue
        if 1 <= i <= n and isinstance(v, list):
            clean = [t for t in v if t in tagset]
            out[i] = list(dict.fromkeys(clean))[:4]
    return out


def _call(model: str, prompt: str) -> tuple[str, dict]:
    import requests
    r = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={"x-api-key": os.environ["ANTHROPIC_API_KEY"],
                 "anthropic-version": "2023-06-01",
                 "content-type": "application/json"},
        json={"model": model, "max_tokens": 1500, "temperature": 0,
              "messages": [{"role": "user", "content": prompt}]},
        timeout=120)
    r.raise_for_status()
    d = r.json()
    text = "".join(b.get("text", "") for b in d.get("content", []))
    return text, d.get("usage", {})


class _State:
    """Shared bookkeeping for both fill stages."""
    def __init__(self):
        self.cache: dict[Path, list] = {}
        self.dirty: set[Path] = set()
        self.report_fills: dict = {}
        self.filled = 0
        self.done: set[tuple[str, int]] = set()   # (file name, record index)

    def fill(self, p: Path, i: int, got: list[str], via: str,
             snippet: str = "") -> None:
        recs = self.cache.setdefault(p, json.loads(p.read_text()))
        if recs[i].get("everywhere_tags"):
            return                               # something else won the race
        recs[i]["everywhere_tags"] = got
        name = recs[i].get("company_name") or recs[i].get("name")
        entry = {"tags": got, "via": via}
        if snippet:
            entry["snippet"] = snippet[:_SNIPPET]
        self.report_fills.setdefault(p.name, {})[name] = entry
        self.filled += 1
        self.dirty.add(p)
        self.done.add((p.name, i))

    def flush(self) -> None:
        for p in sorted(self.dirty):
            p.write_text(json.dumps(self.cache[p], indent=2,
                                    ensure_ascii=False) + "\n")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0,
                    help="max companies this run (0 = all)")
    ap.add_argument("--model", default=_MODEL_DEFAULT)
    ap.add_argument("--skip-fetch", action="store_true",
                    help="no homepage fetches; LLM on names alone")
    ap.add_argument("--dry-run", action="store_true",
                    help="collect and print counts; no fetches, calls or writes")
    args = ap.parse_args()

    _load_env()
    todo = collect()
    if args.limit:
        todo = todo[: args.limit]
    print(f"[tag-backfill] {len(todo)} untagged companies")
    if args.dry_run or not todo:
        per_file: dict[str, int] = {}
        for p, _, _ in todo:
            per_file[p.name] = per_file.get(p.name, 0) + 1
        for f, n in sorted(per_file.items(), key=lambda x: -x[1])[:10]:
            print(f"    {f:40s} {n}")
        return 0

    st = _State()
    snippets: dict[tuple[str, int], str] = {}     # (file, idx) -> homepage text

    # ---- stage 1+2: fetch each homepage, keyword-classify its text ($0) ----
    if not args.skip_fetch:
        kw = 0
        for k, (p, i, r) in enumerate(todo, 1):
            u = _url(r)
            sig = page_signal(extract.fetch(u, timeout=15)) if u else ""
            if sig:
                snippets[(p.name, i)] = sig
                name = r.get("company_name") or r.get("name")
                got = tags.classify(name, sig)
                if got:
                    st.fill(p, i, got, "homepage-keywords", sig)
                    kw += 1
            if k % 100 == 0:
                print(f"[tag-backfill] fetched {k}/{len(todo)}, "
                      f"{kw} keyword-tagged", flush=True)
            time.sleep(0.1)
        st.flush()                    # bank the free gains before spending
        print(f"[tag-backfill] fetch stage done: {kw} tagged from homepages, "
              f"{len(snippets)} homepages readable")

    # ---- stage 3: LLM for whatever is still empty ----
    remaining = [(p, i, r) for (p, i, r) in todo
                 if (p.name, i) not in st.done]
    print(f"[tag-backfill] {len(remaining)} left for the LLM stage")
    if remaining:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            print("[tag-backfill] no ANTHROPIC_API_KEY — keyword fills kept, "
                  "LLM stage skipped")
            remaining = []
        else:
            bstate = budget.load()
            budget.activate(bstate)
            ok_budget, why = budget.can_find(bstate)
            if not ok_budget:
                print(f"[tag-backfill] LLM stage refused: {why} "
                      "(keyword fills kept)")
                remaining = []

    cost = 0.0
    taxonomy = "\n".join(f"- {t}" for t in tags.TAGS)
    for start in range(0, len(remaining), _BATCH):
        batch = remaining[start:start + _BATCH]
        lines = []
        for j, (p, i, r) in enumerate(batch, 1):
            name = r.get("company_name") or r.get("name")
            firm = p.name[: -len("_companies.json")]
            dom = _url(r).split("//")[-1].split("/")[0]
            line = f"{j}. {name}" + (f" — {dom}" if dom else "") \
                   + f" (portfolio of {firm})"
            sig = snippets.get((p.name, i))
            if sig:
                line += f': "{sig[:_SNIPPET]}"'
            lines.append(line)
        prompt = _PROMPT.format(taxonomy=taxonomy, companies="\n".join(lines))
        try:
            text, usage = _call(args.model, prompt)
        except Exception as exc:  # noqa: BLE001 — keep progress, stop clean
            print(f"[tag-backfill] API error, stopping (progress kept): {exc}")
            break
        cost += budget.record(bstate, "tag-backfill", args.model, usage)
        answers = parse_reply(text, len(batch))
        for j, (p, i, _) in enumerate(batch, 1):
            got = answers.get(j) or []
            if got:
                sig = snippets.get((p.name, i), "")
                st.fill(p, i, got,
                        "llm+homepage" if sig else "llm-name-only", sig)
        done = min(start + _BATCH, len(remaining))
        print(f"[tag-backfill] LLM {done}/{len(remaining)}, "
              f"{st.filled} tagged total, ${cost:.4f}", flush=True)
        time.sleep(0.3)

    st.flush()
    _REPORT.write_text(json.dumps(
        {"model": args.model,
         "ran_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
         "companies_tagged": st.filled, "cost_usd": round(cost, 4),
         "fills": st.report_fills},
        indent=2, ensure_ascii=False) + "\n")
    if cost:
        budget.save(bstate)
    print(f"\n[tag-backfill] tagged {st.filled} companies in "
          f"{len(st.dirty)} files; LLM cost ${cost:.4f}; "
          f"report -> data/llm_tag_report.json")
    if st.dirty:
        print("next:  git add data/ && git commit -m 'Tag backfill: homepage "
              f"fetch + LLM ({st.filled} companies)' && git push origin main")
    return 0


if __name__ == "__main__":
    sys.exit(main())
