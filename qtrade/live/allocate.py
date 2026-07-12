"""allocate: turn the meta-portfolio blueprint (E51/E51b) into numbers you can act on.

Given total capital, computes inverse-volatility weights across the DEPLOYED
books (presets that exist and have validated backtests), from each book's
realized daily returns over the trailing year. Prints per-book capital, the
combined expected vol, and the rebalance cadence. Books without presets
(e.g. the E54-degraded defensive sleeve) are deliberately absent.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..backtest.portfolio import run_portfolio
from ..data.store import BarStore
from ..presets import PRESETS

# one representative preset per capital sleeve (v2/4h are execution variants
# of the same crypto book, not separate capital buckets)
SLEEVES = {"crypto_core": "crypto", "cn_futures": "cnfutures_adj"}
LOOKBACK_DAYS = 365


def book_daily_returns(preset_name: str, store_market: str) -> pd.Series:
    p = PRESETS[preset_name]
    store = BarStore()
    bars = {}
    for s in p.symbols:
        df = store.load(store_market, s, p.timeframe)
        bars[s] = df[df.index >= df.index[-1] - pd.Timedelta(days=LOOKBACK_DAYS + 180)]
    start = max(b.index[0] for b in bars.values())
    bars = {s: b[b.index >= start] for s, b in bars.items()}
    _, d = run_portfolio(p.strategy(), bars, p.rules, p.timeframe, allocation="equal",
                         rebalance_eps=p.rebalance_eps, align="ffill", return_details=True)
    ret = (d["weights"].shift(1) * d["closes"].pct_change()).sum(axis=1)
    daily = (1 + ret).resample("1D").prod().sub(1)
    daily = daily[daily.index >= daily.index[-1] - pd.Timedelta(days=LOOKBACK_DAYS)]
    return daily.rename(preset_name)


def run_allocate(capital: float):
    rets = {}
    for name, market in SLEEVES.items():
        try:
            rets[name] = book_daily_returns(name, market)
        except Exception as e:  # noqa: BLE001 — a missing book is reported, not fatal
            print(f"({name}: returns unavailable — {str(e)[:60]})")
    if len(rets) < 2:
        print("need at least two books with data; aborting")
        return

    R = pd.concat(rets.values(), axis=1)
    vols = R.std() * np.sqrt(365)
    w = (1.0 / vols) / (1.0 / vols).sum()
    corr = R.corr()
    combo_var = float(w.values @ (corr * np.outer(vols, vols)).values @ w.values)

    print(f"══════ 资金分配 · 逆波动率 · 总额 {capital:,.0f} ══════")
    print(f"(账本日收益回看 {LOOKBACK_DAYS} 天, 月度再平衡)\n")
    for name in R.columns:
        print(f"  {name:14s}: {w[name]:5.1%}  = {capital * w[name]:>12,.0f}"
              f"   (账本年化vol {vols[name]:.1%})")
    print(f"\n  组合预期年化 vol: {np.sqrt(combo_var):.1%}")
    print(f"  账本相关性: {corr.iloc[0, 1]:+.2f}")
    print("\n  纪律: 每月第一个交易日重算; 任一账本 HALTED 时其份额转现金,")
    print("  不得手动加回直至人工复核解除。E54 后无防守账本 — 未分配部分即现金。")


if __name__ == "__main__":
    run_allocate(10_000)
