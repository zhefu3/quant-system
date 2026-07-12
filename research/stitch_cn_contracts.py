"""E50b: stitch per-contract dailies into back-adjusted continuous series, then audit.

Frozen rules (preregistered in log.md, 2026-07-12) live in qtrade.data.cn_futures —
promoted there after the verdict so research and the live paper path share one
implementation. This script is the full-history audit runner.

Verdict (2026-07-12): full-period Sharpe 0.48 >= 0.4 -> approved; preset
`cn_futures` created, queued for paper.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qtrade.backtest.portfolio import run_portfolio  # noqa: E402
from qtrade.data.cn_futures import PRODUCTS, load_product, stitch  # noqa: E402,F401
from qtrade.data.schema import normalize_ohlcv  # noqa: E402
from qtrade.data.store import BarStore  # noqa: E402
from qtrade.markets.rules import CNFUTURES  # noqa: E402
from qtrade.strategies.cta import CTATrend  # noqa: E402
from qtrade.strategies.overlays import VolTarget  # noqa: E402

MARKET = "cnfutures_adj"
CN_FUT_RULES = CNFUTURES


def book():
    return VolTarget(CTATrend(h1=21, h2=63, h3=252), target_vol=0.30,
                     vol_window=63, bars_per_year=252)


def main():
    store = BarStore()
    print("=== stitching ===")
    for p in PRODUCTS:
        out, st = stitch(p)
        if out is None or len(out) < 500:
            print(f"{p}: insufficient data, skipped")
            continue
        store.save(normalize_ohlcv(out), MARKET, p, "1d")
        print(f"{p}: {st['contracts']} contracts, {len(out)} days "
              f"({out.index[0].date()} -> {out.index[-1].date()}), "
              f"{st['rolls']} rolls, mean |gap| {st['mean_gap_pct']:.2f}%")

    cov = store.coverage()
    syms = sorted(cov[cov["market"] == MARKET]["symbol"])
    bars = {s: store.load(MARKET, s, "1d") for s in syms}
    start = max(b.index[0] for b in bars.values())
    bars = {s: b[b.index >= start] for s, b in bars.items()}
    print(f"\npanel: {len(bars)} products from {start.date()}")

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
