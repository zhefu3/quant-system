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

ab_test additionally reports what it CANNOT see (2026-07-27 amendment, filed
before the 2026-10-07 verdict): a non-significant paired test on two nearly
identical books means "underpowered", not "equivalent". Two diagnostics make
that concrete: the minimum detectable dSharpe at 80% power, and the count of
days on which the two books actually differed. The second matters more —
when a variant differs from its champion only under a rare regime condition,
a quiet window administers zero doses of the treatment, and no amount of
calendar time fixes that. Such a window is INCONCLUSIVE_NO_EXPOSURE, and the
distinction is the whole point: no-exposure is a reason to keep waiting,
whereas a well-exposed null result is evidence of equivalence.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pandas as pd

MIN_MARKS = 30
N_BOOT = 2000
SEED = 42

# a day counts as "differentiating" when the two books' returns differ by more
# than 0.1bp — below that they are the same book to within accounting noise
DIFF_EPS = 1e-5
# minimum differentiating days before a null result may be read as equivalence
MIN_DIFF_DAYS = 10
Z_ALPHA = 1.959964  # two-sided 5%
Z_POWER = 0.841621  # 80% power


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


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
    """Paired bootstrap on common days: does B beat A beyond luck?

    Reports the effect estimate together with the test's resolving power, so a
    quiet result cannot be read as equivalence. See module docstring.
    """
    df = pd.concat([returns_a.rename("a"), returns_b.rename("b")], axis=1).dropna()
    if len(df) < MIN_MARKS:
        return None
    a, b = df["a"].to_numpy(), df["b"].to_numpy()
    diff = b - a
    n = len(diff)
    rng = np.random.default_rng(seed)
    boots = np.array([rng.choice(diff, size=n, replace=True).mean()
                      for _ in range(n_boot)])

    n_diff_days = int((np.abs(diff) > DIFF_EPS).sum())
    vol_a = float(a.std(ddof=1)) * np.sqrt(365)  # champion's annualized vol
    se_daily = float(diff.std(ddof=1)) / np.sqrt(n)

    # minimum detectable dSharpe at 80% power, and power at reference effects
    if se_daily > 0 and vol_a > 0:
        mde = (Z_ALPHA + Z_POWER) * se_daily * 365 / vol_a
        power = {f"dSR={d}": round(_norm_cdf(
            d * vol_a / 365 / se_daily - Z_ALPHA), 2) for d in (0.1, 0.25, 0.5)}
    else:  # identical books: no resolving power at any effect size
        mde, power = float("inf"), {f"dSR={d}": 0.0 for d in (0.1, 0.25, 0.5)}

    return {"mean_daily_diff_bps": round(float(diff.mean()) * 1e4, 2),
            "p_b_better": round(float((boots > 0).mean()), 3),
            "sharpe_a": round(_ann_sharpe(a), 2),
            "sharpe_b": round(_ann_sharpe(b), 2),
            "delta_sharpe": round(_ann_sharpe(b) - _ann_sharpe(a), 2),
            "n_days": n,
            "n_diff_days": n_diff_days,
            "mde_sharpe_80": round(mde, 3) if np.isfinite(mde) else None,
            "power": power,
            "exposure": ("OK" if n_diff_days >= MIN_DIFF_DAYS
                         else "INCONCLUSIVE_NO_EXPOSURE")}
