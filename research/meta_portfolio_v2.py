"""E51b: meta-portfolio refresh with the REAL domestic CTA book (E50b).

E51 used a US-ETF proxy as the only futures leg. cn_futures is now a real,
deployable book on clean stitched data — so the deployable-today set is
(crypto, cn_cta, cn_defensive), with the US futures proxy kept as the
future IBKR leg. Reuses E51's book-return builders unchanged.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from meta_portfolio import ETF_RULES, _daily_book_returns, book_returns_crypto  # noqa: E402
from qtrade.data.cn_futures import PRODUCTS  # noqa: E402
from qtrade.data.store import BarStore  # noqa: E402
from qtrade.markets.rules import CNFUTURES, US  # noqa: E402
from qtrade.strategies.cta import CTATrend  # noqa: E402
from qtrade.strategies.overlays import VolTarget  # noqa: E402


def cta_book():
    return VolTarget(CTATrend(h1=21, h2=63, h3=252), target_vol=0.30,
                     vol_window=63, bars_per_year=252)


def combo_stats(R: pd.DataFrame, label: str):
    w = (1.0 / R.rolling(6).std()).shift(1)
    w = w.div(w.sum(axis=1), axis=0).fillna(1 / len(R.columns))
    combo = (R * w).sum(axis=1)
    eq = (1 + combo).cumprod()
    dd = float((eq / eq.cummax() - 1).min())
    ann, vol = combo.mean() * 12, combo.std() * np.sqrt(12)
    print(f"\n=== {label} (逆波动率, 月调) ===")
    print(f"  ret {ann:+.1%}/yr  vol {vol:.1%}  sharpe {ann / vol:.2f}  maxDD {dd:.1%}")
    print(f"  平均权重: {dict(w.mean().round(2))}")


def main():
    store = BarStore()
    crypto = book_returns_crypto(store)
    cn_cta = _daily_book_returns(store, "cnfutures_adj", PRODUCTS, cta_book(),
                                 CNFUTURES, "cn_cta")
    fut_proxy = _daily_book_returns(
        store, "etf", ["SPY", "QQQ", "TLT", "IEF", "GLD", "SLV", "USO", "UNG", "DBC", "FXE"],
        cta_book(), US, "futures_proxy")
    defensive = _daily_book_returns(
        store, "ashare_index", ["SH_000300", "SH_000905", "SH_000012"],
        VolTarget(CTATrend(h1=21, h2=63, h3=252, long_only=True), target_vol=0.15,
                  vol_window=63, bars_per_year=252), ETF_RULES, "cn_defensive")

    R4 = pd.concat([crypto, cn_cta, fut_proxy, defensive], axis=1).dropna()
    print(f"overlap: {len(R4)} months, {R4.index[0].date()} -> {R4.index[-1].date()}")
    print("\n=== 月收益相关矩阵(4账本) ===")
    print(R4.corr().round(2).to_string())

    print("\n=== 单账本(月度年化) ===")
    for c in R4:
        ann, vol = R4[c].mean() * 12, R4[c].std() * np.sqrt(12)
        print(f"  {c:14s}: ret {ann:+.1%}  vol {vol:.1%}  sharpe {ann / vol:.2f}")

    combo_stats(R4[["crypto", "cn_cta", "cn_defensive"]], "今日可部署3账本")
    combo_stats(R4, "未来4账本(含IBKR代理)")


if __name__ == "__main__":
    main()
