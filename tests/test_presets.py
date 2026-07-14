"""Smoke tests: every registered preset must build and emit sane weights."""

import numpy as np
import pandas as pd
import pytest

from qtrade.presets import PRESETS


@pytest.mark.parametrize("name", list(PRESETS))
def test_preset_builds_and_emits_valid_weights(name):
    p = PRESETS[name]
    if p.build is None:  # llm_agents: targets come from an agent chain, not a Strategy
        pytest.skip(f"{name} has no Strategy (targets_fn book)")
    rng = np.random.RandomState(0)
    n = 1500
    idx = pd.date_range("2024-01-01", periods=n, freq=p.timeframe, tz="UTC")
    close = pd.Series(100 * np.exp(np.cumsum(rng.normal(0, 0.01, n))), index=idx)
    bars = pd.DataFrame(
        {"open": close, "high": close * 1.001, "low": close * 0.999,
         "close": close, "volume": 1.0}
    ).rename_axis("ts")

    strategy = p.strategy()
    w = strategy.target_position(bars)
    assert len(w) == n
    assert (w.abs() <= 1.0 + 1e-9).all()
    assert not w.isna().any()
    assert p.rules.allow_short or (w >= 0).all()

    # explain() must produce a self-consistent decision chain
    info = strategy.explain(bars)
    assert info["target"] == pytest.approx(float(w.iloc[-1]), abs=1e-4)  # 4dp rounding
    for leg in info.get("legs", []):
        assert "target" in leg and "mix" in leg
    # composite target must equal the mix-weighted sum of leg targets
    if info.get("legs"):
        blend = sum(leg["mix"] * leg["target"] for leg in info["legs"])
        assert info["target"] == pytest.approx(blend, abs=1e-3)
