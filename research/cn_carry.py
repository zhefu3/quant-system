"""E52: domestic commodity term-structure carry, cross-sectional (prereg 2026-07-12).

Signal: annualized log slope between the main contract and the most liquid
later-expiry contract (yesterday's OI, >=10% of main's), smoothed 5 days.
Portfolio: Friday rebalance, long top 4 / short bottom 4, 1/8 notional each.
Returns come from the E50b back-adjusted continuous series; costs |dw| * 0.06%.

Verdict gates are frozen in research/log.md — this script only reports.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qtrade.backtest.portfolio import run_portfolio  # noqa: E402
from qtrade.data.cn_futures import PRODUCTS, expiry_key, load_product, pick_main, stitch  # noqa: E402
from qtrade.markets.rules import CNFUTURES  # noqa: E402
from qtrade.strategies.cta import CTATrend  # noqa: E402
from qtrade.strategies.overlays import VolTarget  # noqa: E402

COST = CNFUTURES.fee_rate + CNFUTURES.slippage  # per side, on turnover
N_LEG = 4
MIN_VALID = 10
SMOOTH = 5


def product_carry(product: str) -> pd.Series:
    """Daily annualized carry (positive = backwardation) for one product."""
    contracts = load_product(product)
    if not contracts:
        return pd.Series(dtype=float)
    sched = pick_main(contracts)["contract"]
    close = pd.DataFrame({c: df["close"] for c, df in contracts.items()}).sort_index()
    oi = pd.DataFrame({c: df["hold"] for c, df in contracts.items()}).sort_index()
    prev_oi = oi.shift(1)
    prev_oi.iloc[0] = oi.iloc[0]

    out = {}
    for dt, main in sched.items():
        row_px, row_oi = close.loc[dt], prev_oi.loc[dt]
        main_oi = row_oi[main] if pd.notna(row_oi[main]) else 0.0
        best_c, best_oi = None, -1.0
        for c in close.columns:
            if expiry_key(c) <= expiry_key(main) or pd.isna(row_px[c]) or pd.isna(row_oi[c]):
                continue
            if row_oi[c] >= 0.10 * main_oi and row_oi[c] > best_oi:
                best_c, best_oi = c, row_oi[c]
        if best_c is None or pd.isna(row_px[main]) or row_px[main] <= 0 or row_px[best_c] <= 0:
            continue
        d_months = ((expiry_key(best_c) // 100 - expiry_key(main) // 100) * 12
                    + expiry_key(best_c) % 100 - expiry_key(main) % 100)
        if d_months <= 0:
            continue
        out[dt] = float(np.log(row_px[main] / row_px[best_c]) / d_months * 12)
    return pd.Series(out).sort_index()


def main():
    # -- clean continuous returns + carry signals -----------------------------
    rets, carries = {}, {}
    for p in PRODUCTS:
        bars, _ = stitch(p)
        if bars is None or len(bars) < 500:
            continue
        idx_naive = bars.index.tz_convert("Asia/Shanghai").tz_localize(None).normalize()
        r = bars["close"].pct_change()
        r.index = idx_naive
        rets[p] = r
        c = product_carry(p).rolling(SMOOTH).mean()
        carries[p] = c
    R = pd.DataFrame(rets).sort_index()
    C = pd.DataFrame(carries).reindex(R.index)
    start = pd.Timestamp("2018-06-19")
    R, C = R[R.index >= start], C[C.index >= start]
    print(f"panel {R.shape}, {R.index[0].date()} -> {R.index[-1].date()}")

    # -- weekly cross-sectional weights (decide on yesterday's info) ----------
    W = pd.DataFrame(0.0, index=R.index, columns=R.columns)
    w_cur = pd.Series(0.0, index=R.columns)
    for i, dt in enumerate(R.index):
        if dt.dayofweek == 4 or i == 0:  # Friday close (or panel start)
            sig = C.loc[:dt].iloc[-2] if len(C.loc[:dt]) > 1 else C.loc[dt]  # yesterday's smoothed carry
            valid = sig.dropna()
            if len(valid) >= MIN_VALID:
                ranked = valid.sort_values()
                w_new = pd.Series(0.0, index=R.columns)
                w_new[ranked.index[-N_LEG:]] = 1.0 / (2 * N_LEG)
                w_new[ranked.index[:N_LEG]] = -1.0 / (2 * N_LEG)
                w_cur = w_new
        W.loc[dt] = w_cur

    held = W.shift(1).fillna(0.0)
    gross_ret = (held * R.fillna(0.0)).sum(axis=1)
    turnover = (W - W.shift(1)).abs().sum(axis=1).fillna(0.0)
    net_ret = gross_ret - turnover * COST
    equity = (1 + net_ret).cumprod()

    def sharpe(x):
        return float(x.mean() / x.std() * np.sqrt(252)) if x.std() > 0 else 0.0

    dd = float((equity / equity.cummax() - 1).min())
    half = len(net_ret) // 2
    print(f"\ncarry standalone: total {equity.iloc[-1] - 1:+.1%}, Sharpe {sharpe(net_ret):.2f}, "
          f"maxDD {dd:.1%}, ann turnover {turnover.mean() * 252:.1f}x")
    print(f"halves: {sharpe(net_ret[:half]):.2f} / {sharpe(net_ret[half:]):.2f}")
    for year, gsub in net_ret.groupby(net_ret.index.year):
        print(f"  {year}: ret {(1 + gsub).prod() - 1:+7.2%}  sharpe {sharpe(gsub):5.2f}")

    # -- correlation with the E50b trend book + 50/50 combo -------------------
    from qtrade.data.store import BarStore
    store = BarStore()
    bars = {p: store.load("cnfutures_adj", p, "1d") for p in R.columns}
    common = max(b.index[0] for b in bars.values())
    bars = {s: b[b.index >= common] for s, b in bars.items()}
    strat = VolTarget(CTATrend(h1=21, h2=63, h3=252), target_vol=0.30,
                      vol_window=63, bars_per_year=252)
    _, det = run_portfolio(strat, bars, CNFUTURES, "1d", allocation="equal",
                           rebalance_eps=0.02, align="ffill", return_details=True)
    wts, closes = det["weights"], det["closes"]
    trend_ret = (wts.shift(1) * closes.pct_change()).sum(axis=1)
    trend_ret.index = trend_ret.index.tz_convert("Asia/Shanghai").tz_localize(None).normalize()

    both = pd.DataFrame({"trend": trend_ret, "carry": net_ret}).dropna()
    corr = both["trend"].corr(both["carry"])
    combo = 0.5 * both["trend"] + 0.5 * both["carry"]
    print(f"\ncorr(trend, carry) = {corr:.3f}  ({len(both)} days)")
    print(f"trend-only Sharpe {sharpe(both['trend']):.2f} | carry {sharpe(both['carry']):.2f} "
          f"| 50/50 combo {sharpe(combo):.2f}")


if __name__ == "__main__":
    main()
