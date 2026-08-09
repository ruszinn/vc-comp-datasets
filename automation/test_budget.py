"""Offline tests for the monthly spend ceiling — no network, no API key, no cost.

The claim being tested is a money claim, so it is checked two ways:

  1. the per-call arithmetic matches the published rates, worked by hand
     (a cache-read token billed as a base input token would overstate a factory
      burst ~3x and pause generation weeks early);
  2. a simulated month of nightly runs never exceeds the budget — the actual
     promise. Unit-testing can_generate() alone would pass even if nothing ever
     called it.

Run:  python3 automation/test_budget.py     (exit 0 = all pass)
"""
from __future__ import annotations

import os
import pathlib
import sys
from datetime import datetime, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import budget as b  # noqa: E402

_FAILS: list[str] = []
SONNET = "claude-sonnet-4-5"


def check(label: str, got, want) -> None:
    ok = got == want
    if not ok:
        _FAILS.append(f"{label}: got {got!r}, want {want!r}")
    print(f"  {'ok  ' if ok else 'FAIL'} {label}"
          + ("" if ok else f"   got={got!r} want={want!r}"))


def close(label: str, got: float, want: float, tol: float = 1e-9) -> None:
    ok = abs(got - want) <= tol
    if not ok:
        _FAILS.append(f"{label}: got {got!r}, want ~{want!r}")
    print(f"  {'ok  ' if ok else 'FAIL'} {label}"
          + ("" if ok else f"   got={got!r} want~{want!r}"))


def test_pricing() -> None:
    print("\nper-call pricing (Sonnet 4.5 — $3/$15 per MTok)")
    # the finder's real measured call: 605 in, 286 out
    close("finder call", b.price_of(SONNET, {"input_tokens": 605,
                                             "output_tokens": 286}),
          605 * 3e-6 + 286 * 15e-6)

    # cache economics: a write is 1.25x base, a read 0.1x
    close("cache write billed at 1.25x",
          b.price_of(SONNET, {"cache_creation_input_tokens": 1_000_000}), 3.75)
    close("cache read billed at 0.1x",
          b.price_of(SONNET, {"cache_read_input_tokens": 1_000_000}), 0.30)
    naive = b.price_of(SONNET, {"input_tokens": 1_000_000})
    check("reads really are 10x cheaper than base input",
          round(naive / b.price_of(SONNET, {"cache_read_input_tokens": 1_000_000})),
          10)

    close("haiku is a third of sonnet",
          b.price_of("claude-haiku-4-5", {"input_tokens": 1_000_000}), 1.0)
    close("unknown model charged at the dearest known rate",
          b.price_of("claude-something-new", {"input_tokens": 1_000_000}), 5.0)
    check("empty usage costs nothing", b.price_of(SONNET, {}), 0.0)


def test_ledger() -> None:
    print("\nledger")
    st = {"month": "2026-07", "spent": 0.0, "calls": 0, "by_source": {}}
    b.record(st, "finder", SONNET, {"input_tokens": 605, "output_tokens": 286})
    b.record(st, "factory", SONNET, {"input_tokens": 30_000, "output_tokens": 4_000})
    check("call count", st["calls"], 2)
    check("split by source", sorted(st["by_source"]), ["factory", "finder"])
    check("factory dominates", st["by_source"]["factory"] > st["by_source"]["finder"],
          True)
    close("total is the sum of its parts", st["spent"],
          round(sum(st["by_source"].values()), 6), 1e-6)


def test_cutoff() -> None:
    print("\ncutoff")
    os.environ["MONTHLY_BUDGET_USD"] = "4.50"
    try:
        st = {"month": "2026-07", "spent": 0.0, "calls": 0, "by_source": {}}
        check("generation allowed when empty", b.can_generate(st)[0], True)
        st["spent"] = 4.00
        check("allowed with $0.50 left (a burst needs ~$0.30)",
              b.can_generate(st)[0], True)
        st["spent"] = 4.30
        check("blocked when a burst wouldn't fit", b.can_generate(st)[0], False)
        check("reason states the shortfall",
              "generation paused" in b.can_generate(st)[1], True)
        check("remaining is budget minus spend", round(b.remaining(st), 2), 0.20)
    finally:
        os.environ.pop("MONTHLY_BUDGET_USD", None)


def test_month_rollover(tmp: pathlib.Path) -> None:
    print("\nmonth rollover")
    b._DATA = tmp
    (tmp / b.FILE).write_text('[{"month": "2026-07", "spent": 4.49, '
                              '"calls": 30, "by_source": {"factory": 4.4}}]')
    july = b.load(now=datetime(2026, 7, 31, 23, 0, tzinfo=timezone.utc))
    check("July ledger is read back", round(july["spent"], 2), 4.49)
    check("July is exhausted", b.can_generate(july)[0], False)

    august = b.load(now=datetime(2026, 8, 1, 7, 0, tzinfo=timezone.utc))
    check("August starts at zero", august["spent"], 0.0)
    check("August can generate again", b.can_generate(august)[0], True)
    check("no cron needed for the reset", august["month"], "2026-08")

    b.save(august, None)
    kept = [e["month"] for e in
            __import__("json").loads((tmp / b.FILE).read_text())]
    check("history keeps both months", kept, ["2026-07", "2026-08"])


HAIKU = "claude-haiku-4-5"


def test_escalation() -> None:
    print("\nmodel escalation")
    import gen_state
    import scraper_gen as g
    check("default budget is 4 tries", gen_state.max_attempts(), 4)
    check("4-try burst: cheap, cheap, STRONG, STRONG",
          [g.model_for(i, 4) for i in range(4)], [HAIKU, HAIKU, SONNET, SONNET])
    check("the strong model gets two tries, not one",
          [g.model_for(i, 4) for i in range(4)].count(SONNET), 2)
    check("3-try burst still gives it two",
          [g.model_for(i, 3) for i in range(3)], [HAIKU, SONNET, SONNET])
    check("1-try burst goes straight to the strong model",
          g.model_for(0, 1), SONNET)
    os.environ["GEN_MODEL_CHEAP"] = SONNET
    try:
        check("escalation disables when both models are equal",
              [g.model_for(i, 4) for i in range(4)], [SONNET] * 4)
    finally:
        os.environ.pop("GEN_MODEL_CHEAP", None)


def test_feedback_labels_the_model() -> None:
    print("\nfeedback block names who wrote the code")
    import scraper_gen as g
    fails = [{"reason": "static guard: forbidden call: open()", "code": None,
              "model": HAIKU},
             {"reason": "validation: description coverage < 30%",
              "code": "def scrape(): return []", "model": HAIKU}]
    block = g._feedback_block(fails, model=SONNET)
    check("each attempt is tagged with its model",
          block.count(f"[{HAIKU}]"), 2)
    check("the shown code is attributed", f"(from {HAIKU})" in block, True)
    check("and flagged as a smaller model's work",
          "a different and smaller model than you" in block, True)
    check("told to discard rather than patch", "discard it" in block, True)

    same = g._feedback_block(fails, model=HAIKU)
    check("no such warning when the model hasn't changed",
          "smaller model" in same, False)
    check("but attempts are still labelled", f"[{HAIKU}]" in same, True)
    check("no failures means no block", g._feedback_block([], SONNET), "")


def test_billing_is_actually_wired() -> None:
    """The ledger only binds if the API call sites reach it. Every other test
    here calls record()/bill() directly, so all of them would still pass if
    scraper_gen stopped billing or the pipeline stopped calling activate() —
    and spending would run unrecorded past the cap, silently. This drives a real
    generate() call with a stubbed HTTP layer and checks the money moved."""
    print("\nbilling reaches the ledger from the call site")
    import scraper_gen as g

    class _Resp:
        status_code = 200
        def raise_for_status(self): pass
        def json(self):
            return {"content": [{"type": "text",
                                 "text": "```python\ndef scrape():\n    return []\n```"}],
                    "usage": {"input_tokens": 500,
                              "cache_creation_input_tokens": 22_000,
                              "output_tokens": 5_000}}

    st = {"month": "2026-09", "spent": 0.0, "calls": 0, "by_source": {}}
    b.activate(st)
    real_post, real_key = g.requests.post, os.environ.get("ANTHROPIC_API_KEY")
    os.environ["ANTHROPIC_API_KEY"] = "sk-ant-test"
    try:
        g.requests.post = lambda *a, **k: _Resp()
        code = g.generate("Test Firm", "testfirm", "https://x.test",
                          context="CONTEXT", model=HAIKU)
        check("generate returned the code", code, "def scrape():\n    return []")
        check("the call was billed", st["calls"], 1)
        check("charged to the factory", sorted(st["by_source"]), ["factory"])
        close("at Haiku's rates, not Sonnet's", st["spent"],
              b.price_of(HAIKU, {"input_tokens": 500,
                                 "cache_creation_input_tokens": 22_000,
                                 "output_tokens": 5_000}), 1e-9)
        check("and it is under a cent, not a dollar", st["spent"] < 0.10, True)

        # no active ledger -> spending still works, it just isn't recorded
        b._ACTIVE = None
        g.generate("Test Firm", "testfirm", "https://x.test",
                   context="CONTEXT", model=HAIKU)
        check("no ledger active is not an error", st["calls"], 1)
    finally:
        g.requests.post = real_post
        b._ACTIVE = None
        if real_key is None:
            os.environ.pop("ANTHROPIC_API_KEY", None)
        else:
            os.environ["ANTHROPIC_API_KEY"] = real_key


# --------------------------------------------------------------- the real test
def _try_cost(index: int, tries: int) -> tuple[str, dict]:
    """(model, usage) for one generation try, with the burst's cache behaviour.

    The prompt cache is per-model, so the escalated Sonnet try cannot read the
    cache Haiku wrote — it pays a fresh cache WRITE. Modelling it as a cheap
    read would understate an escalated burst by about a third."""
    import scraper_gen as g
    model = g.model_for(index, tries)
    first_on_this_model = index == 0 or model != g.model_for(index - 1, tries)
    if first_on_this_model:
        return model, {"input_tokens": 500, "cache_creation_input_tokens": 22_000,
                       "output_tokens": 5_000}
    return model, {"input_tokens": 3_000, "cache_read_input_tokens": 22_000,
                   "output_tokens": 4_500}


def _run_month(tmp: pathlib.Path, tries_per_firm, tries: int = 4) -> tuple[float, int]:
    """30 nightly runs, one factory target each. `tries_per_firm` is a callable
    returning how many generation tries that night's firm consumes."""
    b._DATA = tmp
    st = b.load(now=datetime(2026, 9, 1, 7, 0, tzinfo=timezone.utc))
    b.activate(st)
    generated = 0
    try:
        for night in range(30):
            if b.can_find(st)[0]:
                b.bill("finder", SONNET, {"input_tokens": 900,
                                          "output_tokens": 300})
            if not b.can_generate(st)[0]:
                continue
            generated += 1
            for i in range(tries_per_firm(night)):
                model, usage = _try_cost(i, tries)
                b.bill("factory", model, usage)
    finally:
        b._ACTIVE = None
    return st["spent"], generated


def test_month_stays_under_budget(tmp: pathlib.Path) -> None:
    print("\nsimulated month: 30 nightly runs, 1 factory target each")
    os.environ["MONTHLY_BUDGET_USD"] = "4.50"
    try:
        # observed mix from production (CLAUDE.md): amplify/felicis/homebrew
        # passed on try 1, foundrygroup on try 2, signalfire failed all 3
        mix = [1, 1, 2, 1, 3]
        real, real_n = _run_month(tmp, lambda n: mix[n % len(mix)])
        worst, worst_n = _run_month(tmp, lambda _n: 4)

        print(f"    observed mix (1,1,2,1,3 tries): ${real:.2f}, "
              f"{real_n}/30 firms attempted")
        print(f"    worst case (all fail 4 tries) : ${worst:.2f}, "
              f"{worst_n}/30 firms attempted")

        check("a firm every day fits the budget", (real_n, real <= 4.50),
              (30, True))
        check("and leaves room to spare", real < 3.50, True)
        check("worst case still never exceeds the budget", worst <= 4.50, True)
        check("worst case throttles instead of overspending", worst_n < 30, True)
        print(f"    -> a firm a day costs ~${real:.2f}/month; "
              f"${4.50 - real:.2f} of the budget unused")
    finally:
        os.environ.pop("MONTHLY_BUDGET_USD", None)


if __name__ == "__main__":
    import tempfile
    test_pricing()
    test_ledger()
    test_cutoff()
    test_escalation()
    test_feedback_labels_the_model()
    test_billing_is_actually_wired()
    with tempfile.TemporaryDirectory() as d:
        test_month_rollover(pathlib.Path(d))
    with tempfile.TemporaryDirectory() as d:
        test_month_stays_under_budget(pathlib.Path(d))
    print(f"\n{'FAILED: ' + str(len(_FAILS)) if _FAILS else 'all tests passed'}")
    for f in _FAILS:
        print("  -", f)
    raise SystemExit(1 if _FAILS else 0)
