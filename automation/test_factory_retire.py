"""Offline tests for terminal factory retirement — no network, no key, no cost.

The rule under test (user decision 2026-07-27): if no portfolio page resolves
in the factory's step 1, the firm is retired the SAME night. Before this, a
no-url firm burned its whole 4-attempt budget one counted no-op per night
(~2 weeks of "needs-scraper" in Airtable) because the failure shared a counter
designed for generation failures, where retries-with-feedback actually help.

What needs proving, last one most:

  1. attempt() marks a no-url result terminal without touching the network;
  2. one terminal failure retires the firm at once — honestly (attempts stays
     1, "retired": true; NOT attempts jammed to the max);
  3. a transport fluke can never retire a firm, terminal or not;
  4. bespoke-or-nothing (user decision 2026-07-28, superseding the earlier
     keep-the-thin-dataset fallback): a factory-failed firm vanishes
     COMPLETELY — registry row dropped, Airtable delete queued, and any thin
     dataset it had queued for repo deletion (git history is the undo);
  5. it actually stops the nightly burn: 30 simulated nights must record
     exactly ONE attempt. A flag that looked right but left the firm in
     targets() would pass every unit test above and fix nothing.

Run:  python3 automation/test_factory_retire.py     (exit 0 = all pass)
"""
from __future__ import annotations

import pathlib
import sys
from types import SimpleNamespace

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import airtable_writer  # noqa: E402
import gen_state  # noqa: E402
import scraper_factory  # noqa: E402

_FAILS: list[str] = []


def check(label: str, got, want) -> None:
    ok = got == want
    if not ok:
        _FAILS.append(f"{label}: got {got!r}, want {want!r}")
    print(f"  {'ok  ' if ok else 'FAIL'} {label}"
          + ("" if ok else f"   got={got!r} want={want!r}"))


def _firm(slug: str = "nourlfirm") -> SimpleNamespace:
    # homepage=None means attempt() cannot even try to resolve — it must
    # return terminal before any network or API call.
    return SimpleNamespace(slug=slug, data_file=f"{slug}_companies.json",
                           firm_name="No URL Firm", kind="generic",
                           homepage=None, portfolio_url=None)


def test_attempt_is_terminal_offline() -> None:
    print("\nattempt(): no portfolio url is terminal, decided offline")
    r = scraper_factory.attempt(_firm(), None, tries=4)
    check("reason", r["reason"], "no portfolio url")
    check("terminal flag", r["terminal"], True)
    check("exactly one failure (burst never started)", len(r["failures"]), 1)
    check("no success", r["ok"], False)


def test_terminal_retires_at_once_and_honestly() -> None:
    print("\ngen_state: one terminal failure = retired, with honest bookkeeping")
    st: dict = {}
    gen_state.record_failure(st, "nourlfirm", "no portfolio url", terminal=True)
    ok, why = gen_state.eligible(st, "nourlfirm")
    check("ineligible immediately", ok, False)
    check("reason names the cause", "no portfolio url" in why, True)
    check("attempts stays 1, not jammed to max", st["nourlfirm"]["attempts"], 1)
    check("retired flag set", st["nourlfirm"].get("retired"), True)
    # escape hatch unchanged: clearing the entry re-arms the firm
    gen_state.clear(st, "nourlfirm")
    check("clearing the entry re-arms", gen_state.eligible(st, "nourlfirm")[0],
          True)


def test_transport_fluke_never_retires() -> None:
    print("\ngen_state: transport errors stay uncounted and cannot retire")
    st: dict = {}
    gen_state.record_failure(st, "flaky", "generation error: timeout",
                             terminal=True)   # belt-and-braces: even if flagged
    check("uncounted", st["flaky"]["attempts"], 0)
    check("not retired", st["flaky"].get("retired", False), False)
    check("still eligible", gen_state.eligible(st, "flaky")[0], True)


def test_retiree_vanishes_completely() -> None:
    print("\npipeline: bespoke-or-nothing — a factory-failed firm leaves no trace")
    import tempfile

    import pipeline

    # dataset-less candidate: row dropped, Airtable delete queued
    firm = _firm()
    registry = [{"data_file": firm.data_file, "status": "needs-scraper"},
                {"data_file": "other_companies.json", "status": "active"}]
    at_del: list = []
    repo_del: list = []
    registry = pipeline._retire_fully(firm, registry, at_del, repo_del)
    check("its in-run row is dropped",
          [h["data_file"] for h in registry], ["other_companies.json"])
    check("its Airtable row is queued for deletion", at_del, [firm.data_file])

    # dataset-bearing generic firm (the wingvc class): the dataset goes too
    old_data = pipeline._DATA
    try:
        with tempfile.TemporaryDirectory() as tmp:
            pipeline._DATA = pathlib.Path(tmp)
            thin = _firm("thinfirm")
            (pipeline._DATA / thin.data_file).write_text("[]")
            at_del, repo_del = [], []
            registry = pipeline._retire_fully(
                thin, [{"data_file": thin.data_file}], at_del, repo_del)
            check("registry row dropped", registry, [])
            check("Airtable row queued", at_del, [thin.data_file])
            check("repo dataset queued for deletion",
                  thin.data_file in repo_del, True)
            check("local dataset removed",
                  (pipeline._DATA / thin.data_file).exists(), False)
    finally:
        pipeline._DATA = old_data

    # the writer's guards: nothing to delete costs nothing, dry-run is inert
    check("empty delete list is a no-op", airtable_writer.delete_firms([]), 0)
    check("dry-run deletes nothing",
          airtable_writer.delete_firms(["x_companies.json"], dry_run=True), 0)

    # the reconcile pass refuses a broken keep-set: a glob bug upstream must
    # never be amplified into emptying the whole table
    check("empty keep-set refuses to mass-delete",
          airtable_writer.delete_strays(set()), 0)
    check("tiny keep-set refuses too",
          airtable_writer.delete_strays({"a_companies.json"}), 0)
    big_keep = {f"firm{i}_companies.json" for i in range(52)}
    check("sane keep-set proceeds (dry-run)",
          airtable_writer.delete_strays(big_keep, dry_run=True), 0)


def test_notes_link_is_raw_json() -> None:
    """Regression (matrixpartners, 2026-07-28): auto-filled Notes carried a
    github.com BLOB link — the pre-public-repo form — which serves HTML, so the
    dashboard agent couldn't read the dataset. Notes must be the raw-JSON URL,
    and the meta-fill must upgrade old blob values without ever touching a
    hand-written note."""
    print("\nnotes: raw-JSON link, blob form upgraded, hand notes untouched")
    import names
    url = names.notes_url("matrixpartners_companies.json")
    check("raw host", url.startswith("https://raw.githubusercontent.com/"), True)
    check("points at the dataset",
          url.endswith("/data/matrixpartners_companies.json"), True)
    blob = ("https://github.com/michaeleverywhere/vc-comp/blob/main/"
            "data/matrixpartners_companies.json")
    check("old blob form is flagged stale", airtable_writer._stale_note(blob),
          True)
    check("the raw form is not", airtable_writer._stale_note(url), False)
    check("a hand-written note is not",
          airtable_writer._stale_note("re-check selectors next run"), False)


def test_thirty_nights_record_one_attempt() -> None:
    print("\nsimulation: 30 nights, exactly one attempt ever recorded")
    st: dict = {}
    firm = _firm()
    recorded = 0
    for _night in range(30):
        targets = scraper_factory.targets([firm], st)
        if firm.slug not in {f.slug for f in targets}:
            continue                       # skipped before any work — the point
        r = scraper_factory.attempt(firm, None, tries=4)
        for fail in r["failures"]:
            gen_state.record_failure(st, firm.slug, fail,
                                     terminal=r.get("terminal", False))
            recorded += 1
    check("attempts recorded across 30 nights", recorded, 1)
    check("state agrees", st[firm.slug]["attempts"], 1)
    check("retired", st[firm.slug].get("retired"), True)


if __name__ == "__main__":
    test_attempt_is_terminal_offline()
    test_terminal_retires_at_once_and_honestly()
    test_transport_fluke_never_retires()
    test_retiree_vanishes_completely()
    test_notes_link_is_raw_json()
    test_thirty_nights_record_one_attempt()
    print()
    if _FAILS:
        print(f"{len(_FAILS)} FAILURE(S)")
        for f in _FAILS:
            print(" -", f)
        sys.exit(1)
    print("all tests passed")
