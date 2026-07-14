"""Look-ahead sentinels: no strategy output may depend on future bars.

Prefix invariance is the mechanical form of "no look-ahead": the target
weight at time t computed on data[:t+k] must equal the one computed on the
full series, for every t. A strategy peeking at the future in ANY way
(centered windows, full-series normalization, leaky resampling) breaks this
equality somewhere, so this is stronger than a profit-trap fixture — it
catches leaks too small to monetize. (E59's verdict is the cautionary tale
for why this must be an assertion, not a review habit.)
"""

import numpy as np
import pandas as pd
import pytest

from qtrade.presets import PRESETS

TRUNCS = [900, 1100, 1300]


def _bars(preset, n=1400, seed=3):
    rng = np.random.RandomState(seed)
    idx = pd.date_range("2023-01-01", periods=n, freq=preset.timeframe, tz="UTC")
    close = pd.Series(100 * np.exp(np.cumsum(rng.normal(0, 0.01, n))), index=idx)
    return pd.DataFrame(
        {"open": close, "high": close * 1.002, "low": close * 0.998,
         "close": close, "volume": rng.lognormal(10, 1, n)}
    ).rename_axis("ts")


@pytest.mark.parametrize("name", list(PRESETS))
def test_strategy_prefix_invariance(name):
    p = PRESETS[name]
    if p.build is None:  # llm_agents has no Strategy; it cannot be backtested at all
        pytest.skip(f"{name} has no Strategy (targets_fn book)")
    bars = _bars(p)
    w_full = p.strategy().target_position(bars)
    for t in TRUNCS:
        w_trunc = p.strategy().target_position(bars.iloc[:t])
        pd.testing.assert_series_equal(
            w_full.iloc[:t], w_trunc, check_names=False,
            obj=f"{name} weights[:{t}] (future bars changed past outputs)")
