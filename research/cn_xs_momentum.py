"""E53: commodity cross-sectional momentum (prereg 2026-07-12).

Signal: trailing 126-trading-day return on the E50b stitched series, ranked
cross-sectionally. Portfolio/costs/eval identical to E52 (cn_carry.py).
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from cn_carry import COST, MIN_VALID, N_LEG  # noqa: E402  — frozen E52 machinery
from qtrade.backtest.portfolio import run_portfolio  # noqa: E402
from qtrade.data.cn_futures import PRODUCTS, stitch  # noqa: E402
from qtrade.markets.rules import CNFUTURES  # noqa: E402
from qtrade.strategies.cta import CTATrend  # noqa: E402
from qtrade.strategies.overlays import VolTarget  # noqa: E402

LOOKBACK = 126


def main():
    rets, moms = {}, {}
    for p in PRODUCTS:
        bars, _ = stitch(p)
        if bars is None or len(bars) < 500:
            continue
        idx = bars.index.tz_convert("Asia/Shanghai").tz_localize(None).normalize()
        close = bars["close"]
        close.index = idx
        rets[p] = close.pct_change()
        moms[p] = close.pct_change(LOOKBACK)
    R = pd.DataFrame(rets).sort_index()
    C = pd.DataFrame(moms).reindex(R.index)
    start = pd.Timestamp("2018-06-19")
    R, C = R[R.index >= start], C[C.index >= start]
    print(f"panel {R.shape}, {R.index[0].date()} -> {R.index[-1].date()}")

    W = pd.DataFrame(0.0, index=R.index, columns=R.columns)
    w_cur = pd.Series(0.0, index=R.columns)
    for i, dt in enumerate(R.index):
        if dt.dayofweek == 4 or i == 0:
            sig = C.loc[:dt].iloc[-2] if len(C.loc[:dt]) > 1 else C.loc[dt]  # yesterday's info
            valid = sig.dropna()
            if len(valid) >= MIN_VALID:
                ranked = valid.sort_values()
                w_new = pd.Series(0.0, index=R.columns)
                w_new[ranked.index[-N_LEG:]] = 1.0 / (2 * N_LEG)
                w_new[ranked.index[:N_LEG]] = -1.0 / (2 * N_LEG)
                w_cur = w_new
        W.loc[dt] = w_cur

    held = W.shift(1).fillna(0.0)
    net_ret = (held * R.fillna(0.0)).sum(axis=1) - (W - W.shift(1)).abs().sum(axis=1).fillna(0.0) * COST
    equity = (1 + net_ret).cumprod()

    def sharpe(x):
        return float(x.mean() / x.std() * np.sqrt(252)) if x.std() > 0 else 0.0

    dd = float((equity / equity.cummax() - 1).min())
    half = len(net_ret) // 2
    print(f"\nxs-momentum standalone: total {equity.iloc[-1] - 1:+.1%}, Sharpe {sharpe(net_ret):.2f}, maxDD {dd:.1%}")
    print(f"halves: {sharpe(net_ret[:half]):.2f} / {sharpe(net_ret[half:]):.2f}")
    for year, g in net_ret.groupby(net_ret.index.year):
        print(f"  {year}: ret {(1 + g).prod() - 1:+7.2%}  sharpe {sharpe(g):5.2f}")

    from qtrade.data.store import BarStore
    store = BarStore()
    bars = {p: store.load("cnfutures_adj", p, "1d") for p in R.columns}
    common = max(b.index[0] for b in bars.values())
    bars = {s: b[b.index >= common] for s, b in bars.items()}
    strat = VolTarget(CTATrend(h1=21, h2=63, h3=252), target_vol=0.30,
                      vol_window=63, bars_per_year=252)
    _, det = run_portfolio(strat, bars, CNFUTURES, "1d", allocation="equal",
                           rebalance_eps=0.02, align="ffill", return_details=True)
    trend_ret = (det["weights"].shift(1) * det["closes"].pct_change()).sum(axis=1)
    trend_ret.index = trend_ret.index.tz_convert("Asia/Shanghai").tz_localize(None).normalize()

    both = pd.DataFrame({"trend": trend_ret, "xsmom": net_ret}).dropna()
    combo = 0.5 * both["trend"] + 0.5 * both["xsmom"]
    print(f"\ncorr(trend, xsmom) = {both['trend'].corr(both['xsmom']):.3f}  ({len(both)} days)")
    print(f"trend-only Sharpe {sharpe(both['trend']):.2f} | xsmom {sharpe(both['xsmom']):.2f} "
          f"| 50/50 combo {sharpe(combo):.2f}")


if __name__ == "__main__":
    main()
