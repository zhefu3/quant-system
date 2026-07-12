"""E55: universe breadth 14 -> 30 for cn_futures (prereg 2026-07-12).

Same frozen protocol as E50b (stitch rules / strategy / costs / equal alloc).
SA lists 2019-12, so the fair comparison window is the 30-product common
start; the gate is old-pool Sharpe ON THAT WINDOW + 0.1, maxDD <= old + 2pp.
Sanity gate: the 16 new products alone must carry positive Sharpe.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qtrade.backtest.portfolio import run_portfolio  # noqa: E402
from qtrade.data.cn_futures import PRODUCTS as OLD  # noqa: E402
from qtrade.data.cn_futures import stitch  # noqa: E402
from qtrade.data.schema import normalize_ohlcv  # noqa: E402
from qtrade.data.store import BarStore  # noqa: E402
from qtrade.markets.rules import CNFUTURES  # noqa: E402
from qtrade.strategies.cta import CTATrend  # noqa: E402
from qtrade.strategies.overlays import VolTarget  # noqa: E402

NEW = ["HC", "FG", "SA", "PP", "L", "V", "EG", "BU", "NI", "SN", "ZN", "PB",
       "C", "RM", "P", "JD"]
MARKET = "cnfutures_adj"


def book():
    return VolTarget(CTATrend(h1=21, h2=63, h3=252), target_vol=0.30,
                     vol_window=63, bars_per_year=252)


def run(bars, label):
    res = run_portfolio(book(), bars, CNFUTURES, "1d", allocation="equal",
                        rebalance_eps=0.02, align="ffill").loc["full"]
    print(f"{label:28s}: ret {res['return_pct']:+7.2f}%  sharpe {res['sharpe']:5.2f}  "
          f"maxDD {res['max_dd_pct']:5.1f}%  trades {int(res['trades'])}")
    return res


def main():
    store = BarStore()
    print("=== stitching new products ===")
    for p in NEW:
        out, st = stitch(p)
        if out is None or len(out) < 500:
            print(f"{p}: insufficient data ({0 if out is None else len(out)} days), skipped")
            continue
        store.save(normalize_ohlcv(out), MARKET, p, "1d")
        print(f"{p}: {st['contracts']} contracts, {len(out)} days "
              f"({out.index[0].date()} -> {out.index[-1].date()}), "
              f"{st['rolls']} rolls, mean |gap| {st['mean_gap_pct']:.2f}%")

    all_bars = {}
    for p in OLD + NEW:
        try:
            all_bars[p] = store.load(MARKET, p, "1d")
        except Exception:  # noqa: BLE001 — skipped products stay out
            pass
    usable_new = [p for p in NEW if p in all_bars]
    start = max(b.index[0] for b in all_bars.values())
    bars = {s: b[b.index >= start] for s, b in all_bars.items()}
    print(f"\ncommon window from {start.date()} ({len(bars)} products, "
          f"{len(usable_new)} new)\n")

    old_res = run({s: bars[s] for s in OLD}, "old 14-pool (same window)")
    new_res = run({s: bars[s] for s in usable_new}, "new products only (sanity)")
    exp_res = run(bars, f"expanded {len(bars)}-pool")

    gate_sharpe = old_res["sharpe"] + 0.1
    gate_dd = old_res["max_dd_pct"] - 2.0  # max_dd_pct is negative
    print(f"\ngates: sharpe >= {gate_sharpe:.2f} and maxDD >= {gate_dd:.1f}% "
          f"and new-only sharpe > 0")
    ok = (exp_res["sharpe"] >= gate_sharpe and exp_res["max_dd_pct"] >= gate_dd
          and new_res["sharpe"] > 0)
    print("VERDICT: ADOPT expansion" if ok else "VERDICT: keep 14-pool")


if __name__ == "__main__":
    main()
