"""E42: A-share EXECUTABLE multi-asset rotation (via exchange-traded ETFs).

Universe = what a retail A-share account can actually buy on-exchange:
  沪深300(510300)  中证500(510500)  国债(511010)  黄金(518880)  纳指(513100)

Validation data = the tracking targets / clean proxies:
  SH_000300, SH_000905, SH_000012 (baostock indexes, 16y)
  GLD, QQQ (US ETFs as proxies for the QDII ETFs; FX/premium noted as caveat)

Structures tested (both classic retail-executable):
  A) momentum rotation: monthly, hold top-2 by 3m return
  B) trend long-flat per asset: our CTA construction, long-only, equal alloc

Costs: ETF fees 0.03% + slip 0.05%, T+1. Cross-asset correlation is the
free lunch individual stocks never gave us — this is the test.
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
from qtrade.strategies.xsection import XSectionMomentum  # noqa: E402

ETF_RULES = MarketRules(market="ashare_etf", fee_rate=0.0003, slippage=0.0005,
                        t_plus_one=True, tz="Asia/Shanghai")


def load_panel():
    store = BarStore()
    bars = {
        "HS300": store.load("ashare_index", "SH_000300", "1d"),
        "CSI500": store.load("ashare_index", "SH_000905", "1d"),
        "国债": store.load("ashare_index", "SH_000012", "1d"),
        "黄金": store.load("etf", "GLD", "1d"),
        "纳指": store.load("etf", "QQQ", "1d"),
    }
    start = max(b.index[0] for b in bars.values())
    return {k: v[v.index >= start] for k, v in bars.items()}


def main():
    bars = load_panel()
    n = len(next(iter(bars.values())))
    print(f"panel: {list(bars)} from {next(iter(bars.values())).index[0].date()}")

    print("\n=== A) 动量轮动 top2 月调 (3m lookback, skip 5d) ===")
    strat = XSectionMomentum(lookback=63, skip=5, top_k=2, rebalance=21)
    res = run_portfolio(strat, bars, ETF_RULES, "1d", align="ffill",
                        rebalance_eps=0.02)
    print(res[["return_pct", "bench_ew_bh_pct", "edge_pp", "sharpe",
               "max_dd_pct", "fees"]].to_string())

    print("\n=== B) 逐资产趋势 long-flat (CTA 1/3/12月, vol target 15%) ===")
    trend = VolTarget(CTATrend(h1=21, h2=63, h3=252, long_only=True),
                      target_vol=0.15, vol_window=63, bars_per_year=252)
    res = run_portfolio(trend, bars, ETF_RULES, "1d", align="ffill",
                        rebalance_eps=0.02)
    print(res[["return_pct", "bench_ew_bh_pct", "edge_pp", "sharpe",
               "max_dd_pct", "fees"]].to_string())

    print("\n=== 逐年 (变体 B) ===")
    full = load_panel()
    for year in range(2012, 2027):
        y0 = pd.Timestamp(f"{year}-01-01", tz="UTC")
        y1 = pd.Timestamp(f"{year + 1}-01-01", tz="UTC")
        yb = {s: b[(b.index >= y0 - pd.Timedelta(days=420)) & (b.index < y1)]
              for s, b in full.items()}
        if min(len(b) for b in yb.values()) < 400:
            continue
        r = run_portfolio(trend, yb, ETF_RULES, "1d", align="ffill",
                          rebalance_eps=0.02, oos_fraction=0.0001).loc["full"]
        print(f"{year}: ret {r['return_pct']:+7.2f}%  dd {r['max_dd_pct']:5.1f}%  "
              f"sharpe {r['sharpe']:5.2f}")


if __name__ == "__main__":
    main()
