"""Offline tests for the shared everywhere_tags classifier — no network, no cost.

The tags are the product: the dashboard agent builds comps from everywhere_tags,
so factory-generated and generic output must come out tagged, under exactly the
CLAUDE.md taxonomy rules. What needs proving:

  1. output discipline — every emitted tag is one of the 17, verbatim, cap 4;
  2. the AI rule — an ML/AI company classifies by the market it serves, and
     "machine/deep learning" never trips the education "learning" keywords;
  3. fill_empty never overwrites a hand-written scraper's own tagging, and
     tolerates both name-field conventions and str/list sector fields;
  4. the taxonomy in tags.py matches CLAUDE.md's 17 exactly — a typo in a tag
     name would silently zero that Airtable column for every new dataset.

Run:  python3 automation/test_tags.py     (exit 0 = all pass)
"""
from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import tags  # noqa: E402

_FAILS: list[str] = []


def check(label: str, got, want) -> None:
    ok = got == want
    if not ok:
        _FAILS.append(f"{label}: got {got!r}, want {want!r}")
    print(f"  {'ok  ' if ok else 'FAIL'} {label}"
          + ("" if ok else f"   got={got!r} want={want!r}"))


def test_output_discipline() -> None:
    print("\nevery output is verbatim taxonomy, capped at 4")
    tagset = set(tags.TAGS)
    check("taxonomy has exactly 17 tags", len(tags.TAGS), 17)
    samples = [
        ("Acme", "AI-powered payments, lending and insurance for banks and "
                 "healthcare clinics with analytics dashboards and developer "
                 "APIs for supply chain freight"),
        ("Suno", "Create stunning original music for free in seconds using our "
                 "AI generator."),
        ("Nothing Co", "we exist"),
    ]
    for name, desc in samples:
        out = tags.classify(name, desc)
        check(f"{name}: all tags valid", all(t in tagset for t in out), True)
        check(f"{name}: cap 4", len(out) <= 4, True)
        check(f"{name}: no duplicates", len(out), len(set(out)))
    check("no signal -> no tags", tags.classify("Nothing Co", "we exist"), [])


def test_ai_rule() -> None:
    print("\nAI is not a category — classify by the market served")
    check("AI for developers -> Dev Tools",
          "Dev Tools / Cloud" in tags.classify(
              "Codex", "AI coding assistant for developers"), True)
    check("AI for patient care -> Health",
          "Health" in tags.classify(
              "Aura", "AI platform for patient care in clinics"), True)
    check("earned-wage access reads as FinTech (EarnIn regression)",
          "FinTech / Insurance" in tags.classify(
              "EarnIn", "an app that gives you your pay without waiting "
                        "for payday"), True)
    # the neutralization guard: "machine learning platform" must NOT reach the
    # education/work "learning"/"learning platform" keywords
    out = tags.classify("VectorCo", "a machine learning platform")
    check("bare ML platform doesn't become Consumer/edu",
          "Consumer" in out, False)
    check("...nor Future of Work via 'learning platform'",
          "Future of Work" in out, False)


def test_fill_empty() -> None:
    print("\nfill_empty: fills gaps, never overwrites, tolerates schema variance")
    recs = [
        {"company_name": "PayCo", "description": "payments for banks",
         "everywhere_tags": []},
        {"name": "OldSchema", "description": "cybersecurity threat detection"},
        {"company_name": "Tagged", "description": "payments",
         "everywhere_tags": ["CPG"]},                     # hand tag: wrong but HIS
        {"company_name": "SectorOnly", "description": None,
         "sector": "Fintech", "everywhere_tags": []},     # singular str sector
        {"company_name": "SectorList", "description": None,
         "sectors": ["Real Estate"], "everywhere_tags": []},
    ]
    n = tags.fill_empty(recs)
    check("4 records newly tagged", n, 4)
    check("company_name convention",
          "FinTech / Insurance" in recs[0]["everywhere_tags"], True)
    check("legacy name convention",
          "Cybersecurity" in recs[1]["everywhere_tags"], True)
    check("existing tags never overwritten", recs[2]["everywhere_tags"], ["CPG"])
    check("str sector folds into text",
          "FinTech / Insurance" in recs[3]["everywhere_tags"], True)
    check("list sector folds into text",
          "PropTech" in recs[4]["everywhere_tags"], True)
    check("None records tolerated", tags.fill_empty(None), 0)
    # and the Airtable side sees what the dashboard sees
    counts = tags.count_tags(recs)
    check("counts flow from filled tags",
          counts["FinTech / Insurance"] >= 2, True)


def test_carry_forward() -> None:
    print("\ncarry_forward: enrichment survives a refresh")
    old = [
        {"company_name": "Stripe", "company_url": "https://stripe.com/",
         "everywhere_tags": ["FinTech / Insurance"]},          # LLM-enriched
        {"company_name": "Acme Robotics", "company_url": None,
         "everywhere_tags": ["Deeptech / Robotics / AR/VR"]},  # name-only match
        {"company_name": "Untagged Co", "company_url": "https://untag.io",
         "everywhere_tags": []},
    ]
    # the weekly refresh rebuilds records from scratch: no tags, no text
    new = [
        {"company_name": "Stripe, Inc.", "company_url": "http://www.stripe.com",
         "everywhere_tags": []},                    # url matches despite restyle
        {"company_name": "Acme Robotics", "everywhere_tags": []},
        {"company_name": "Untagged Co", "company_url": "https://untag.io",
         "everywhere_tags": []},
        {"company_name": "Brand New", "company_url": "https://new.io",
         "everywhere_tags": ["CPG"]},               # fresh scraper tag: wins
    ]
    n = tags.carry_forward(old, new)
    check("two records inherited", n, 2)
    check("url-matched inheritance",
          new[0]["everywhere_tags"], ["FinTech / Insurance"])
    check("name-fallback inheritance",
          new[1]["everywhere_tags"], ["Deeptech / Robotics / AR/VR"])
    check("old empty tags are not inherited", new[2]["everywhere_tags"], [])
    check("fresh non-empty tags untouched", new[3]["everywhere_tags"], ["CPG"])
    # ...and fill_empty afterwards cannot undo an inheritance
    tags.fill_empty(new)
    check("keyword pass respects inherited tags",
          new[0]["everywhere_tags"], ["FinTech / Insurance"])


def test_page_signal() -> None:
    """The fetch stage's guards: a homepage may only classify a company if it
    actually says something. A squatted domain or a JS shell must yield ""."""
    print("\npage_signal: real text passes, parked domains and shells do not")
    import llm_tag_backfill as ltb
    real = ("<html><head><title>Stripe | Payments</title>"
            '<meta name="description" content="Payments infrastructure for '
            'the internet, used by millions of businesses."></head>'
            "<body><h1>Financial infrastructure</h1></body></html>")
    sig = ltb.page_signal(real)
    check("title captured", "Stripe" in sig, True)
    check("meta description captured", "Payments infrastructure" in sig, True)
    check("classifies via keywords",
          "FinTech / Insurance" in tags.classify("Stripe", sig), True)
    parked = ("<html><head><title>stripe.com</title></head><body>"
              "This premium domain is for sale! Buy this domain today "
              "through our GoDaddy partner network and make it yours forever."
              "</body></html>")
    check("parked domain yields nothing", ltb.page_signal(parked), "")
    shell = "<html><head><title>Home</title></head><body></body></html>"
    check("JS shell yields nothing", ltb.page_signal(shell), "")
    check("no HTML yields nothing", ltb.page_signal(None), "")
    scripty = ("<html><body><script>var x = 'payments fintech banking loans "
               "credit insurance'</script></body></html>")
    check("script text is not signal", ltb.page_signal(scripty), "")


def test_llm_reply_parsing() -> None:
    print("\nllm backfill: replies are validated, never trusted")
    import llm_tag_backfill as ltb
    good = ('Here you go:\n{"1": ["FinTech / Insurance", "Consumer"], '
            '"2": [], "3": ["Fintech", "Health"], "9": ["Health"]}')
    out = ltb.parse_reply(good, 3)
    check("valid tags kept", out[1], ["FinTech / Insurance", "Consumer"])
    check("empty answer kept as empty", out[2], [])
    check("non-verbatim tag dropped, valid sibling kept", out[3], ["Health"])
    check("out-of-range keys ignored", 9 in out, False)
    check("garbage reply -> nothing", ltb.parse_reply("no json here", 3), {})
    five = json.dumps({"1": ["Health", "BioTech", "Consumer", "CPG",
                             "PropTech"]})
    check("cap 4 enforced", len(ltb.parse_reply(five, 1)[1]), 4)


def test_taxonomy_matches_claude_md() -> None:
    print("\ntaxonomy strings match CLAUDE.md verbatim")
    md = (pathlib.Path(__file__).resolve().parent.parent / "CLAUDE.md").read_text()
    for t in tags.TAGS:
        check(f"{t!r} present in CLAUDE.md", t in md, True)
    keyword_tags = {t for t, _ in tags._KEYWORD_TAGS}
    check("keyword map covers the whole taxonomy",
          keyword_tags, set(tags.TAGS))


if __name__ == "__main__":
    test_output_discipline()
    test_ai_rule()
    test_fill_empty()
    test_carry_forward()
    test_page_signal()
    test_llm_reply_parsing()
    test_taxonomy_matches_claude_md()
    print()
    if _FAILS:
        print(f"{len(_FAILS)} FAILURE(S)")
        for f in _FAILS:
            print(" -", f)
        sys.exit(1)
    print("all tests passed")
