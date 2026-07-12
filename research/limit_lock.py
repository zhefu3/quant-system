"""E58: limit-lock execution robustness for cn_futures (prereg 2026-07-12).

Domestic commodities halt at daily price limits. A one-price day
(high == low on the day's main contract) means no realistic fill: the replay
freezes weights through locked days and catches up on the next tradable day.
Signals, costs and panel are untouched — only executability changes.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qtrade.backtest.portfolio import run_portfolio  # noqa: E402
from qtrade.data.cn_futures import PRODUCTS, load_product, pick_main  # noqa: E402
from qtrade.data.store import BarStore  # noqa: E402
from qtrade.markets.rules import CNFUTURES  # noqa: E402
from qtrade.strategies.cta import CTATrend  # noqa: E402
from qtrade.strategies.overlays import VolTarget  # noqa: E402

COST = CNFUTURES.fee_rate + CNFUTURES.slippage


def locked_days(product: str) -> pd.Series:
    """True on days the MAIN contract printed a one-price session."""
    contracts = load_product(product)
    sched = pick_main(contracts)["contract"]
    out = {}
    for dt, c in sched.items():
        row = contracts[c].loc[dt]
        out[dt] = bool(row["high"] == row["low"])
    s = pd.Series(out).sort_index()
    s.index = (s.index + pd.Timedelta(hours=15)).tz_localize("Asia/Shanghai").tz_convert("UTC")
    return s


def sharpe(x: pd.Series) -> float:
    return float(x.mean() / x.std() * np.sqrt(252)) if x.std() > 0 else 0.0


def main():
    store = BarStore()
    bars = {p: store.load("cnfutures_adj", p, "1d") for p in PRODUCTS}
    start = max(b.index[0] for b in bars.values())
    bars = {s: b[b.index >= start] for s, b in bars.items()}
    strat = VolTarget(CTATrend(h1=21, h2=63, h3=252), target_vol=0.30,
                      vol_window=63, bars_per_year=252)
    _, d = run_portfolio(strat, bars, CNFUTURES, "1d", allocation="equal",
                         rebalance_eps=0.02, align="ffill", return_details=True)
    W, closes = d["weights"], d["closes"]
    rets = closes.pct_change()

    locks = pd.DataFrame({p: locked_days(p) for p in PRODUCTS}).reindex(W.index).fillna(False)
    lock_rate = locks.mean()
    print("one-price (locked) day share per product:")
    print((lock_rate * 100).round(2).sort_values(ascending=False).to_string())

    # replay: weights freeze through locked days, catch up on next tradable day
    W_eff = W.copy()
    for p in W.columns:
        lp = locks[p].values
        col = W[p].values.copy()
        for i in range(1, len(col)):
            if lp[i]:
                col[i] = col[i - 1]  # cannot trade into or out today
        W_eff[p] = col

    def book_ret(weights):
        gross = (weights.shift(1) * rets).sum(axis=1)
        costs = (weights - weights.shift(1)).abs().sum(axis=1).fillna(0.0) * COST
        return (gross - costs).dropna()

    base, adj = book_ret(W), book_ret(W_eff)
    both = pd.DataFrame({"base": base, "locked": adj}).dropna()
    delayed = int((W_eff != W).sum().sum())
    print(f"\ndelayed rebalance-cells: {delayed} "
          f"({delayed / W.size:.2%} of weight matrix)")
    print(f"baseline  : sharpe {sharpe(both['base']):.2f}, total {(1 + both['base']).prod() - 1:+.1%}")
    print(f"lock-aware: sharpe {sharpe(both['locked']):.2f}, total {(1 + both['locked']).prod() - 1:+.1%}")
    drop = sharpe(both["base"]) - sharpe(both["locked"])
    print(f"\nSharpe drop {drop:+.3f} (gate <= 0.10 -> {'ROBUST' if drop <= 0.10 else 'EXECUTION RISK'})")


if __name__ == "__main__":
    main()
