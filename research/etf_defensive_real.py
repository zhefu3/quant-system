"""E54: E42 variant B on REAL exchange ETF prices (prereg 2026-07-12).

Fetches back-adjusted (hfq) daily bars for the five tradable ETFs via akshare,
stores them under market "ashare_etf", and reruns the frozen defensive book.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qtrade.backtest.portfolio import run_portfolio  # noqa: E402
from qtrade.data.schema import normalize_ohlcv  # noqa: E402
from qtrade.data.store import BarStore  # noqa: E402
from qtrade.markets.rules import MarketRules  # noqa: E402
from qtrade.strategies.cta import CTATrend  # noqa: E402
from qtrade.strategies.overlays import VolTarget  # noqa: E402

ETF_RULES = MarketRules(market="ashare_etf", fee_rate=0.0003, slippage=0.0005,
                        t_plus_one=True, tz="Asia/Shanghai")
UNIVERSE = {"510300": "沪深300", "510500": "中证500", "511010": "国债",
            "518880": "黄金", "513100": "纳指"}


def fetch():
    """Tushare route (eastmoney refused connections): fund_daily raw OHLCV
    windowed under the 2000-row cap, times fund_adj factor = 后复权."""
    import os

    import tushare as ts

    pro = ts.pro_api(os.environ["TUSHARE_TOKEN"])
    store = BarStore()
    for sym, name in UNIVERSE.items():
        if store.path("ashare_etf", sym, "1d").exists():
            continue
        code = f"{sym}.SH"
        parts = [pro.fund_daily(ts_code=code, start_date=s, end_date=e)
                 for s, e in (("20120101", "20171231"), ("20180101", "20231231"),
                              ("20240101", "20261231"))]
        df = pd.concat(parts).drop_duplicates("trade_date").sort_values("trade_date")
        adj = pro.fund_adj(ts_code=code)[["trade_date", "adj_factor"]]
        df = df.merge(adj, on="trade_date", how="left")
        df["adj_factor"] = df["adj_factor"].ffill().fillna(1.0)
        out = df[["open", "high", "low", "close"]].apply(pd.to_numeric, errors="coerce")
        out = out.mul(pd.to_numeric(df["adj_factor"], errors="coerce").values, axis=0)
        out["volume"] = pd.to_numeric(df["vol"], errors="coerce").values
        out.index = (pd.to_datetime(df["trade_date"]) + pd.Timedelta(hours=15)
                     ).dt.tz_localize("Asia/Shanghai").dt.tz_convert("UTC")
        store.save(normalize_ohlcv(out), "ashare_etf", sym, "1d")
        print(f"{sym} {name}: {len(out)} bars {out.index[0].date()} -> {out.index[-1].date()}",
              flush=True)
        time.sleep(1.0)


def book():
    return VolTarget(CTATrend(h1=21, h2=63, h3=252, long_only=True),
                     target_vol=0.15, vol_window=63, bars_per_year=252)


def main():
    fetch()
    store = BarStore()
    bars = {s: store.load("ashare_etf", s, "1d") for s in UNIVERSE}
    start = max(b.index[0] for b in bars.values())
    bars = {s: b[b.index >= start] for s, b in bars.items()}
    print(f"\npanel from {start.date()}, {len(next(iter(bars.values())))} days")

    res = run_portfolio(book(), bars, ETF_RULES, "1d", allocation="equal",
                        rebalance_eps=0.02, align="ffill")
    print(res[["return_pct", "bench_ew_bh_pct", "edge_pp", "sharpe",
               "max_dd_pct", "trades", "fees"]].to_string())

    print("\n=== year by year ===")
    for year in range(2014, 2027):
        y0 = pd.Timestamp(f"{year}-01-01", tz="UTC")
        y1 = pd.Timestamp(f"{year + 1}-01-01", tz="UTC")
        yb = {s: b[(b.index >= y0 - pd.Timedelta(days=420)) & (b.index < y1)]
              for s, b in bars.items()}
        if min(len(b) for b in yb.values()) < 350:
            continue
        r = run_portfolio(book(), yb, ETF_RULES, "1d", allocation="equal",
                          rebalance_eps=0.02, align="ffill",
                          oos_fraction=0.0001).loc["full"]
        print(f"{year}: ret {r['return_pct']:+7.2f}%  dd {r['max_dd_pct']:5.1f}%  "
              f"sharpe {r['sharpe']:5.2f}")


if __name__ == "__main__":
    main()
