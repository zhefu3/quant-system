"""Named book presets shared by backtest and paper trading.

One definition, two consumers — the strategy that was validated is exactly
the strategy that paper-trades. Drift between research and execution configs
is a classic way to lose money.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .live.risk import RiskLimits
from .markets.rules import (
    ASHARE as ASHARE_RULES,
    CNFUTURES,
    CRYPTO_PERP,
    FUTURES_IBKR as FUTURES_IBKR_RULES,
    MarketRules,
)
from .strategies.base import Strategy
from .strategies.composite import Composite
from .strategies.cta import CTATrend
from .strategies.meanrev import BollingerRevert
from .strategies.overlays import VolTarget


@dataclass
class BookPreset:
    name: str
    market: str
    timeframe: str
    symbols: list[str]
    rules: MarketRules
    rebalance_eps: float
    # () -> Strategy; None for books whose targets don't come from a Strategy
    # (e.g. llm_agents, whose weights come from an agent chain via targets_fn)
    build: object | None = field(default=None, repr=False)
    # Pre-trade risk gate budget. dd_halt is sized at ~1.5x the book's
    # validated backtest max drawdown: normal operation never touches it,
    # beyond-backtest behavior flattens the book pending human review.
    risk: RiskLimits = field(default_factory=RiskLimits)

    def strategy(self) -> Strategy:
        if self.build is None:
            raise TypeError(f"preset {self.name} has no Strategy — targets come "
                            "from an injected targets_fn (see live/paper.py)")
        return self.build()


def _crypto_core_strategy() -> Strategy:
    def vt(s):
        return VolTarget(s, target_vol=0.4, vol_window=168, bars_per_year=8760)

    trend = vt(CTATrend(h1=96, h2=288, h3=720))
    meanrev = vt(BollingerRevert(window=96, entry_z=2.0, side="both", regime_window=720))
    return Composite([(trend, 0.5), (meanrev, 0.5)])


_UNIVERSE = [
    "ADA/USDT", "AVAX/USDT", "BTC/USDT", "DOGE/USDT", "DOT/USDT",
    "ETH/USDT", "LINK/USDT", "LTC/USDT", "SOL/USDT", "XRP/USDT",
]

CRYPTO_CORE = BookPreset(
    name="crypto_core",
    market="crypto",
    timeframe="1h",
    symbols=list(_UNIVERSE),
    rules=CRYPTO_PERP,
    rebalance_eps=0.05,
    build=_crypto_core_strategy,
    risk=RiskLimits(max_weight=0.25, max_gross=2.0, dd_halt=0.23, max_data_age_bars=6),
)


def _crypto_core_4h_strategy() -> Strategy:
    # E24 parallel variant: same construction, horizons /4 for 4h bars.
    # Sharpe parity with 1h (1.10 vs 1.16), fees -25%; kept as an execution
    # option (fewer ticks needed), NOT the capital default.
    def vt(s):
        return VolTarget(s, target_vol=0.4, vol_window=42, bars_per_year=2190)

    trend = vt(CTATrend(h1=24, h2=72, h3=180))
    meanrev = vt(BollingerRevert(window=24, entry_z=2.0, side="both", regime_window=180))
    return Composite([(trend, 0.5), (meanrev, 0.5)])


CRYPTO_CORE_4H = BookPreset(
    name="crypto_core_4h",
    market="crypto",
    timeframe="4h",
    symbols=list(_UNIVERSE),
    rules=CRYPTO_PERP,
    rebalance_eps=0.05,
    build=_crypto_core_4h_strategy,
    risk=RiskLimits(max_weight=0.25, max_gross=2.0, dd_halt=0.23, max_data_age_bars=6),
)

def _crypto_core_v2_strategy() -> Strategy:
    # E34/E35 candidate: meanrev shorts additionally require close < MA2160
    # (deep-bear confirmation). Improved all three panels incl the pristine
    # 2019-22 segment, but Sharpe deltas (+0.04/+0.07/+0.08) sit below the
    # pre-registered +0.1 replacement gate — so it runs as a PARALLEL preset.
    # Promotion rule (pre-registered 2026-07-10): replaces crypto_core at the
    # next quarterly review iff its paper record and the new out-of-sample
    # quarter confirm non-inferiority (paper Sharpe >= v1, no new worst-DD).
    def vt(s):
        return VolTarget(s, target_vol=0.4, vol_window=168, bars_per_year=8760)

    trend = vt(CTATrend(h1=96, h2=288, h3=720))
    meanrev = vt(BollingerRevert(window=96, entry_z=2.0, side="both",
                                 regime_window=720, short_regime_window=2160))
    return Composite([(trend, 0.5), (meanrev, 0.5)])


CRYPTO_CORE_V2 = BookPreset(
    name="crypto_core_v2",
    market="crypto",
    timeframe="1h",
    symbols=list(_UNIVERSE),
    rules=CRYPTO_PERP,
    rebalance_eps=0.05,
    build=_crypto_core_v2_strategy,
    risk=RiskLimits(max_weight=0.25, max_gross=2.0, dd_halt=0.23, max_data_age_bars=6),
)

def _cn_futures_strategy() -> Strategy:
    # E50b-approved book (2026-07-12): stitched-data audit Sharpe 0.48 over
    # 8.1y, OOS 0.67, worst year -2.3%. Parameters frozen since E50 — the
    # trend horizons and vol target below are exactly what the audit ran.
    return VolTarget(CTATrend(h1=21, h2=63, h3=252), target_vol=0.30,
                     vol_window=63, bars_per_year=252)


CN_FUTURES = BookPreset(
    name="cn_futures",
    market="cnfutures",
    timeframe="1d",
    symbols=["RB", "I", "J", "M", "Y", "CF", "SR", "TA", "MA", "CU", "AL", "AU", "AG", "RU"],
    rules=CNFUTURES,
    rebalance_eps=0.02,
    build=_cn_futures_strategy,
    # 5-day age tolerates weekends/short holidays; long holidays skip ticks,
    # which is correct (closed market, nothing to trade).
    risk=RiskLimits(max_weight=0.25, max_gross=2.0, dd_halt=0.185, max_data_age_bars=5),
)

def _futures_ibkr_strategy() -> Strategy:
    # E40/E40b frozen construction, identical to the cn_futures book but on
    # US futures. OBSERVATION BOOK ONLY (prereg 2026-07-13): E40b failed the
    # deployment gate (window Sharpe -0.15), so this paper record exists to
    # build the multi-year forward track envisioned as unlock path (2) in the
    # E40b verdict. It must NOT enter the portfolio layer (allocate.py) or be
    # cited as capital-allocation evidence.
    return VolTarget(CTATrend(h1=21, h2=63, h3=252), target_vol=0.30,
                     vol_window=63, bars_per_year=252)


FUTURES_IBKR = BookPreset(
    name="futures_ibkr",
    market="futures_ibkr",
    timeframe="1d",
    symbols=["ES", "NQ", "ZN", "GC", "CL", "HG", "NG", "ZC", "SI"],
    rules=FUTURES_IBKR_RULES,
    rebalance_eps=0.02,
    build=_futures_ibkr_strategy,
    # dd_halt = 1.5x the E40b IBKR-window backtest maxDD (24.6%); 5-bar age
    # tolerates weekends/holidays, and an offline IB Gateway simply skips ticks.
    risk=RiskLimits(max_weight=0.25, max_gross=2.0, dd_halt=0.37, max_data_age_bars=5),
)

# E60 OBSERVATION book (prereg 2026-07-14): an LLM committee (bull/bear debate
# -> trader decision, TradingAgents-style) trades crypto_core's exact universe
# and cost model — the honest A/B: LLM judgment vs a frozen mechanical system
# on identical assets. LLM systems cannot be backtested (knowledge-cutoff
# contamination = look-ahead), so this book is forward-record-only: not in the
# portfolio layer, never capital-allocation evidence. Tighter risk than
# crypto_core because there is NO validated backtest to size dd_halt from.
LLM_AGENTS = BookPreset(
    name="llm_agents",
    market="crypto",
    timeframe="1d",
    symbols=list(_UNIVERSE),
    rules=CRYPTO_PERP,
    rebalance_eps=0.02,
    build=None,  # targets come from qtrade/live/llm_agents.py
    risk=RiskLimits(max_weight=0.10, max_gross=1.0, dd_halt=0.15, max_data_age_bars=2),
)

# E61 OBSERVATION book: E47's LightGBM index-enhancement, forward paper record.
# Universe is ~300 point-in-time HS300 members (dynamic), so symbols is empty
# and the book bypasses PaperTrader (see live/ashare_ml.py). dd_halt sized for
# a long-only equity book (market-level drawdowns are normal, not a bug).
ASHARE_ML = BookPreset(
    name="ashare_ml",
    market="ashare",
    timeframe="1d",
    symbols=[],
    rules=ASHARE_RULES,
    rebalance_eps=0.0,  # monthly full rebalance, no eps band
    build=None,  # targets come from qtrade/live/ashare_ml.py
    risk=RiskLimits(max_weight=0.03, max_gross=1.0, dd_halt=0.35, max_data_age_bars=5),
)

PRESETS = {p.name: p for p in (CRYPTO_CORE, CRYPTO_CORE_4H, CRYPTO_CORE_V2, CN_FUTURES,
                               FUTURES_IBKR, LLM_AGENTS, ASHARE_ML)}
