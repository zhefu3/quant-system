"""E14: trend + regime-meanrev ensemble over the full 3-year cycle.

Each leg vol-targeted, then risk-split. Also prints each leg alone and a
BTC-only buy&hold reference so the diversification benefit is visible.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qtrade.backtest.portfolio import run_portfolio  # noqa: E402
from qtrade.data.store import BarStore  # noqa: E402
from qtrade.markets.rules import CRYPTO_PERP  # noqa: E402
from qtrade.strategies.composite import Composite  # noqa: E402
from qtrade.strategies.cta import CTATrend  # noqa: E402
from qtrade.strategies.meanrev import BollingerRevert  # noqa: E402
from qtrade.strategies.overlays import VolTarget  # noqa: E402


def vt(strategy, tv=0.4):
    return VolTarget(strategy, target_vol=tv, vol_window=168, bars_per_year=8760)


def main():
    store = BarStore()
    cov = store.coverage()
    syms = sorted(cov[(cov["market"] == "crypto") & (cov["timeframe"] == "1h")]["symbol"])
    bars = {s: store.load("crypto", s, "1h") for s in syms}

    trend = vt(CTATrend(h1=48, h2=120, h3=288))
    slow_trend = vt(CTATrend(h1=96, h2=288, h3=720))
    meanrev = vt(BollingerRevert(window=96, entry_z=2.0, side="both", regime_window=720))
    book = {
        "trend_only": (trend, 0.02),
        "meanrev_only": (meanrev, 0.02),
        "ensemble_50_50": (Composite([(trend, 0.5), (meanrev, 0.5)]), 0.02),
        "slow_trend_only": (slow_trend, 0.05),
        "slow_ensemble": (Composite([(slow_trend, 0.5), (meanrev, 0.5)]), 0.05),
    }
    for name, (strat, eps) in book.items():
        res = run_portfolio(strat, bars, CRYPTO_PERP, "1h", allocation="equal",
                            rebalance_eps=eps)
        print(f"\n=== {name} (eps={eps}) ===")
        print(res[["return_pct", "bench_ew_bh_pct", "edge_pp", "sharpe", "max_dd_pct", "trades", "fees"]].to_string())


if __name__ == "__main__":
    main()
