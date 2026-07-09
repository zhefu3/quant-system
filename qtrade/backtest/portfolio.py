"""Multi-asset portfolio backtest: one strategy signal per symbol, shared cash.

Allocation styles:
  - "equal":   1/N of equity per symbol
  - "inv_vol": risk parity lite — allocation proportional to 1/realized_vol,
               normalized to sum to 1. Uses only trailing data (no lookahead).

Diversification is the one free lunch: crypto symbols are highly correlated,
so expect less benefit than symbol count suggests — measure it, don't assume.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import vectorbt as vbt

from ..markets.rules import MarketRules
from ..strategies.base import Strategy
from .engine import Engine


def _allocations(
    closes: pd.DataFrame, style: str, vol_window: int = 168
) -> pd.DataFrame:
    if style == "equal":
        return pd.DataFrame(1.0 / closes.shape[1], index=closes.index, columns=closes.columns)
    if style == "inv_vol":
        vol = closes.pct_change().rolling(vol_window).std()
        inv = 1.0 / vol
        alloc = inv.div(inv.sum(axis=1), axis=0)
        # Warm-up (all-NaN rows): fall back to equal weight.
        return alloc.fillna(1.0 / closes.shape[1])
    raise ValueError(f"unknown allocation style {style!r}")


def run_portfolio(
    strategy: Strategy,
    bars_by_symbol: dict[str, pd.DataFrame],
    rules: MarketRules,
    timeframe: str,
    allocation: str = "inv_vol",
    vol_window: int = 168,
    oos_fraction: float = 0.3,
    init_cash: float = 10_000.0,
    rebalance_eps: float = 0.02,
) -> pd.DataFrame:
    """Backtest `strategy` applied to every symbol under a shared cash pool.

    Returns a summary frame (full / in_sample / out_of_sample rows) comparing
    the portfolio against an equal-weight buy&hold of the same symbols.
    """
    engine = Engine(rules, init_cash=init_cash, rebalance_eps=rebalance_eps)

    # Common timeline: inner join so every symbol has a bar at every ts.
    common = None
    for df in bars_by_symbol.values():
        common = df.index if common is None else common.intersection(df.index)
    closes = pd.DataFrame(
        {sym: df.loc[common, "close"] for sym, df in bars_by_symbol.items()}
    )

    orders, effective = {}, {}
    if hasattr(strategy, "target_weights"):
        # Portfolio-level strategy: emits the whole weight matrix itself
        # (its gross budget replaces the per-symbol allocation step).
        weights = strategy.target_weights(closes)
        for sym in closes.columns:
            orders[sym], effective[sym] = engine.process_weights(weights[sym], common)
    else:
        alloc = _allocations(closes, allocation, vol_window)
        for sym, df in bars_by_symbol.items():
            raw = strategy.target_position(df.loc[common]).reindex(common).fillna(0.0)
            scaled = (raw * alloc[sym]).clip(-1.0, 1.0)
            orders[sym], effective[sym] = engine.process_weights(scaled, common)
    orders = pd.DataFrame(orders)
    effective = pd.DataFrame(effective)

    n = len(common)
    split = int(n * (1 - oos_fraction))
    slices = {"full": slice(None), "in_sample": slice(None, split), "out_of_sample": slice(split, None)}

    rows = []
    for label, sl in slices.items():
        seg_close, seg_orders = closes.iloc[sl], orders.iloc[sl].copy()
        if not len(seg_close):
            continue
        seg_orders.iloc[0] = effective.iloc[sl].iloc[0]  # inherit state
        pf = vbt.Portfolio.from_orders(
            seg_close,
            size=seg_orders,
            size_type="targetpercent",
            direction="both" if rules.allow_short else "longonly",
            fees=rules.fee_rate,
            slippage=rules.slippage,
            init_cash=init_cash,
            freq=timeframe,
            group_by=True,
            cash_sharing=True,
        )
        bench = (seg_close.iloc[-1] / seg_close.iloc[0] - 1.0).mean() * 100
        ret = float(pf.total_return()) * 100
        sharpe = float(pf.sharpe_ratio())
        rows.append(
            {
                "segment": label,
                "start": str(seg_close.index[0]),
                "end": str(seg_close.index[-1]),
                "return_pct": round(ret, 2),
                "bench_ew_bh_pct": round(bench, 2),
                "edge_pp": round(ret - bench, 2),
                "sharpe": round(sharpe, 2) if np.isfinite(sharpe) else np.nan,
                "max_dd_pct": round(float(pf.max_drawdown()) * -100, 2),
                "trades": int(pf.trades.count()),
                "fees": round(float(pf.orders.fees.sum()), 2),
            }
        )
    return pd.DataFrame(rows).set_index("segment")
