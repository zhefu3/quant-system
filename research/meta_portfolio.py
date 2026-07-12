"""E51: the meta-portfolio — what the books look like COMBINED.

Institutions win by holding low-correlation return streams. We now have:
  A) crypto_core        (validated; hourly, 2021+ 10-symbol panel)
  B) futures trend      (ETF-proxy version, daily — clean-data stand-in
                         until IBKR; E41)
  C) A股防守 ETF 配置    (E42 variant B, daily)

This computes monthly-return correlations between the three books from
their own backtests, then a risk-budgeted combination (inverse-vol weights,
monthly) with combined equity/DD — the blueprint for allocating real money
across venues. Books use only their own history overlap (2021+).
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import vectorbt as vbt

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qtrade.backtest.portfolio import run_portfolio  # noqa: E402
from qtrade.data.schema import resample_ohlcv  # noqa: E402
from qtrade.data.store import BarStore  # noqa: E402
from qtrade.markets.rules import CRYPTO_PERP, US, MarketRules  # noqa: E402
from qtrade.presets import CRYPTO_CORE  # noqa: E402
from qtrade.strategies.cta import CTATrend  # noqa: E402
from qtrade.strategies.overlays import VolTarget  # noqa: E402

ETF_RULES = MarketRules(market="ashare_etf", fee_rate=0.0003, slippage=0.0005,
                        t_plus_one=True, tz="Asia/Shanghai")


def book_returns_crypto(store) -> pd.Series:
    p = CRYPTO_CORE
    bars = {s: store.load(p.market, s, p.timeframe) for s in p.symbols}
    start = max(b.index[0] for b in bars.values())
    bars = {s: b[b.index >= start] for s, b in bars.items()}
    _, d = run_portfolio(p.strategy(), bars, CRYPTO_PERP, "1h", allocation="equal",
                         rebalance_eps=p.rebalance_eps, return_details=True)
    W, closes = d["weights"], d["closes"]
    orders = W.where(W.ne(W.shift(1)), np.nan)
    orders.iloc[0] = W.iloc[0]
    pf = vbt.Portfolio.from_orders(closes, size=orders, size_type="targetpercent",
                                   direction="both", fees=CRYPTO_PERP.fee_rate,
                                   slippage=CRYPTO_PERP.slippage, init_cash=10_000,
                                   freq="1h", group_by=True, cash_sharing=True)
    return pf.value().resample("ME").last().pct_change().dropna().rename("crypto")


def _daily_book_returns(store, market, syms, strat, rules, name) -> pd.Series:
    bars = {s: store.load(market, s, "1d") for s in syms}
    start = max(b.index[0] for b in bars.values())
    bars = {s: b[b.index >= start] for s, b in bars.items()}
    _, d = run_portfolio(strat, bars, rules, "1d", allocation="equal",
                         rebalance_eps=0.02, align="ffill", return_details=True)
    W, closes = d["weights"], d["closes"]
    ret = (W.shift(1) * closes.pct_change()).sum(axis=1)  # pre-cost approx ok for corr
    return (1 + ret).resample("ME").prod().sub(1).rename(name)


def main():
    store = BarStore()

    crypto = book_returns_crypto(store)
    fut = _daily_book_returns(
        store, "etf", ["SPY", "QQQ", "TLT", "IEF", "GLD", "SLV", "USO", "UNG", "DBC", "FXE"],
        VolTarget(CTATrend(h1=21, h2=63, h3=252), target_vol=0.30, vol_window=63,
                  bars_per_year=252), US, "futures_proxy")
    defensive = _daily_book_returns(
        store, "ashare_index", ["SH_000300", "SH_000905", "SH_000012"],
        VolTarget(CTATrend(h1=21, h2=63, h3=252, long_only=True), target_vol=0.15,
                  vol_window=63, bars_per_year=252), ETF_RULES, "cn_defensive")

    R = pd.concat([crypto, fut, defensive], axis=1).dropna()
    print(f"overlap: {len(R)} months, {R.index[0].date()} -> {R.index[-1].date()}")
    print("\n=== 月收益相关矩阵 ===")
    print(R.corr().round(2).to_string())

    print("\n=== 单账本(月度年化) ===")
    for c in R:
        ann, vol = R[c].mean() * 12, R[c].std() * np.sqrt(12)
        print(f"  {c:14s}: ret {ann:+.1%}  vol {vol:.1%}  sharpe {ann / vol:.2f}")

    # 风险预算组合: 逆波动率权重, 月度再平衡
    w = (1.0 / R.rolling(6).std()).shift(1)
    w = w.div(w.sum(axis=1), axis=0).fillna(1 / 3)
    combo = (R * w).sum(axis=1)
    eq = (1 + combo).cumprod()
    dd = float((eq / eq.cummax() - 1).min())
    ann, vol = combo.mean() * 12, combo.std() * np.sqrt(12)
    print("\n=== 组合(逆波动率风险预算, 月调) ===")
    print(f"  ret {ann:+.1%}/yr  vol {vol:.1%}  sharpe {ann / vol:.2f}  maxDD {dd:.1%}")
    print(f"  平均权重: {dict(w.mean().round(2))}")


if __name__ == "__main__":
    main()
