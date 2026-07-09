"""Named book presets shared by backtest and paper trading.

One definition, two consumers — the strategy that was validated is exactly
the strategy that paper-trades. Drift between research and execution configs
is a classic way to lose money.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .markets.rules import CRYPTO_PERP, MarketRules
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
    build: object = field(repr=False)  # () -> Strategy

    def strategy(self) -> Strategy:
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
)

PRESETS = {p.name: p for p in (CRYPTO_CORE, CRYPTO_CORE_4H, CRYPTO_CORE_V2)}
