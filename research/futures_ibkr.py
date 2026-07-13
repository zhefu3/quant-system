"""E40b: IBKR real-futures translation check (prereg 2026-07-13).

Frozen protocol identical to E40 (futures_trend.py); only the data source
changes to IBKR back-adjusted CONTFUT. Core test: does clean IBKR data fix
the 2022 red flag (yfinance showed -2.7% in the biggest trend year)?
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qtrade.backtest.portfolio import run_portfolio  # noqa: E402
from qtrade.data.store import BarStore  # noqa: E402
from qtrade.markets.rules import MarketRules  # noqa: E402
from qtrade.strategies.cta import CTATrend  # noqa: E402
from qtrade.strategies.overlays import VolTarget  # noqa: E402

FUTURES_RULES = MarketRules(market="futures_ibkr", fee_rate=0.0001,
                            slippage=0.0002, allow_short=True)
UNIVERSE = ["ES", "NQ", "ZN", "GC", "CL", "HG", "NG", "ZC", "SI"]


def book():
    return VolTarget(CTATrend(h1=21, h2=63, h3=252), target_vol=0.30,
                     vol_window=63, bars_per_year=252)


def single_book_returns(bars: pd.DataFrame) -> pd.Series:
    """Net daily returns of the trend book on one instrument."""
    _, d = run_portfolio(book(), {"X": bars}, FUTURES_RULES, "1d",
                         allocation="equal", rebalance_eps=0.02,
                         return_details=True)
    W, closes = d["weights"], d["closes"]
    gross = (W.shift(1) * closes.pct_change()).sum(axis=1)
    costs = (W - W.shift(1)).abs().sum(axis=1).fillna(0.0) * (
        FUTURES_RULES.fee_rate + FUTURES_RULES.slippage)
    return (gross - costs).dropna()


def sharpe(x):
    return float(x.mean() / x.std() * np.sqrt(252)) if x.std() > 0 else 0.0


def main():
    store = BarStore()
    bars = {s: store.load("futures_ibkr", s, "1d") for s in UNIVERSE}

    # --- CORE TEST: per-product 2022 return (the yfinance red flag) ---
    print("=== 2022 红旗检验（干净 IBKR 数据）===")
    print("(yfinance 版全组合 2022 仅 -2.7%；趋势账本该在史上最强趋势年大赚)")
    have2022 = []
    for s in UNIVERSE:
        b = bars[s]
        if b.index[0] > pd.Timestamp("2022-01-01", tz="UTC"):
            print(f"  {s:3s}: 无 2022 数据（IBKR 深度始 {b.index[0].date()}）")
            continue
        r = single_book_returns(b)
        r22 = r[(r.index >= "2022-01-01") & (r.index < "2023-01-01")]
        tot = (1 + r22).prod() - 1
        print(f"  {s:3s}: 2022 收益 {tot:+7.1%}  sharpe {sharpe(r22):+.2f}")
        have2022.append(tot)
    print(f"\n  有 2022 数据的品种均值收益: {np.mean(have2022):+.1%} "
          f"({'✅ 修正为正' if np.mean(have2022) > 0 else '❌ 仍为负'})")

    # --- portfolio over max window (products enter as data begins, ffill) ---
    print("\n=== 组合（全部可用窗口, ffill 对齐, 品种随数据入场）===")
    start = min(b.index[0] for b in bars.values())
    res = run_portfolio(book(), bars, FUTURES_RULES, "1d", allocation="equal",
                        rebalance_eps=0.02, align="ffill")
    print(res[["return_pct", "sharpe", "max_dd_pct", "trades", "fees"]].to_string())

    print("\n=== 逐年（组合）===")
    for year in range(2015, 2027):
        y0 = pd.Timestamp(f"{year}-01-01", tz="UTC")
        y1 = pd.Timestamp(f"{year + 1}-01-01", tz="UTC")
        yb = {s: b[(b.index >= y0 - pd.Timedelta(days=420)) & (b.index < y1)]
              for s, b in bars.items() if b.index[0] < y1}
        yb = {s: b for s, b in yb.items() if len(b) > 300}
        if len(yb) < 2:
            continue
        r = run_portfolio(book(), yb, FUTURES_RULES, "1d", allocation="equal",
                          rebalance_eps=0.02, align="ffill",
                          oos_fraction=0.0001).loc["full"]
        print(f"{year}: ret {r['return_pct']:+7.2f}%  dd {r['max_dd_pct']:5.1f}%  "
              f"sharpe {r['sharpe']:5.2f}  ({len(yb)} 品种)")


if __name__ == "__main__":
    main()
