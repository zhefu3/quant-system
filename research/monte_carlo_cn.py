"""E29-style block-bootstrap risk profile for the cn_futures book (E50b).

Weekly blocks of the audited book's net daily returns, resampled into
thousands of 1-year paths: P(losing year), P(hitting the 18.5% halt),
plausible annual return / drawdown ranges. Expectation-setting for the
newly deployed book, not a new experiment.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qtrade.backtest.portfolio import run_portfolio  # noqa: E402
from qtrade.data.cn_futures import PRODUCTS  # noqa: E402
from qtrade.data.store import BarStore  # noqa: E402
from qtrade.markets.rules import CNFUTURES  # noqa: E402
from qtrade.presets import PRESETS  # noqa: E402

BLOCK = 5      # one trading week
YEAR = 252
N_PATHS = 5000
HALT = 0.185   # the preset's dd_halt


def net_daily_returns() -> pd.Series:
    store = BarStore()
    bars = {p: store.load("cnfutures_adj", p, "1d") for p in PRODUCTS}
    start = max(b.index[0] for b in bars.values())
    bars = {s: b[b.index >= start] for s, b in bars.items()}
    strat = PRESETS["cn_futures"].strategy()
    _, d = run_portfolio(strat, bars, CNFUTURES, "1d", allocation="equal",
                         rebalance_eps=0.02, align="ffill", return_details=True)
    W, closes = d["weights"], d["closes"]
    gross = (W.shift(1) * closes.pct_change()).sum(axis=1)
    costs = (W - W.shift(1)).abs().sum(axis=1).fillna(0.0) * (CNFUTURES.fee_rate + CNFUTURES.slippage)
    return (gross - costs).dropna()


def max_drawdown(path: np.ndarray) -> float:
    eq = np.cumprod(1 + path)
    return float((eq / np.maximum.accumulate(eq) - 1).min())


def main():
    r = net_daily_returns()
    print(f"history: {len(r)} days, ann ret {(1 + r).prod() ** (YEAR / len(r)) - 1:+.1%}, "
          f"ann vol {r.std() * np.sqrt(YEAR):.1%}")

    blocks = [r.values[i:i + BLOCK] for i in range(0, len(r) - BLOCK, BLOCK)]
    rng = np.random.default_rng(42)
    n_blocks = YEAR // BLOCK + 1
    rets, dds = [], []
    for _ in range(N_PATHS):
        idx = rng.integers(0, len(blocks), n_blocks)
        path = np.concatenate([blocks[i] for i in idx])[:YEAR]
        rets.append(float(np.prod(1 + path) - 1))
        dds.append(max_drawdown(path))
    rets, dds = np.array(rets), np.array(dds)

    print(f"\n{N_PATHS} bootstrap 1y paths (weekly blocks):")
    print(f"  P(losing year)        : {float((rets < 0).mean()):.0%}")
    print(f"  P(maxDD > {HALT:.0%} halt) : {float((dds < -HALT).mean()):.1%}")
    print(f"  annual return p5/p50/p95 : {np.percentile(rets, 5):+.1%} / "
          f"{np.percentile(rets, 50):+.1%} / {np.percentile(rets, 95):+.1%}")
    print(f"  maxDD p50/p95            : {np.percentile(dds, 50):.1%} / "
          f"{np.percentile(dds, 5):.1%}")


if __name__ == "__main__":
    main()
