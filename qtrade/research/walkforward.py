"""Walk-forward validation: the closest a backtest gets to telling the truth.

Timeline is cut into rolling (train, test) windows. In each fold the best
params are chosen ONLY from the train window (by Sharpe), then applied to the
unseen test window. A strategy that only looks good with hindsight-picked
params fails here — which is the point.

Positions for the test window are computed with the train window as warm-up
context (via Engine.portfolios' oos mechanism), so indicators aren't blinded
at the fold boundary; trades are simulated on test bars only.
"""

from __future__ import annotations

import itertools

import pandas as pd
from tqdm import tqdm

from ..backtest.engine import Engine
from ..markets.rules import MarketRules
from .grid import _quick_metrics


def walk_forward(
    strategy_cls,
    bars: pd.DataFrame,
    grid: dict[str, list],
    rules: MarketRules,
    timeframe: str,
    n_folds: int = 5,
    train_bars: int | None = None,
    fixed: dict | None = None,
) -> pd.DataFrame:
    """Rolling train/test evaluation. Returns one row per fold.

    Window layout: |—— train (default 50% of a fold span) ——|— test —| sliding
    by test_len each fold, anchored so the last test window ends at the data's end.
    """
    n = len(bars)
    test_len = n // (n_folds + 2)
    train_len = train_bars or test_len * 2
    if train_len + n_folds * test_len > n:
        raise ValueError("not enough bars for the requested folds/train size")

    keys = list(grid)
    combos = [dict(zip(keys, v)) for v in itertools.product(*(grid[k] for k in keys))]
    engine = Engine(rules)
    rows = []

    # Fold k's test slice is [n - (n_folds-k)*test_len, next slice); the last
    # test slice ends exactly at the newest bar, train_len bars precede each.
    test_starts = [n - (n_folds - k) * test_len for k in range(n_folds)]
    for fold, ts in enumerate(tqdm(test_starts, desc="walk-forward", unit="fold")):
        window = bars.iloc[ts - train_len : ts + test_len]
        oos_fraction = test_len / len(window)

        best_params, best_sharpe = None, -float("inf")
        for params in combos:
            try:
                strategy = strategy_cls(**(params | (fixed or {})))
                pfs = engine.portfolios(strategy, window, timeframe, oos_fraction)
            except ValueError:
                continue
            m = _quick_metrics(pfs["in_sample"])
            if pd.notna(m["sharpe"]) and m["sharpe"] > best_sharpe:
                best_sharpe, best_params = m["sharpe"], params

        if best_params is None:
            continue
        strategy = strategy_cls(**(best_params | (fixed or {})))
        pfs = engine.portfolios(strategy, window, timeframe, oos_fraction)
        test_metrics = _quick_metrics(pfs["out_of_sample"])
        rows.append(
            {
                "fold": fold,
                "test_start": window.index[-test_len],
                "test_end": window.index[-1],
                "chosen": str(best_params),
                "train_sharpe": best_sharpe,
                **{f"test_{k}": v for k, v in test_metrics.items()},
            }
        )
    return pd.DataFrame(rows)


def wf_verdict(wf: pd.DataFrame) -> str:
    """One honest sentence about a walk-forward table."""
    if wf.empty:
        return "walk-forward 无有效折 — 数据不足或全部参数组合非法。"
    pos = int((wf["test_edge_pct"] > 0).sum())
    n = len(wf)
    mean_edge = wf["test_edge_pct"].mean()
    if pos > n / 2 and mean_edge > 0:
        return (
            f"{n} 折中 {pos} 折跑赢基准, 平均超额 {mean_edge:+.1f}pp — "
            "有初步稳健性, 下一步看多品种迁移。"
        )
    return (
        f"{n} 折中仅 {pos} 折跑赢基准, 平均超额 {mean_edge:+.1f}pp — "
        "训练窗选出的参数在未见数据上不成立, 判定过拟合/无优势。"
    )
