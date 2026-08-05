"""E70 observation book: an LLM committee trades the top-50 US large caps.

Architecture is llm_agents (E60) verbatim — news analyst (Haiku + web search)
-> bull/bear debate -> trader decision (structured JSON) — transplanted to US
single stocks. The cross-market repeat IS the experiment: E60's crypto arm
trails its mechanical A/B; the prereg's honest prior is that this arm loses
too, and the book exists to show that falsifiably.

E70 prereg deltas from E60 (research/log.md 2026-08-04):
  - universe: 50 largest US-listed stocks, frozen before the first tick
    (research/artifacts/llm_us_universe_20260804.json; quarterly re-eval
    with 50/60 hysteresis per research/freeze_llm_us_universe.py)
  - LONG-ONLY, |w| <= 0.05 per name, gross <= 1.0, dd_halt 0.15
  - one committee decision per US TRADING DAY: the cache key is the last
    completed daily session in the fetched bars, so weekends/holidays reuse
    the cached decision at zero API cost
  - costs: US_ETF rules (0.01% fee + 0.03% slippage per side)
  - A/B counterfactuals at evaluation (2027-02-04): SPY buy-and-hold and
    etf_trend over the identical window — computed from records, not run
    as extra books
  - reflection benchmark: SPY (llm_agents uses BTC)

Discipline: observation-only (never in the portfolio layer), API spend counts
against the SAME frozen $30/month cap as llm_agents (weekly digest enforces),
decisions + transcripts archived in decisions/ and surfaced on the dashboard.
Credentials: ANTHROPIC_API_KEY in the environment; a missing key freezes
positions while marking continues (llm_agents fallback semantics, verbatim).
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

MAX_W = 0.05                      # per-name cap, long-only (E70 prereg)
MEMORY_DAYS = 5                   # recent decisions shown back to the trader
REFLECT_AFTER_DAYS = 7            # decision outcome horizon before reflection

DEEP_MODEL = "claude-sonnet-5"    # debate + decision (pinned, E70 prereg)
QUICK_MODEL = "claude-haiku-4-5"  # news gathering + reflection (pinned, E70 prereg)

ROOT = Path(__file__).resolve().parents[2] / "outputs" / "paper" / "llm_us"
DECISIONS = ROOT / "decisions"


# -- pure helpers (unit-tested, no network) -----------------------------------

def session_date(bars_by_symbol: dict[str, pd.DataFrame]) -> str:
    """Date of the last COMPLETED US session across the universe — the daily
    decision's cache key. Weekend/holiday ticks resolve to the same key, so
    the committee convenes exactly once per trading day."""
    last = max(b.index[-1] for b in bars_by_symbol.values())
    return pd.Timestamp(last).strftime("%Y-%m-%d")


def market_brief(bars_by_symbol: dict[str, pd.DataFrame]) -> str:
    """Per-stock momentum/vol/drawdown summary from daily bars (annualization
    uses 252 trading days — the one line that differs from the crypto brief)."""
    lines = []
    for sym, b in sorted(bars_by_symbol.items()):
        c = b["close"]
        r = c.pct_change()
        line = (f"{sym:6s} px {c.iloc[-1]:.4g}"
                f" | 1d {c.iloc[-1] / c.iloc[-2] - 1:+.1%}"
                f" | 1w {c.iloc[-1] / c.iloc[-6] - 1:+.1%}"
                f" | 1m {c.iloc[-1] / c.iloc[-22] - 1:+.1%}"
                f" | 3m {c.iloc[-1] / c.iloc[-64] - 1:+.1%}"
                f" | vol30d {r.iloc[-30:].std() * (252 ** 0.5):.0%}"
                f" | vs90dHigh {c.iloc[-1] / c.iloc[-90:].max() - 1:+.1%}")
        lines.append(line)
    return "\n".join(lines)


def parse_decision(payload: dict, symbols: list[str]) -> dict[str, float]:
    """LONG-ONLY clamp to the book's hard bounds (defense in depth — the
    RiskGate clamps again downstream; allow_short=False in US_ETF rules)."""
    weights = {}
    for sym in symbols:
        w = float(payload.get("weights", {}).get(sym, 0.0))
        weights[sym] = max(0.0, min(MAX_W, w))
    gross = sum(weights.values())
    if gross > 1.0:
        weights = {s: w / gross for s, w in weights.items()}
    return weights


def recent_memory() -> str:
    """Recent decisions plus outcome reflections — the committee sees not
    just what it decided, but how it turned out."""
    if not DECISIONS.exists():
        return "(no prior decisions)"
    files = sorted(DECISIONS.glob("*.json"))[-MEMORY_DAYS:]
    lines = []
    for f in files:
        d = json.loads(f.read_text())
        top = sorted(d["weights"].items(), key=lambda kv: -abs(kv[1]))[:5]
        pos = ", ".join(f"{s} {w:+.2f}" for s, w in top if w)
        lines.append(f"{f.stem}: {pos or 'flat'} — {d.get('rationale', '')[:120]}")
        if "outcome" in d:
            o = d["outcome"]
            lines.append(f"  outcome {REFLECT_AFTER_DAYS}d: book {o['book_ret']:+.1%} "
                         f"vs SPY {o['spy_ret']:+.1%} | lesson: {d.get('reflection', '')}")
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


def _spy_return(d0: pd.Timestamp, d1: pd.Timestamp) -> float:
    """SPY return over the reflection window (benchmark for lessons). Any
    failure returns 0.0 — reflection must never break the tick."""
    try:
        from ..data.adapters import make_adapter

        bars = make_adapter("us_etf").fetch_ohlcv(
            "SPY", "1d", d0 - pd.Timedelta(days=5), d1 + pd.Timedelta(days=5))
        c = bars["close"]
        a, b = c[c.index >= d0], c[c.index >= d1]
        if a.empty or b.empty:
            return 0.0
        return float(b.iloc[0] / a.iloc[0] - 1)
    except Exception:  # noqa: BLE001
        return 0.0


def reflect_matured(client) -> int:
    """Once a decision's outcome is known, a quick model writes a 2-4 sentence
    lesson that future committees re-read. Never breaks the tick."""
    if not DECISIONS.exists():
        return 0
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
            spy_ret = _spy_return(d0, d0 + pd.Timedelta(days=REFLECT_AFTER_DAYS))
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
                           f"SPY over same window: {spy_ret:+.2%}"}])
            d["reflection"] = _text(r)
            d["outcome"] = {"book_ret": ret, "spy_ret": spy_ret,
                            "horizon_days": REFLECT_AFTER_DAYS}
            f.write_text(json.dumps(d, indent=2))
            written += 1
        except Exception as e:  # noqa: BLE001 — reflection must never break the tick
            print(f"  reflection skipped for {f.stem}: {str(e)[:80]}")
    return written


# -- LLM chain -----------------------------------------------------------------

_SYSTEM = (
    "You are part of a US equity trading committee running a PAPER (simulated) "
    "LONG-ONLY portfolio of the 50 largest US-listed stocks. Costs are 0.01% "
    "fee + 0.03% slippage per side, so churn is expensive. Weights are "
    f"per-stock fractions of equity in [0, +{MAX_W}]; gross exposure <= 1.0; "
    "short positions are not available. Decisions are made once per US trading "
    "day; positions persist until changed. Be honest about uncertainty — a "
    "flat (all-cash) book is a valid position."
)

# The A/B's experimental subject is (models + prompt). Models are pinned
# above; this pins the prompt the same way (E70 prereg). Any edit to _SYSTEM
# changes the hash, and the mismatch refuses NEW committee decisions
# (yesterday's book holds, marking continues) until a preregistration
# amendment re-pins it — silent prompt drift cannot masquerade as the same
# experiment.
PROMPT_SHA256 = "3b0f71fb4b94b6ef34b820a7692d2a573c45ea2e96a9a58c8d02d9fb9895fdcb"


def _prompt_hash() -> str:
    import hashlib

    return hashlib.sha256(_SYSTEM.encode()).hexdigest()


def _decision_schema(symbols: list[str]) -> dict:
    return {
        "type": "object",
        "properties": {
            "rationale": {"type": "string"},
            "weights": {
                "type": "object",
                "properties": {s: {"type": "number"} for s in symbols},
                "required": list(symbols),
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

    tickers = ", ".join(symbols)
    news_r = track(client.messages.create(
        model=QUICK_MODEL, max_tokens=1500,
        system=_SYSTEM,
        tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 5}],
        messages=[{"role": "user", "content":
                   "Search for the most important US equity market news of the "
                   "last 24-48 hours (macro, Fed, earnings, guidance, sector "
                   "flows, single-stock events) relevant to this universe: "
                   f"{tickers}. Summarize the 8-10 most decision-relevant items "
                   "with dates. Facts only, no recommendations."}]))
    news = _text(news_r)

    def analyst(side: str):
        stance = ("strongest case for taking/keeping LONG exposure and which stocks"
                  if side == "bull" else
                  "strongest case for CAUTION (reducing to cash) and which stocks to avoid")
        # max_tokens covers adaptive thinking + text on this model; the 50-name
        # universe needs real headroom or the text block never gets emitted
        return track(client.messages.create(
            model=DEEP_MODEL, max_tokens=3000, system=_SYSTEM,
            messages=[{"role": "user", "content":
                       f"MARKET DATA:\n{brief}\n\nNEWS:\n{news}\n\n"
                       f"You are the {side.upper()} researcher. Make the {stance}. "
                       "Ground every claim in the data or news above. <=250 words."}]))

    bull_r, bear_r = analyst("bull"), analyst("bear")
    bull, bear = _text(bull_r), _text(bear_r)

    # 8000: adaptive thinking shares the cap with the JSON answer; a truncated
    # thinking phase returns NO text block at all (2026-08-05 first-tick bug)
    decision_r = track(client.messages.create(
        model=DEEP_MODEL, max_tokens=8000, system=_SYSTEM,
        output_config={"format": {"type": "json_schema",
                                  "schema": _decision_schema(symbols)}},
        messages=[{"role": "user", "content":
                   f"MARKET DATA:\n{brief}\n\nNEWS:\n{news}\n\n"
                   f"BULL CASE:\n{bull}\n\nBEAR CASE:\n{bear}\n\n"
                   f"YOUR RECENT DECISIONS:\n{recent_memory()}\n\n"
                   "You are the trader. Weigh both cases and output today's "
                   "target weights (0 for every stock you don't want). Only "
                   "deviate from your previous book when the evidence justifies "
                   "paying transaction costs."}]))
    payload = json.loads(_text(decision_r))
    weights = parse_decision(payload, symbols)

    archive = {"news": news, "bull": bull, "bear": bear,
               "rationale": payload.get("rationale", ""), "usage": usage}
    return weights, archive


# -- per-trading-day-cached targets_fn -----------------------------------------

def make_targets_fn(preset):
    def targets_fn(bars_by_symbol: dict[str, pd.DataFrame]):
        closes = {s: float(b["close"].iloc[-1]) for s, b in bars_by_symbol.items()}
        skey = session_date(bars_by_symbol)
        cache = DECISIONS / f"{skey}.json"
        if cache.exists():
            weights = json.loads(cache.read_text())["weights"]
            return {s: float(weights.get(s, 0.0)) for s in preset.symbols}, closes

        # Committee unavailability (API billing/outage) must not stop the
        # BOOKKEEPING: fall back to the previous decision — positions freeze,
        # marking continues, and the missing decision surfaces via health's
        # decision-freshness check (llm_agents fallback semantics, verbatim).
        import anthropic

        try:
            if _prompt_hash() != PROMPT_SHA256:
                raise RuntimeError(
                    "prompt drift: _SYSTEM hash != pinned PROMPT_SHA256 — "
                    "amend the prereg to re-pin before new decisions")
            client = anthropic.Anthropic()
            reflect_matured(client)  # lessons land before today's meeting
            brief = market_brief(bars_by_symbol)
            weights, archive = run_committee(client, brief, preset.symbols)
        except Exception as e:  # noqa: BLE001 — TickDeadline passes (BaseException)
            print(f"  committee unavailable ({type(e).__name__}: {str(e)[:90]}) "
                  "— holding previous book, mark continues")
            prev = sorted(DECISIONS.glob("*.json"))
            if not prev:
                return {s: 0.0 for s in preset.symbols}, closes
            weights = json.loads(prev[-1].read_text())["weights"]
            return {s: float(weights.get(s, 0.0)) for s in preset.symbols}, closes

        DECISIONS.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(
            {"date": skey, "weights": weights, "rationale": archive["rationale"],
             "usage": archive["usage"], "prompt_sha256": PROMPT_SHA256}, indent=2))
        (DECISIONS / f"{skey}.md").write_text(
            f"# llm_us decision {skey}\n\n## Market brief\n{brief}\n\n"
            f"## News (haiku + web search)\n{archive['news']}\n\n"
            f"## Bull case\n{archive['bull']}\n\n## Bear case\n{archive['bear']}\n\n"
            f"## Decision rationale\n{archive['rationale']}\n\n"
            f"## Weights\n{json.dumps({k: v for k, v in weights.items() if v}, indent=2)}\n\n"
            f"## Usage\n{json.dumps(archive['usage'], indent=2)}\n")
        return weights, closes

    return targets_fn
