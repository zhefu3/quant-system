"""E29: block-bootstrap risk profile of crypto_core.

One 7-year path is a single draw from the strategy's return distribution.
Resampling weekly blocks of its net hourly returns builds thousands of
plausible 1-year paths, answering the questions a capital decision needs:

  - P(hit the -20% kill switch within a year)?
  - P(a losing year)?
  - the plausible range of annual returns and drawdowns

Block resampling preserves intra-week autocorrelation but shuffles regimes,
so treat tails as indicative, not gospel.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import vectorbt as vbt

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qtrade.backtest.portfolio import run_portfolio  # noqa: E402
from qtrade.data.store import BarStore  # noqa: E402
from qtrade.markets.rules import CRYPTO_PERP  # noqa: E402
from qtrade.presets import CRYPTO_CORE  # noqa: E402

MAJORS = ["BTC/USDT", "ETH/USDT", "XRP/USDT", "ADA/USDT", "LTC/USDT", "LINK/USDT"]
BLOCK = 168          # one week of hourly bars
YEAR = 8760
N_PATHS = 2000
KILL = 0.20


def net_hourly_returns() -> pd.Series:
    p = CRYPTO_CORE
    store = BarStore()
    bars = {s: store.load(p.market, s, p.timeframe) for s in MAJORS}
    start = max(b.index[0] for b in bars.values())
    bars = {s: b[b.index >= start] for s, b in bars.items()}
    _, d = run_portfolio(p.strategy(), bars, CRYPTO_PERP, "1h", allocation="equal",
                         rebalance_eps=p.rebalance_eps, return_details=True)
    W, closes = d["weights"], d["closes"]
    orders = W.where(W.ne(W.shift(1)), np.nan)
    orders.iloc[0] = W.iloc[0]
    pf = vbt.Portfolio.from_orders(
        closes, size=orders, size_type="targetpercent", direction="both",
        fees=CRYPTO_PERP.fee_rate, slippage=CRYPTO_PERP.slippage,
        init_cash=10_000, freq="1h", group_by=True, cash_sharing=True)
    return pf.returns()


def max_drawdown(path: np.ndarray) -> float:
    eq = np.cumprod(1 + path)
    return float((eq / np.maximum.accumulate(eq) - 1).min())


def main():
    rets = net_hourly_returns().to_numpy()
    n = len(rets)
    rng = np.random.default_rng(7)
    n_blocks = YEAR // BLOCK + 1

    ann, dds = np.empty(N_PATHS), np.empty(N_PATHS)
    for i in range(N_PATHS):
        starts = rng.integers(0, n - BLOCK, n_blocks)
        path = np.concatenate([rets[s:s + BLOCK] for s in starts])[:YEAR]
        ann[i] = np.prod(1 + path) - 1
        dds[i] = max_drawdown(path)

    q = lambda a, p: float(np.percentile(a, p))
    print(f"paths {N_PATHS} x 1y | block {BLOCK}h | source: {n} net hourly returns (7y, 6 majors)")
    print(f"annual return : p5 {q(ann,5):+.1%}  median {q(ann,50):+.1%}  p95 {q(ann,95):+.1%}")
    print(f"max drawdown  : p50 {q(dds,50):.1%}  p95 {q(dds,5):.1%}  worst {dds.min():.1%}")
    print(f"P(losing year)          : {float((ann < 0).mean()):.1%}")
    print(f"P(hit -{KILL:.0%} kill switch): {float((dds <= -KILL).mean()):.1%}")
    print(f"P(annual > +20%)        : {float((ann > 0.20).mean()):.1%}")


if __name__ == "__main__":
    main()
