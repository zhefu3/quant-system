"""Statistical tests for paper records: is this track record luck?

Three questions, three tools (borrowed from Vibe-Trading's validation.py and
nautilus_trader's analysis module, reduced to what daily paper marks support):

  - sharpe_ci:  bootstrap CI for the annualized Sharpe — "is it > 0?"
  - dd_pvalue:  permutation test on drawdown — "are losses clustering more
                than iid ordering explains?" (Sharpe is order-invariant, so
                shuffling tests the PATH, i.e. the drawdown)
  - ab_test:    paired bootstrap on aligned daily returns — the arbiter tool
                for parallel-preset promotion (crypto_core vs v2, 2026-10-07)

All tests need >=MIN_MARKS daily marks; below that they refuse rather than
lend fake precision to noise.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

MIN_MARKS = 30
N_BOOT = 2000
SEED = 42


def daily_returns(equity_file: Path | str, column: str = "equity") -> pd.Series:
    """Last mark per UTC day -> daily returns."""
    eq = pd.read_csv(equity_file)
    ts = pd.to_datetime(eq["ts"], format="mixed", utc=True)
    s = pd.Series(eq[column].values, index=pd.DatetimeIndex(ts))
    daily = s.groupby(s.index.date).last()  # tz-ok: documented UTC-day mark grouping (docstring)
    return pd.Series(daily.values,
                     index=pd.to_datetime(daily.index)).pct_change().dropna()


def _ann_sharpe(r: np.ndarray) -> float:
    sd = r.std(ddof=1)
    return float(r.mean() / sd * np.sqrt(365)) if sd > 0 else 0.0


def sharpe_ci(returns: pd.Series, n_boot: int = N_BOOT,
              seed: int = SEED) -> dict | None:
    if len(returns) < MIN_MARKS:
        return None
    r = returns.to_numpy()
    rng = np.random.default_rng(seed)
    boots = np.array([_ann_sharpe(rng.choice(r, size=len(r), replace=True))
                      for _ in range(n_boot)])
    return {"sharpe": round(_ann_sharpe(r), 2),
            "ci_lo": round(float(np.percentile(boots, 5)), 2),
            "ci_hi": round(float(np.percentile(boots, 95)), 2),
            "p_positive": round(float((boots > 0).mean()), 3),
            "n_days": len(r)}


def dd_pvalue(returns: pd.Series, n_shuffle: int = 1000,
              seed: int = SEED) -> dict | None:
    if len(returns) < MIN_MARKS:
        return None
    r = returns.to_numpy()

    def max_dd(x):
        eq = np.cumprod(1 + x)
        return float((eq / np.maximum.accumulate(eq) - 1).min())

    obs = max_dd(r)
    rng = np.random.default_rng(seed)
    worse = sum(max_dd(rng.permutation(r)) <= obs for _ in range(n_shuffle))
    return {"max_dd": round(obs, 4),
            "p_ordering": round(worse / n_shuffle, 3),  # small = losses cluster
            "n_days": len(r)}


def ab_test(returns_a: pd.Series, returns_b: pd.Series, n_boot: int = N_BOOT,
            seed: int = SEED) -> dict | None:
    """Paired bootstrap on common days: does B beat A beyond luck?"""
    df = pd.concat([returns_a.rename("a"), returns_b.rename("b")], axis=1).dropna()
    if len(df) < MIN_MARKS:
        return None
    diff = (df["b"] - df["a"]).to_numpy()
    rng = np.random.default_rng(seed)
    boots = np.array([rng.choice(diff, size=len(diff), replace=True).mean()
                      for _ in range(n_boot)])
    return {"mean_daily_diff_bps": round(float(diff.mean()) * 1e4, 2),
            "p_b_better": round(float((boots > 0).mean()), 3),
            "sharpe_a": round(_ann_sharpe(df["a"].to_numpy()), 2),
            "sharpe_b": round(_ann_sharpe(df["b"].to_numpy()), 2),
            "n_days": len(df)}
