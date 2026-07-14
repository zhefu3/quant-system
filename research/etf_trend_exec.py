"""E62: tradability test for E41's ETF trend book (prereg 2026-07-14).

Construction is E40/E41 verbatim; only execution realism is added:
commission 0.01%/side, slippage 0.03%/side, short borrow 1.0%/yr accrued
daily on short notional. Two frozen variants: V1 long/short, V2 long-flat.

Frozen verdict: candidate = V1 if V1 net Sharpe > V2 else V2; candidate
passes iff net Sharpe >= 0.25 AND net returns in 2008, 2020, 2022 are all
positive. Pass -> etf_trend observation book. Fail -> archived.
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

UNIVERSE = ["SPY", "QQQ", "TLT", "IEF", "GLD", "SLV", "USO", "UNG", "DBC", "FXE"]
FEE = 0.0001      # frozen: conservative IBKR commission per side
SLIP = 0.0003     # frozen: conservative spread cost per side
BORROW = 0.010    # frozen: 1.0%/yr on short notional, daily accrual
RULES = MarketRules(market="us_etf", fee_rate=FEE, slippage=SLIP, allow_short=True)

CRISIS_YEARS = (2008, 2020, 2022)
SHARPE_GATE = 0.25


def book():
    return VolTarget(CTATrend(h1=21, h2=63, h3=252), target_vol=0.30,
                     vol_window=63, bars_per_year=252)


def net_returns(W: pd.DataFrame, closes: pd.DataFrame) -> pd.Series:
    rets = closes.pct_change()
    gross = (W.shift(1) * rets).sum(axis=1)
    turnover = (W - W.shift(1)).abs().sum(axis=1).fillna(0.0)
    borrow = W.shift(1).clip(upper=0).abs().sum(axis=1) * (BORROW / 252)
    return (gross - turnover * (FEE + SLIP) - borrow).dropna()


def stats(r: pd.Series, label: str) -> dict:
    sharpe = float(r.mean() / r.std() * np.sqrt(252)) if r.std() > 0 else 0.0
    eq = (1 + r).cumprod()
    dd = float((eq / eq.cummax() - 1).min())
    out = {"variant": label, "sharpe": round(sharpe, 3),
           "ann_ret_pct": round(((eq.iloc[-1]) ** (252 / len(r)) - 1) * 100, 2),
           "max_dd_pct": round(dd * 100, 1)}
    for y in CRISIS_YEARS:
        yr = r[(r.index >= f"{y}-01-01") & (r.index < f"{y + 1}-01-01")]
        out[f"y{y}_pct"] = round(((1 + yr).prod() - 1) * 100, 2)
    return out


def main():
    store = BarStore()
    bars = {s: store.load("etf", s, "1d") for s in UNIVERSE}
    start = min(b.index[0] for b in bars.values())
    print(f"universe {len(bars)} ETFs, window {start.date()} -> "
          f"{max(b.index[-1] for b in bars.values()).date()}")

    _, d = run_portfolio(book(), bars, RULES, "1d", allocation="equal",
                         rebalance_eps=0.02, align="ffill", return_details=True)
    W, closes = d["weights"], d["closes"]

    r1 = net_returns(W, closes)                 # V1: long/short as validated
    r2 = net_returns(W.clip(lower=0), closes)   # V2: long-flat

    s1, s2 = stats(r1, "V1 long/short"), stats(r2, "V2 long-flat")
    print(pd.DataFrame([s1, s2]).set_index("variant").to_string())

    cand, sc = (s1, r1) if s1["sharpe"] > s2["sharpe"] else (s2, r2)
    crisis_ok = all(cand[f"y{y}_pct"] > 0 for y in CRISIS_YEARS)
    passed = cand["sharpe"] >= SHARPE_GATE and crisis_ok
    print(f"\n候选: {cand['variant']} | 净Sharpe {cand['sharpe']} "
          f"(门槛 {SHARPE_GATE}) | 危机年皆正: {crisis_ok}")
    print(f"判决: {'✅ 过 -> etf_trend 观察账本' if passed else '❌ 不过 -> 入档'}")

    print("\n逐年(候选):")
    yearly = sc.groupby(sc.index.year).apply(lambda x: ((1 + x).prod() - 1) * 100)
    print({int(y): round(v, 1) for y, v in yearly.items()})


if __name__ == "__main__":
    main()
