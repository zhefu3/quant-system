"""E24: crypto_core on 4h bars (resampled from stored 1h) vs the 1h base.

Same construction, horizons scaled by 4 so the lookback windows cover the
same wall-clock spans. Hypothesis: coarser bars = less noise, less churn,
similar or better risk-adjusted returns.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qtrade.backtest.portfolio import run_portfolio  # noqa: E402
from qtrade.data.schema import resample_ohlcv  # noqa: E402
from qtrade.data.store import BarStore  # noqa: E402
from qtrade.markets.rules import CRYPTO_PERP  # noqa: E402
from qtrade.presets import CRYPTO_CORE  # noqa: E402
from qtrade.strategies.composite import Composite  # noqa: E402
from qtrade.strategies.cta import CTATrend  # noqa: E402
from qtrade.strategies.meanrev import BollingerRevert  # noqa: E402
from qtrade.strategies.overlays import VolTarget  # noqa: E402


def book_4h():
    def vt(s):
        return VolTarget(s, target_vol=0.4, vol_window=42, bars_per_year=2190)

    trend = vt(CTATrend(h1=24, h2=72, h3=180))
    meanrev = vt(BollingerRevert(window=24, entry_z=2.0, side="both", regime_window=180))
    return Composite([(trend, 0.5), (meanrev, 0.5)])


def main():
    p = CRYPTO_CORE
    store = BarStore()
    bars_1h = {s: store.load(p.market, s, p.timeframe) for s in p.symbols}
    # Same wall-clock span for both variants.
    start = max(b.index[0] for b in bars_1h.values())
    bars_1h = {s: b[b.index >= start] for s, b in bars_1h.items()}
    bars_4h = {s: resample_ohlcv(b, "4h") for s, b in bars_1h.items()}

    for name, bars, strat, tf in [
        ("base 1h", bars_1h, p.strategy(), "1h"),
        ("variant 4h", bars_4h, book_4h(), "4h"),
    ]:
        res = run_portfolio(strat, bars, CRYPTO_PERP, tf, allocation="equal",
                            rebalance_eps=0.05)
        print(f"\n=== {name} ===")
        print(res[["return_pct", "bench_ew_bh_pct", "edge_pp", "sharpe",
                   "max_dd_pct", "trades", "fees"]].to_string())


if __name__ == "__main__":
    main()
