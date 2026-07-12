"""E50: domestic commodity futures CTA — the mainland CTA industry's home turf.

Universe: 14 liquid contracts across sectors (black/agri/chem/metal/precious),
main-continuous daily from sina via akshare (17-21y, free, no account).

DATA CAVEAT ON RECORD: main-continuous series are roll-spliced without
back-adjustment (same class of defect as E40's yfinance futures). Results
get a haircut; promising outcomes trigger a per-contract stitching upgrade
before any deployment decision.

Costs: Chinese commodity futures are cheap — fee 0.02% + slip 0.04% per side
(conservative). Both directions allowed. Execution venue: any domestic
futures account (NO capital threshold for commodities, unlike index futures).
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

CN_FUT_RULES = MarketRules(market="cnfutures", fee_rate=0.0002, slippage=0.0004,
                           allow_short=True)
UNIVERSE = {
    "RB0": "螺纹钢", "I0": "铁矿石", "J0": "焦炭", "M0": "豆粕", "Y0": "豆油",
    "CF0": "棉花", "SR0": "白糖", "TA0": "PTA", "MA0": "甲醇", "CU0": "沪铜",
    "AL0": "沪铝", "AU0": "沪金", "AG0": "沪银", "RU0": "橡胶",
}


def fetch():
    import akshare as ak

    store = BarStore()
    for sym in UNIVERSE:
        if store.path("cnfutures", sym, "1d").exists():
            continue
        try:
            df = ak.futures_main_sina(symbol=sym)
            df.columns = ["ts", "open", "high", "low", "close", "volume", "hold", "settle"][:len(df.columns)]
            out = df[["open", "high", "low", "close", "volume"]].apply(pd.to_numeric, errors="coerce")
            out.index = (pd.to_datetime(df["ts"]) + pd.Timedelta(hours=15)
                         ).dt.tz_localize("Asia/Shanghai").dt.tz_convert("UTC")
            store.save(normalize_ohlcv(out), "cnfutures", sym, "1d")
            print(f"{sym} {UNIVERSE[sym]}: {len(out)} bars", flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"{sym} FAIL: {str(e)[:60]}", flush=True)
        time.sleep(1.0)


def book():
    return VolTarget(CTATrend(h1=21, h2=63, h3=252), target_vol=0.30,
                     vol_window=63, bars_per_year=252)


def main():
    fetch()
    store = BarStore()
    cov = store.coverage()
    syms = sorted(cov[cov["market"] == "cnfutures"]["symbol"])
    bars = {s: store.load("cnfutures", s, "1d") for s in syms}
    start = max(b.index[0] for b in bars.values())
    bars = {s: b[b.index >= start] for s, b in bars.items()}
    n = len(next(iter(bars.values())))
    print(f"\npanel: {len(bars)} contracts from {start.date()} ({n} days)")

    res = run_portfolio(book(), bars, CN_FUT_RULES, "1d", allocation="equal",
                        rebalance_eps=0.02, align="ffill")
    print(res[["return_pct", "bench_ew_bh_pct", "edge_pp", "sharpe",
               "max_dd_pct", "trades", "fees"]].to_string())

    print("\n=== year by year ===")
    for year in range(2015, 2027):
        y0 = pd.Timestamp(f"{year}-01-01", tz="UTC")
        y1 = pd.Timestamp(f"{year + 1}-01-01", tz="UTC")
        yb = {s: b[(b.index >= y0 - pd.Timedelta(days=420)) & (b.index < y1)]
              for s, b in bars.items()}
        if min(len(b) for b in yb.values()) < 350:
            continue
        r = run_portfolio(book(), yb, CN_FUT_RULES, "1d", allocation="equal",
                          rebalance_eps=0.02, align="ffill",
                          oos_fraction=0.0001).loc["full"]
        print(f"{year}: ret {r['return_pct']:+7.2f}%  dd {r['max_dd_pct']:5.1f}%  "
              f"sharpe {r['sharpe']:5.2f}")


if __name__ == "__main__":
    main()
