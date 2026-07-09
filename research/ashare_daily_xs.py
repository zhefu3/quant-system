"""E20: A-share daily cross-sectional momentum on the HS300 universe.

KNOWN BIAS, on the record: the universe is TODAY's HS300 constituents, so the
backtest inherits survivorship/index-inclusion bias — winners that later
joined the index look investable before they were. Results are therefore an
UPPER BOUND; treat anything marginal as a fail.

Long-only (A-share retail cannot short single names), T+1 satisfied by
daily bars + next-bar execution, fees include stamp duty approximation.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qtrade.backtest.portfolio import run_portfolio  # noqa: E402
from qtrade.data.store import BarStore  # noqa: E402
from qtrade.markets.rules import ASHARE  # noqa: E402
from qtrade.strategies.xsection import XSectionMomentum  # noqa: E402


def main():
    store = BarStore()
    cov = store.coverage()
    daily = cov[(cov["market"] == "ashare") & (cov["timeframe"] == "1d")]
    symbols = sorted(daily["symbol"])
    print(f"universe: {len(symbols)} names, daily bars")
    bars = {s: store.load("ashare", s, "1d") for s in symbols}

    variants = {
        "mom 120d skip5, top30, monthly": XSectionMomentum(
            lookback=120, skip=5, top_k=30, rebalance=21),
        "mom 60d skip5, top30, monthly": XSectionMomentum(
            lookback=60, skip=5, top_k=30, rebalance=21),
        "mom 250d skip21, top30, monthly": XSectionMomentum(
            lookback=250, skip=21, top_k=30, rebalance=21),
        "mom 120d skip5, top50, monthly": XSectionMomentum(
            lookback=120, skip=5, top_k=50, rebalance=21),
        "mom 120d skip5, top30, weekly": XSectionMomentum(
            lookback=120, skip=5, top_k=30, rebalance=5),
    }
    for name, strat in variants.items():
        res = run_portfolio(strat, bars, ASHARE, "1d", align="ffill",
                            rebalance_eps=0.002)
        print(f"\n=== {name} ===")
        print(res[["return_pct", "bench_ew_bh_pct", "edge_pp", "sharpe",
                   "max_dd_pct", "trades", "fees"]].to_string())


if __name__ == "__main__":
    main()
