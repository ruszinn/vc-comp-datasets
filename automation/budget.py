"""Monthly spend ceiling for the Claude API calls — data/spend.json.

The pipeline's only variable cost is the Claude API: the scraper factory and,
trivially, the candidate finder (~$0.006 a run). On Sonnet alone a firm cost
~$0.21, so one new firm a night was ~$6.60/month — over the user's $5 budget.
Model escalation (scraper_gen.model_for) brought that to ~$0.10 a firm, so a
firm a day now lands near $3/month; this ledger is what keeps it there.

Tuning knobs alone can't guarantee a ceiling — GEN_MAX_PER_RUN caps firms per
night, not dollars, and a night where three firms each burn a full burst costs
three times a night where one succeeds immediately. So this module does the
accounting properly: every API call reports its real usage, the cost is computed
from the published per-token rates, and generation stops for the rest of the
calendar month once the budget is gone.

    {"month": "2026-07", "spent": 3.91, "calls": 44,
     "by_source": {"factory": 3.79, "finder": 0.12}}

Degrading, not failing: when the budget runs out the FACTORY stops (it is ~97%
of the spend) while the finder and the scrapers carry on, because those are
nearly free and keep the queue warm. Firms that would have been generated stay
queued and get picked up on the 1st, when the month rolls over and the counter
resets on its own — no cron, no manual reset.

Two known imprecisions, both bounded and both erring safe:
  * a container killed mid-burst loses that burst's unrecorded spend (≤ ~$0.26);
  * prices are hardcoded per model here, so a model change needs an entry. An
    unknown model is charged at the most expensive known rate, which cuts
    spending off early rather than overshooting silently.

The Anthropic Console spend limit is still worth setting as the real wall: this
is a cooperative guard inside the pipeline, not an enforced one.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

FILE = "spend.json"
_DATA = Path(__file__).resolve().parent.parent / "data"

# $ per token: (base input, 5-min cache write, cache read, output).
# Source: platform.claude.com/docs/en/about-claude/pricing (checked 2026-07-27).
_PRICES: dict[str, tuple[float, float, float, float]] = {
    "claude-sonnet-4-5": (3e-6, 3.75e-6, 0.30e-6, 15e-6),
    "claude-sonnet-4-6": (3e-6, 3.75e-6, 0.30e-6, 15e-6),
    "claude-sonnet-5":   (2e-6, 2.50e-6, 0.20e-6, 10e-6),   # intro, to 2026-08-31
    "claude-haiku-4-5":  (1e-6, 1.25e-6, 0.10e-6,  5e-6),
    "claude-opus-4-5":   (5e-6, 6.25e-6, 0.50e-6, 25e-6),
    "claude-opus-5":     (5e-6, 6.25e-6, 0.50e-6, 25e-6),
}
_FALLBACK = max(_PRICES.values())        # unknown model -> assume the dearest

# What one full factory burst is assumed to cost when deciding whether to start
# it. With model escalation a worst-case 3-try burst is ~$0.24 (two Haiku tries
# then a Sonnet one — note the Sonnet try pays a fresh cache WRITE, because the
# prompt cache is per-model and Haiku's copy is not readable by Sonnet). Kept a
# little generous so the last firm of the month can't tip the total over.
_BURST_ESTIMATE = 0.25


def burst_estimate() -> float:
    return float(os.environ.get("GEN_COST_ESTIMATE", _BURST_ESTIMATE))


# Held back from the factory so the finder can keep running to month end. The
# finder costs ~$0.007 a night and runs even after generation pauses, so without
# a reserve the factory spends right up to the line and the finder's remaining
# nights push the month over it (observed: $4.53 against a $4.50 budget).
_FINDER_RESERVE = 0.25


def budget() -> float:
    return float(os.environ.get("MONTHLY_BUDGET_USD", "4.50"))


def month_key(now: datetime | None = None) -> str:
    return (now or datetime.now(timezone.utc)).strftime("%Y-%m")


def price_of(model: str, usage: dict) -> float:
    """Dollar cost of one API response, from its own `usage` block.

    Cache tokens are billed differently from base input — writes at 1.25x, reads
    at 0.1x — and the factory's bursts are mostly cache reads, so folding them
    into the base rate would overstate spend by roughly 3x and cut generation
    off long before the money was actually gone."""
    base, write, read, out = _PRICES.get(model, _FALLBACK)
    return (int(usage.get("input_tokens") or 0) * base
            + int(usage.get("cache_creation_input_tokens") or 0) * write
            + int(usage.get("cache_read_input_tokens") or 0) * read
            + int(usage.get("output_tokens") or 0) * out)


def load(store=None, now: datetime | None = None) -> dict:
    """Current month's ledger. A different month (or missing/garbled state)
    starts fresh at zero — the rollover needs no scheduled job."""
    raw = None
    if store is not None:
        try:
            raw = store.read_json(FILE)
        except Exception:  # noqa: BLE001
            raw = None
    if raw is None:
        try:
            raw = json.loads((_DATA / FILE).read_text())
        except Exception:  # noqa: BLE001
            raw = []
    key = month_key(now)
    for e in raw if isinstance(raw, list) else []:
        if isinstance(e, dict) and e.get("month") == key:
            e.setdefault("spent", 0.0)
            e.setdefault("calls", 0)
            e.setdefault("by_source", {})
            return e
    return {"month": key, "spent": 0.0, "calls": 0, "by_source": {}}


def record(state: dict, source: str, model: str, usage: dict) -> float:
    """Add one call's cost to the ledger. Returns that call's cost."""
    cost = price_of(model, usage)
    state["spent"] = round(float(state.get("spent") or 0.0) + cost, 6)
    state["calls"] = int(state.get("calls") or 0) + 1
    by = state.setdefault("by_source", {})
    by[source] = round(float(by.get(source) or 0.0) + cost, 6)
    return cost


# The run's live ledger. Module-level on purpose: the alternative is threading a
# ledger object through pipeline -> scraper_factory.attempt -> scraper_gen.generate,
# changing three signatures so a counter can be incremented at the bottom. The
# pipeline calls activate() once; anything that spends money calls bill().
_ACTIVE: dict | None = None


def activate(state: dict) -> None:
    global _ACTIVE
    _ACTIVE = state


def bill(source: str, model: str, usage: dict) -> float | None:
    """Charge a call to the active ledger. None when no ledger is active (local
    one-off runs and tests) — spending must never depend on the guard existing."""
    if _ACTIVE is None:
        return None
    return record(_ACTIVE, source, model, usage)


def remaining(state: dict) -> float:
    return budget() - float(state.get("spent") or 0.0)


def can_generate(state: dict, need: float | None = None) -> tuple[bool, str]:
    """(True, "") if a factory burst may start, else (False, why).

    Requires the burst's cost PLUS the finder's reserve, so pausing the factory
    leaves the cheap stages funded for the rest of the month."""
    need = (burst_estimate() if need is None else need) + _FINDER_RESERVE
    left = remaining(state)
    if left >= need:
        return True, ""
    return False, (f"${state.get('spent', 0):.2f} of ${budget():.2f} spent this "
                   f"month; ${left:.2f} left, need ~${need:.2f} — generation "
                   f"paused until {month_key()}-01 rolls over")


def can_find(state: dict) -> tuple[bool, str]:
    """(True, "") if the candidate finder may make its one call. Only false if
    the budget is genuinely gone — this is the last thing to be switched off."""
    left = remaining(state)
    if left > 0.02:
        return True, ""
    return False, (f"budget exhausted (${state.get('spent', 0):.2f} of "
                   f"${budget():.2f}) — finder paused too")


def save(state: dict, store=None) -> None:
    """Keep the current month plus the previous 11, so the file doubles as a
    spend history without growing forever.

    Prior months are read back from the REPO when a store is available, for the
    same reason load() prefers it: a Railway container's local copy is whatever
    the last deploy shipped, so building history from disk would silently drop
    every month committed since. Only the current month comes from `state`."""
    existing: list = []
    if store is not None:
        try:
            existing = store.read_json(FILE) or []
        except Exception:  # noqa: BLE001
            existing = []
    if not existing:
        try:
            existing = json.loads((_DATA / FILE).read_text())
        except Exception:  # noqa: BLE001
            existing = []
    existing = [e for e in existing
                if isinstance(e, dict) and e.get("month") != state["month"]]
    payload = sorted(existing + [state],
                     key=lambda e: e.get("month", ""))[-12:]
    try:
        (_DATA / FILE).write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    except OSError:
        pass
    if store is not None:
        store.commit_json(
            FILE, payload,
            f"Spend: {state['month']} ${state['spent']:.2f} "
            f"of ${budget():.2f} ({state['calls']} calls)")
