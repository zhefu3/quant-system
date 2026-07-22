"""E60 observation book: an LLM committee trades crypto_core's universe.

Architecture distilled from TradingAgents (TauricResearch) to the minimum
that preserves the idea:

    news analyst (Haiku + web search)  ->  bull analyst  \
                                                            ->  trader decision
    market brief (pure code, no LLM)   ->  bear analyst  /     (structured JSON)

The adversarial bull/bear step is deliberate: forcing both cases before a
verdict is a structural correction for one-sided LLM narratives. The final
weights then pass through the SAME RiskGate and paper-fill path as every
other book (see live/paper.py targets_fn injection).

Discipline (E60 prereg):
  - one LLM decision per UTC day, cached in decisions/<date>.json; hourly
    ticks mark-to-market against the cached weights at zero API cost
  - full transcripts archived in decisions/<date>.md — runs are not
    reproducible (that is inherent to LLMs), so the archive IS the record
  - models pinned below; changing them requires a prereg amendment

Credentials: ANTHROPIC_API_KEY in the environment (never via chat/code).
A missing key fails the tick cleanly — positions freeze, other books run.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from ..presets import BookPreset

DEEP_MODEL = "claude-sonnet-5"    # debate + decision (pinned, E60 prereg)
QUICK_MODEL = "claude-haiku-4-5"  # news gathering + reflection (pinned, E60 prereg)
MAX_W = 0.10                      # |weight| per coin; RiskGate enforces again
MEMORY_DAYS = 5                   # recent decisions shown back to the trader
REFLECT_AFTER_DAYS = 7            # decision outcome horizon before reflection

ROOT = Path(__file__).resolve().parents[2] / "outputs" / "paper" / "llm_agents"
DECISIONS = ROOT / "decisions"


# -- pure helpers (unit-tested, no network) -----------------------------------

def market_brief(bars_by_symbol: dict[str, pd.DataFrame]) -> str:
    """Per-coin momentum/vol/drawdown summary from daily bars."""
    lines = []
    for sym, b in sorted(bars_by_symbol.items()):
        c = b["close"]
        r = c.pct_change()
        line = (f"{sym.split('/')[0]:5s} px {c.iloc[-1]:.4g}"
                f" | 1d {c.iloc[-1] / c.iloc[-2] - 1:+.1%}"
                f" | 1w {c.iloc[-1] / c.iloc[-8] - 1:+.1%}"
                f" | 1m {c.iloc[-1] / c.iloc[-22] - 1:+.1%}"
                f" | 3m {c.iloc[-1] / c.iloc[-64] - 1:+.1%}"
                f" | vol30d {r.iloc[-30:].std() * (365 ** 0.5):.0%}"
                f" | vs90dHigh {c.iloc[-1] / c.iloc[-90:].max() - 1:+.1%}")
        lines.append(line)
    return "\n".join(lines)


def parse_decision(payload: dict, symbols: list[str]) -> dict[str, float]:
    """Clamp the model's weights to the book's hard bounds (defense in depth —
    the RiskGate clamps again downstream)."""
    weights = {}
    for sym in symbols:
        w = float(payload.get("weights", {}).get(sym.split("/")[0], 0.0))
        weights[sym] = max(-MAX_W, min(MAX_W, w))
    gross = sum(abs(w) for w in weights.values())
    if gross > 1.0:
        weights = {s: w / gross for s, w in weights.items()}
    return weights


def recent_memory() -> str:
    """Recent decisions plus outcome reflections (TradingAgents Phase B):
    the committee sees not just what it decided, but how it turned out."""
    if not DECISIONS.exists():
        return "(no prior decisions)"
    files = sorted(DECISIONS.glob("*.json"))[-MEMORY_DAYS:]
    lines = []
    for f in files:
        d = json.loads(f.read_text())
        top = sorted(d["weights"].items(), key=lambda kv: -abs(kv[1]))[:3]
        pos = ", ".join(f"{s.split('/')[0]} {w:+.2f}" for s, w in top if w)
        lines.append(f"{f.stem}: {pos or 'flat'} — {d.get('rationale', '')[:120]}")
        if "outcome" in d:
            o = d["outcome"]
            lines.append(f"  outcome {REFLECT_AFTER_DAYS}d: book {o['book_ret']:+.1%} "
                         f"vs BTC {o['btc_ret']:+.1%} | lesson: {d.get('reflection', '')}")
    # matured lessons from older decisions stay in memory permanently
    older = sorted(DECISIONS.glob("*.json"))[:-MEMORY_DAYS]
    lessons = []
    for f in older:
        d = json.loads(f.read_text())
        if d.get("reflection"):
            lessons.append(f"{f.stem}: {d['reflection']}")
    if lessons:
        lines.append("\nEARLIER LESSONS:\n" + "\n".join(lessons[-10:]))
    return "\n".join(lines) or "(no prior decisions)"


def book_outcome(decision_date: str, horizon_days: int = REFLECT_AFTER_DAYS,
                 equity_file: Path | None = None) -> float | None:
    """Realized book return from decision date over the horizon, from the
    paper equity record. None while the horizon hasn't matured."""
    eq_file = equity_file or (ROOT / "equity.csv")
    if not eq_file.exists():
        return None
    eq = pd.read_csv(eq_file)
    ts = pd.to_datetime(eq["ts"], format="mixed", utc=True)
    d0 = pd.Timestamp(decision_date, tz="UTC")
    d1 = d0 + pd.Timedelta(days=horizon_days)
    at0 = eq[ts >= d0]
    at1 = eq[ts >= d1]
    if at0.empty or at1.empty:
        return None
    return float(at1["equity"].iloc[0] / at0["equity"].iloc[0] - 1)


def reflect_matured(client, bars_by_symbol: dict[str, pd.DataFrame]) -> int:
    """Phase B (TradingAgents): once a decision's outcome is known, a quick
    model writes a 2-4 sentence lesson that future committees will re-read.
    Never lets a reflection failure break the tick. Returns lessons written."""
    if not DECISIONS.exists():
        return 0
    btc = bars_by_symbol.get("BTC/USDT")
    written = 0
    for f in sorted(DECISIONS.glob("*.json")):
        d = json.loads(f.read_text())
        if "reflection" in d:
            continue
        ret = book_outcome(d["date"])
        if ret is None:
            continue
        try:
            d0 = pd.Timestamp(d["date"], tz="UTC")
            d1 = d0 + pd.Timedelta(days=REFLECT_AFTER_DAYS)
            c = btc["close"]
            btc_ret = float(c[c.index >= d1].iloc[0] / c[c.index >= d0].iloc[0] - 1) \
                if btc is not None and len(c[c.index >= d1]) else 0.0
            r = client.messages.create(
                model=QUICK_MODEL, max_tokens=300,
                system=("You are a trading analyst reviewing your own past decision "
                        "now that the outcome is known. Write exactly 2-4 sentences "
                        "of plain prose. Cover: was the directional call correct "
                        "(cite the numbers); which part of the thesis held or failed; "
                        "one concrete lesson for the next similar decision. Terse — "
                        "this is re-read verbatim by future committees."),
                messages=[{"role": "user", "content":
                           f"Decision ({d['date']}): {d.get('rationale', '')}\n"
                           f"Top weights: {json.dumps({k: v for k, v in d['weights'].items() if v})}\n"
                           f"Realized {REFLECT_AFTER_DAYS}d book return: {ret:+.2%}\n"
                           f"BTC over same window: {btc_ret:+.2%}"}])
            d["reflection"] = _text(r)
            d["outcome"] = {"book_ret": ret, "btc_ret": btc_ret,
                            "horizon_days": REFLECT_AFTER_DAYS}
            f.write_text(json.dumps(d, indent=2))
            written += 1
        except Exception as e:  # noqa: BLE001 — reflection must never break the tick
            print(f"  reflection skipped for {f.stem}: {str(e)[:80]}")
    return written


# -- LLM chain -----------------------------------------------------------------

_SYSTEM = (
    "You are part of a crypto trading committee running a PAPER (simulated) "
    "portfolio of 10 liquid perpetual swaps. Costs are 0.05% fee + 0.05% "
    "slippage per side, so churn is expensive. Weights are per-coin fractions "
    f"of equity in [-{MAX_W}, +{MAX_W}]; gross exposure <= 1.0. Decisions are "
    "daily; positions persist until changed. Be honest about uncertainty — "
    "a flat book is a valid position."
)

_DECISION_SCHEMA = {
    "type": "object",
    "properties": {
        "rationale": {"type": "string"},
        "weights": {
            "type": "object",
            "properties": {c: {"type": "number"} for c in
                           ["ADA", "AVAX", "BTC", "DOGE", "DOT",
                            "ETH", "LINK", "LTC", "SOL", "XRP"]},
            "required": ["ADA", "AVAX", "BTC", "DOGE", "DOT",
                         "ETH", "LINK", "LTC", "SOL", "XRP"],
            "additionalProperties": False,
        },
    },
    "required": ["rationale", "weights"],
    "additionalProperties": False,
}


def _text(response) -> str:
    return next((b.text for b in response.content if b.type == "text"), "")


def run_committee(client, brief: str, symbols: list[str]) -> tuple[dict[str, float], dict]:
    """News -> bull/bear debate -> trader decision. Returns (weights, archive)."""
    usage = []

    def track(r):
        usage.append({"model": r.model, "in": r.usage.input_tokens,
                      "out": r.usage.output_tokens})
        return r

    news_r = track(client.messages.create(
        model=QUICK_MODEL, max_tokens=1500,
        system=_SYSTEM,
        tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 5}],
        messages=[{"role": "user", "content":
                   "Search for the most important crypto market news of the last "
                   "24-48 hours (macro, regulation, flows, per-asset events for "
                   "BTC/ETH/SOL/XRP/ADA/AVAX/DOGE/DOT/LINK/LTC). Summarize the "
                   "8-10 most decision-relevant items with dates. Facts only, "
                   "no recommendations."}]))
    news = _text(news_r)

    def analyst(side: str):
        stance = ("strongest case for taking/keeping LONG exposure and which coins"
                  if side == "bull" else
                  "strongest case for CAUTION or SHORT exposure and which coins")
        return track(client.messages.create(
            model=DEEP_MODEL, max_tokens=1200, system=_SYSTEM,
            messages=[{"role": "user", "content":
                       f"MARKET DATA:\n{brief}\n\nNEWS:\n{news}\n\n"
                       f"You are the {side.upper()} researcher. Make the {stance}. "
                       "Ground every claim in the data or news above. <=250 words."}]))

    bull_r, bear_r = analyst("bull"), analyst("bear")
    bull, bear = _text(bull_r), _text(bear_r)

    decision_r = track(client.messages.create(
        model=DEEP_MODEL, max_tokens=2000, system=_SYSTEM,
        output_config={"format": {"type": "json_schema", "schema": _DECISION_SCHEMA}},
        messages=[{"role": "user", "content":
                   f"MARKET DATA:\n{brief}\n\nNEWS:\n{news}\n\n"
                   f"BULL CASE:\n{bull}\n\nBEAR CASE:\n{bear}\n\n"
                   f"YOUR RECENT DECISIONS:\n{recent_memory()}\n\n"
                   "You are the trader. Weigh both cases and output today's "
                   "target weights. Only deviate from your previous book when "
                   "the evidence justifies paying transaction costs."}]))
    payload = json.loads(_text(decision_r))
    weights = parse_decision(payload, symbols)

    archive = {"news": news, "bull": bull, "bear": bear,
               "rationale": payload.get("rationale", ""), "usage": usage}
    return weights, archive


# -- daily-cached targets_fn -----------------------------------------------------

def make_targets_fn(preset: BookPreset):
    def targets_fn(bars_by_symbol: dict[str, pd.DataFrame]):
        closes = {s: float(b["close"].iloc[-1]) for s, b in bars_by_symbol.items()}
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        cache = DECISIONS / f"{today}.json"
        if cache.exists():
            weights = json.loads(cache.read_text())["weights"]
            return {s: float(weights.get(s, 0.0)) for s in preset.symbols}, closes

        # Committee unavailability (API billing/outage — 2026-07-22: exhausted
        # credit balance killed every tick for 10h, marks included) must not
        # stop the BOOKKEEPING: fall back to yesterday's cached weights —
        # positions freeze, marking continues, and the missing decision is
        # surfaced by health's decision-freshness check, not by a starved
        # heartbeat. Decisions stay LLM-only; this is accounting continuity,
        # not a decision path (E60 protocol untouched).
        import anthropic

        try:
            client = anthropic.Anthropic()
            reflect_matured(client, bars_by_symbol)  # lessons land before today's meeting
            brief = market_brief(bars_by_symbol)
            weights, archive = run_committee(client, brief, preset.symbols)
        except Exception as e:  # noqa: BLE001 — TickDeadline passes (BaseException)
            print(f"  committee unavailable ({type(e).__name__}: {str(e)[:90]}) "
                  "— holding yesterday's book, mark continues")
            prev = sorted(DECISIONS.glob("*.json"))
            if not prev:
                return {s: 0.0 for s in preset.symbols}, closes
            weights = json.loads(prev[-1].read_text())["weights"]
            return {s: float(weights.get(s, 0.0)) for s in preset.symbols}, closes

        DECISIONS.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(
            {"date": today, "weights": weights, "rationale": archive["rationale"],
             "usage": archive["usage"]}, indent=2))
        (DECISIONS / f"{today}.md").write_text(
            f"# llm_agents decision {today}\n\n## Market brief\n{brief}\n\n"
            f"## News (haiku + web search)\n{archive['news']}\n\n"
            f"## Bull case\n{archive['bull']}\n\n## Bear case\n{archive['bear']}\n\n"
            f"## Decision rationale\n{archive['rationale']}\n\n"
            f"## Weights\n{json.dumps(weights, indent=2)}\n\n"
            f"## Usage\n{json.dumps(archive['usage'], indent=2)}\n")
        return weights, closes

    return targets_fn
