"""E45: HS300 index enhancement with A-share price factors on the PIT universe.

PRE-REGISTERED gates (before running, 2026-07-10): promote to paper-candidate
only if post-cost excess > 3%/yr AND IR > 0.5 AND both halves of the sample
show positive excess. Factors (E44a, price-only until fundamentals land):

  reversal_1m   : -(1-month return)          — A股短期反转
  low_vol_3m    : -(3-month daily vol)       — 低波异象
  anti_max      : -(max daily ret, 1m)       — 博彩偏好(MAX effect)

Composite = mean of per-date cross-sectional z-scores. Equal-weight top-50.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qtrade.backtest.relative import backtest_topk  # noqa: E402
from qtrade.data.store import BarStore  # noqa: E402

PIT = Path(__file__).resolve().parents[1] / "data_store" / "pit"


def zscore(s: pd.Series) -> pd.Series:
    return (s - s.mean()) / (s.std() or 1.0)


def score_composite(hist: pd.DataFrame) -> pd.Series:
    rets = hist.pct_change()
    rev = -hist.iloc[-1].div(hist.iloc[-21]).sub(1.0)          # 1m reversal
    lv = -rets.tail(63).std()                                   # low vol
    amax = -rets.tail(21).max()                                 # anti-lottery
    return zscore(rev) + zscore(lv) + zscore(amax)


def single(name):
    fns = {
        "reversal_1m": lambda h: zscore(-h.iloc[-1].div(h.iloc[-21]).sub(1.0)),
        "low_vol_3m": lambda h: zscore(-h.pct_change().tail(63).std()),
        "anti_max": lambda h: zscore(-h.pct_change().tail(21).max()),
    }
    return fns[name]


def main():
    store = BarStore()
    cov = store.coverage()
    pit_syms = sorted(cov[cov["market"] == "ashare_pit"]["symbol"])
    print(f"PIT universe on disk: {len(pit_syms)} names")
    closes = pd.DataFrame({s: store.load("ashare_pit", s, "1d")["close"] for s in pit_syms})
    membership = pd.read_parquet(PIT / "hs300_membership.parquet")
    bench = store.load("ashare_index", "SH_000300", "1d")["close"]

    for label, fn in [("composite(3因子)", score_composite),
                      ("reversal_1m", single("reversal_1m")),
                      ("low_vol_3m", single("low_vol_3m")),
                      ("anti_max", single("anti_max"))]:
        r = backtest_topk(closes, membership, fn, bench, k=50)
        curve = r.pop("excess_curve")
        half = len(curve) // 2
        h1 = float(curve.iloc[half] / curve.iloc[0] - 1) * 100
        h2 = float(curve.iloc[-1] / curve.iloc[half] - 1) * 100
        print(f"\n=== {label} ===")
        print({k: v for k, v in r.items()})
        print(f"前半段超额 {h1:+.1f}% / 后半段超额 {h2:+.1f}%")


if __name__ == "__main__":
    main()
