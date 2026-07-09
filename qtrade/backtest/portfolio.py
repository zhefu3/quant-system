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
    if style == "inv_beta":
        # Low-beta tilt (E36: beta IC negative at weekly horizon). Slow-moving
        # weights so the tilt adds ~no turnover; BTC itself gets beta 1.
        r = closes.pct_change()
        btc = r.get("BTC/USDT")
        if btc is None:
            raise ValueError("inv_beta allocation needs BTC/USDT in the universe")
        beta = r.rolling(720).cov(btc).div(btc.rolling(720).var(), axis=0)
        inv = 1.0 / beta.clip(lower=0.25)  # cap the tilt; shorts of beta make no sense here
        alloc = inv.div(inv.sum(axis=1), axis=0)
        return alloc.fillna(1.0 / closes.shape[1])
    raise ValueError(f"unknown allocation style {style!r}")


BARS_PER_YEAR = {"1h": 8760.0, "4h": 2190.0, "1d": 365.0}


def _scale_to_book_vol(
    W: pd.DataFrame,
    closes: pd.DataFrame,
    timeframe: str,
    target: float,
    window: int,
    cap: float,
) -> pd.DataFrame:
    """Scale the whole weight matrix so the BOOK's trailing realized vol tracks
    `target` (annualized). Uses only past returns of the weights actually held
    (pre-cost approximation); scaling up is capped and total gross stays <= 1."""
    bpy = BARS_PER_YEAR.get(timeframe, 8760.0)
    book_ret = (W.shift(1) * closes.pct_change()).sum(axis=1)
    realized = book_ret.rolling(window).std() * np.sqrt(bpy)
    scale = (target / realized).clip(upper=cap).fillna(1.0)
    scaled = W.mul(scale, axis=0).clip(-1.0, 1.0)
    gross = scaled.abs().sum(axis=1)
    over = gross > 1.0
    if over.any():
        scaled.loc[over] = scaled.loc[over].div(gross[over], axis=0)
    return scaled


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
    return_details: bool = False,
    align: str = "inner",
    book_vol_target: float | None = None,
    book_vol_window: int = 168,
    book_scale_cap: float = 2.0,
):
    """Backtest `strategy` applied to every symbol under a shared cash pool.

    Returns a summary frame (full / in_sample / out_of_sample rows) comparing
    the portfolio against an equal-weight buy&hold of the same symbols.
    With return_details=True, returns (summary, {"weights": ..., "closes": ...})
    where weights is the effective per-symbol weight matrix actually held.
    """
    engine = Engine(rules, init_cash=init_cash, rebalance_eps=rebalance_eps)

    if align == "inner":
        # Common timeline: inner join so every symbol has a bar at every ts.
        common = None
        for df in bars_by_symbol.values():
            common = df.index if common is None else common.intersection(df.index)
        closes = pd.DataFrame(
            {sym: df.loc[common, "close"] for sym, df in bars_by_symbol.items()}
        )
    elif align == "ffill":
        # Union timeline for large stock universes where suspensions would
        # collapse an intersection. Gaps are forward-filled for marking;
        # fills at stale prices are an approximation — suspended names rarely
        # top a momentum rank because their momentum input is stale too.
        common = None
        for df in bars_by_symbol.values():
            common = df.index if common is None else common.union(df.index)
        closes = pd.DataFrame(
            {sym: df["close"].reindex(common) for sym, df in bars_by_symbol.items()}
        ).ffill()
    else:
        raise ValueError(f"unknown align {align!r}")

    if hasattr(strategy, "target_weights"):
        # Portfolio-level strategy: emits the whole weight matrix itself
        # (its gross budget replaces the per-symbol allocation step).
        W = strategy.target_weights(closes)[list(closes.columns)]
    else:
        alloc = _allocations(closes, allocation, vol_window)
        cols = {}
        for sym, df in bars_by_symbol.items():
            # Signals are computed on the symbol's native bars, then held
            # (ffilled) across any timestamps it is missing from the grid.
            raw = strategy.target_position(df).reindex(common).ffill().fillna(0.0)
            cols[sym] = (raw * alloc[sym]).clip(-1.0, 1.0)
        W = pd.DataFrame(cols)

    if book_vol_target is not None:
        W = _scale_to_book_vol(W, closes, timeframe, book_vol_target,
                               book_vol_window, book_scale_cap)

    orders, effective = {}, {}
    for sym in W.columns:
        orders[sym], effective[sym] = engine.process_weights(W[sym], common)
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
    summary = pd.DataFrame(rows).set_index("segment")
    if return_details:
        return summary, {"weights": effective, "closes": closes}
    return summary
