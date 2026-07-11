"""Benchmark-relative backtester for index-enhancement portfolios.

Different machine from the absolute-return engine: monthly rebalance into a
top-K selection from a POINT-IN-TIME universe, long-only, measured against
the index in excess-return terms (IR, tracking error, relative drawdown).

Honesty rails:
  - universe at each rebalance = index members ON THAT DATE (membership df)
  - scores computed from data strictly before the rebalance date
  - costs: per-side fee/slippage + sell-side stamp duty, charged on turnover
  - T+1 is satisfied by monthly holding periods
"""

from __future__ import annotations

import numpy as np
import pandas as pd

STAMP_SELL = 0.0005  # 印花税(卖出), 2023 调降后
FEE = 0.00025        # 佣金单边
SLIP = 0.001         # 冲击/滑点单边(成分股, 保守)


def backtest_topk(
    closes: pd.DataFrame,          # daily closes, columns = all union symbols
    membership: pd.DataFrame,      # long df: snap(date str), code(sh.600000)
    score_fn,                      # (closes_up_to_t: DataFrame) -> Series[symbol scores]
    benchmark: pd.Series,          # index daily closes
    k: int = 50,
    min_history: int = 126,
) -> dict:
    """Monthly top-K selection; returns metrics + daily excess curve."""
    def to_sym(code: str) -> str:
        ex, digits = code.split(".")
        return f"{digits}.{ex.upper()}"

    member_map: dict[str, set] = {}
    for snap, grp in membership.groupby("snap"):
        member_map[snap] = {to_sym(c) for c in grp["code"]}
    snap_dates = sorted(member_map)

    rebal_dates = closes.resample("ME").last().index
    rebal_dates = [d for d in rebal_dates if d >= closes.index[0] + pd.Timedelta(days=min_history)]

    rets = closes.pct_change()
    port_ret = pd.Series(0.0, index=closes.index)
    weights: pd.Series | None = None
    turnover_cost = pd.Series(0.0, index=closes.index)
    holdings_log = []

    for i, d in enumerate(rebal_dates[:-1]):
        # PIT universe: latest snapshot at or before d
        snap = max((s for s in snap_dates if pd.Timestamp(s, tz="UTC") <= d), default=None)
        if snap is None:
            continue
        eligible = [s for s in member_map[snap] if s in closes.columns]
        hist = closes.loc[:d, eligible].dropna(axis=1, thresh=min_history)
        if hist.shape[1] < k * 2:
            continue
        scores = score_fn(hist).dropna()
        picks = scores.nlargest(k).index
        new_w = pd.Series(1.0 / len(picks), index=picks)

        prev = weights if weights is not None else pd.Series(dtype=float)
        union = new_w.index.union(prev.index)
        turn = (new_w.reindex(union, fill_value=0.0) - prev.reindex(union, fill_value=0.0))
        buys, sells = turn[turn > 0].sum(), -turn[turn < 0].sum()
        cost = buys * (FEE + SLIP) + sells * (FEE + SLIP + STAMP_SELL)

        nxt = rebal_dates[i + 1]
        window = rets.loc[d:nxt].iloc[1:]  # returns AFTER the rebalance date
        seg = window[picks].mean(axis=1)   # equal weight, drift approximated monthly
        port_ret.loc[seg.index] = seg.fillna(0.0)
        first_day = seg.index[0] if len(seg) else None
        if first_day is not None:
            turnover_cost.loc[first_day] += cost
        weights = new_w
        holdings_log.append({"date": str(d.date()), "n": len(picks),
                             "turnover": round(float(buys + sells), 3)})

    port_ret = port_ret - turnover_cost
    bench_ret = benchmark.pct_change().reindex(port_ret.index).fillna(0.0)
    start = rebal_dates[0]
    port_ret, bench_ret = port_ret.loc[start:], bench_ret.loc[start:]

    excess = port_ret - bench_ret
    ann = 252
    ex_ann = float(excess.mean() * ann)
    te = float(excess.std() * np.sqrt(ann))
    ir = ex_ann / te if te > 0 else np.nan
    cum_ex = (1 + excess).cumprod()
    rel_dd = float((cum_ex / cum_ex.cummax() - 1).min())
    avg_turnover = float(np.mean([h["turnover"] for h in holdings_log])) if holdings_log else np.nan

    return {
        "excess_ann_pct": round(ex_ann * 100, 2),
        "tracking_error_pct": round(te * 100, 2),
        "IR": round(ir, 2),
        "rel_maxdd_pct": round(rel_dd * 100, 2),
        "port_ann_pct": round(float(port_ret.mean() * ann) * 100, 2),
        "bench_ann_pct": round(float(bench_ret.mean() * ann) * 100, 2),
        "avg_monthly_turnover": round(avg_turnover, 3),
        "n_rebalances": len(holdings_log),
        "excess_curve": cum_ex,
    }
