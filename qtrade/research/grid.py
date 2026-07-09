"""Parameter-grid scan with lightweight metrics.

The point of a scan is NOT to pick the best cell — it's to look at the
neighborhood. A strategy whose edge lives on one isolated parameter island is
overfit; a robust one shows a plateau. Hence the sensitivity heatmap.
"""

from __future__ import annotations

import itertools
from pathlib import Path

import numpy as np
import pandas as pd
from tqdm import tqdm

from ..backtest.engine import Engine
from ..markets.rules import MarketRules


def _quick_metrics(pf) -> dict:
    """Cheap metrics without the full pf.stats() machinery."""
    ret = float(pf.total_return()) * 100
    bench = float(pf.total_benchmark_return()) * 100
    sharpe = float(pf.sharpe_ratio())
    return {
        "return_pct": round(ret, 2),
        "benchmark_pct": round(bench, 2),
        "edge_pct": round(ret - bench, 2),
        "sharpe": round(sharpe, 2) if np.isfinite(sharpe) else np.nan,
        "max_dd_pct": round(float(pf.max_drawdown()) * -100, 2),
        "trades": int(pf.trades.count()),
    }


def grid_scan(
    strategy_cls,
    bars: pd.DataFrame,
    grid: dict[str, list],
    rules: MarketRules,
    timeframe: str,
    fixed: dict | None = None,
    oos_fraction: float = 0.3,
) -> pd.DataFrame:
    """Run every param combo; report in-sample and out-of-sample metrics side by side."""
    engine = Engine(rules)
    keys = list(grid)
    rows = []
    combos = list(itertools.product(*(grid[k] for k in keys)))
    for values in tqdm(combos, desc=f"grid {strategy_cls.__name__}", unit="combo"):
        params = dict(zip(keys, values)) | (fixed or {})
        try:
            strategy = strategy_cls(**params)
            pfs = engine.portfolios(strategy, bars, timeframe, oos_fraction)
        except ValueError:
            continue  # invalid combo (e.g. fast >= slow)
        row = dict(zip(keys, values))
        for label, prefix in [("in_sample", "is"), ("out_of_sample", "oos")]:
            row |= {f"{prefix}_{k}": v for k, v in _quick_metrics(pfs[label]).items()}
        rows.append(row)
    return pd.DataFrame(rows).sort_values("oos_sharpe", ascending=False).reset_index(drop=True)


def sensitivity_heatmap(
    results: pd.DataFrame,
    x: str,
    y: str,
    value: str = "oos_edge_pct",
    out_path: Path | str = "outputs/sensitivity.png",
) -> Path:
    """Save a heatmap of `value` over the (x, y) parameter plane."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    pivot = results.pivot_table(index=y, columns=x, values=value)
    fig, ax = plt.subplots(figsize=(1.2 * len(pivot.columns) + 2, 0.8 * len(pivot) + 2))
    lim = np.nanmax(np.abs(pivot.values)) or 1.0
    im = ax.imshow(pivot.values, cmap="RdYlGn", vmin=-lim, vmax=lim, aspect="auto")
    ax.set_xticks(range(len(pivot.columns)), pivot.columns)
    ax.set_yticks(range(len(pivot.index)), pivot.index)
    ax.set_xlabel(x)
    ax.set_ylabel(y)
    ax.set_title(f"{value} — green plateau = robust, lone green island = overfit")
    for i in range(len(pivot.index)):
        for j in range(len(pivot.columns)):
            v = pivot.values[i, j]
            if np.isfinite(v):
                ax.text(j, i, f"{v:.0f}", ha="center", va="center", fontsize=8)
    fig.colorbar(im, ax=ax, shrink=0.8)
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=120, bbox_inches="tight")
    plt.close(fig)
    return out
