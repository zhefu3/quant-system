"""E40: managed-futures trend book on 26 years of daily data (IBKR-executable).

The most documented strategy family in the literature, applied through our
own validated construction: multi-horizon EWMA trend votes (1/3/12 months),
per-symbol vol targeting, equal risk allocation, long-short, throttled.

Data caveat ON THE RECORD: yfinance continuous contracts are front-month
splices — roll gaps add noise and carry/roll yield is invisible. Results get
a haircut until IBKR's properly back-adjusted series replace them. Costs
modeled at micro-futures retail levels (0.01% fee + 0.02% slippage per side).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qtrade.backtest.portfolio import run_portfolio  # noqa: E402
from qtrade.data.store import BarStore  # noqa: E402
from qtrade.markets.rules import MarketRules  # noqa: E402
from qtrade.strategies.cta import CTATrend  # noqa: E402
from qtrade.strategies.overlays import VolTarget  # noqa: E402

FUTURES_RULES = MarketRules(market="futures", fee_rate=0.0001, slippage=0.0002,
                            allow_short=True)
UNIVERSE = ["ES", "NQ", "ZN", "GC", "CL", "HG", "NG", "ZC", "SI"]


def book():
    trend = CTATrend(h1=21, h2=63, h3=252)  # 1/3/12-month votes
    return VolTarget(trend, target_vol=0.30, vol_window=63, bars_per_year=252)


def main():
    store = BarStore()
    bars = {s: store.load("futures", s, "1d") for s in UNIVERSE}
    start = max(b.index[0] for b in bars.values())
    bars = {s: b[b.index >= start] for s, b in bars.items()}
    n = len(next(iter(bars.values())))
    print(f"panel: {len(bars)} contracts x {n} days, {start.date()} ->")

    res = run_portfolio(book(), bars, FUTURES_RULES, "1d", allocation="equal",
                        rebalance_eps=0.02)
    print(res[["return_pct", "bench_ew_bh_pct", "edge_pp", "sharpe",
               "max_dd_pct", "trades", "fees"]].to_string())

    print("\n=== year by year ===")
    for year in range(2002, 2027):
        y0 = pd.Timestamp(f"{year}-01-01", tz="UTC")
        y1 = pd.Timestamp(f"{year + 1}-01-01", tz="UTC")
        yb = {s: b[(b.index >= y0 - pd.Timedelta(days=420)) & (b.index < y1)]
              for s, b in bars.items()}
        if min(len(b) for b in yb.values()) < 400:
            continue
        r = run_portfolio(book(), yb, FUTURES_RULES, "1d", allocation="equal",
                          rebalance_eps=0.02, oos_fraction=0.0001).loc["full"]
        print(f"{year}: ret {r['return_pct']:+7.2f}%  dd {r['max_dd_pct']:5.1f}%  "
              f"sharpe {r['sharpe']:5.2f}")


if __name__ == "__main__":
    main()
