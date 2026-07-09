"""E22: A-share index trend timing (executable via index ETFs, e.g. 510300).

Long/flat CTA trend on index daily bars over a long history (2010+ covers the
2015 bubble/crash, 2018 bear, 2019-21 bull, 2022-24 grind, 2024-09 rally).
Retail-executable: no shorting, T+1 satisfied by daily bars + next-bar fills.
ETF costs are lower than single stocks (no stamp duty): fee ~0.03%+slippage.

Gates: full-period edge vs buy&hold AND max-DD reduction AND WF folds.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qtrade.backtest.engine import Engine  # noqa: E402
from qtrade.backtest.report import render_text  # noqa: E402
from qtrade.data.store import BarStore  # noqa: E402
from qtrade.markets.rules import MarketRules  # noqa: E402
from qtrade.research import walk_forward  # noqa: E402
from qtrade.research.walkforward import wf_verdict  # noqa: E402
from qtrade.strategies.cta import CTATrend  # noqa: E402

# ETF trading: broker commission only, no stamp duty.
ETF_RULES = MarketRules(market="ashare_etf", fee_rate=0.0003, slippage=0.0005,
                        t_plus_one=True, tz="Asia/Shanghai")

INDEXES = ["SH_000300", "SH_000905", "SH_000016"]


def main():
    store = BarStore()
    for sym in INDEXES:
        bars = store.load("ashare_index", sym, "1d")
        print(f"\n######## {sym}: {len(bars)} bars "
              f"({bars.index[0].date()} -> {bars.index[-1].date()}) ########")

        strat = CTATrend(h1=20, h2=60, h3=200, long_only=True)
        res = Engine(ETF_RULES, rebalance_eps=0.05).run(
            strat, bars, symbol=sym, timeframe="1d")
        print(render_text(res))

        wf = walk_forward(
            CTATrend, bars,
            grid={"h1": [10, 20, 40], "h3": [120, 200, 300]},
            rules=ETF_RULES, timeframe="1d", n_folds=6,
            fixed={"h2": 60, "long_only": True},
        )
        print(wf[["test_start", "test_end", "chosen", "test_return_pct",
                  "test_benchmark_pct", "test_edge_pct", "test_max_dd_pct"]].to_string(index=False))
        print(wf_verdict(wf))


if __name__ == "__main__":
    main()
