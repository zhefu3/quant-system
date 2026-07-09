"""E20/E21: daily cross-sectional momentum on a stock universe (A-share / US).

KNOWN BIAS, on the record: universes are TODAY's index members / megacaps, so
results inherit survivorship & index-inclusion bias and are an UPPER BOUND.
Treat marginal results as fails; only large, robust edges earn a next step.

    .venv/bin/python research/daily_xs.py --market ashare
    .venv/bin/python research/daily_xs.py --market us [--long-short]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qtrade.backtest.portfolio import run_portfolio  # noqa: E402
from qtrade.data.store import BarStore  # noqa: E402
from qtrade.markets.rules import BY_NAME  # noqa: E402
from qtrade.strategies.xsection import XSectionMomentum  # noqa: E402


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--market", required=True, choices=["ashare", "us"])
    p.add_argument("--long-short", action="store_true")
    args = p.parse_args()
    rules = BY_NAME[args.market]

    store = BarStore()
    cov = store.coverage()
    daily = cov[(cov["market"] == args.market) & (cov["timeframe"] == "1d")]
    symbols = sorted(daily["symbol"])
    print(f"universe: {len(symbols)} names, daily bars, rules={rules.market} "
          f"(fee {rules.fee_rate:.2%}, slip {rules.slippage:.2%})")
    bars = {s: store.load(args.market, s, "1d") for s in symbols}

    ls = args.long_short
    variants = {
        "mom 120d skip5, top30, monthly": XSectionMomentum(
            lookback=120, skip=5, top_k=30, rebalance=21, long_short=ls),
        "mom 60d skip5, top30, monthly": XSectionMomentum(
            lookback=60, skip=5, top_k=30, rebalance=21, long_short=ls),
        "mom 250d skip21, top30, monthly": XSectionMomentum(
            lookback=250, skip=21, top_k=30, rebalance=21, long_short=ls),
        "mom 120d skip5, top15, monthly": XSectionMomentum(
            lookback=120, skip=5, top_k=15, rebalance=21, long_short=ls),
        "mom 120d skip5, top30, weekly": XSectionMomentum(
            lookback=120, skip=5, top_k=30, rebalance=5, long_short=ls),
    }
    for name, strat in variants.items():
        res = run_portfolio(strat, bars, rules, "1d", align="ffill",
                            rebalance_eps=0.002)
        print(f"\n=== {name}{' LS' if ls else ''} ===")
        print(res[["return_pct", "bench_ew_bh_pct", "edge_pp", "sharpe",
                   "max_dd_pct", "trades", "fees"]].to_string())


if __name__ == "__main__":
    main()
