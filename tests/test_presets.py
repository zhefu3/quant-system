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


def test_crypto_core_15_is_core_scaled():
    """E71: the 15% tier is exactly core x 1.154 — a risk dial, not a signal."""
    import numpy as np
    import pandas as pd

    from qtrade.presets import CRYPTO_CORE, CRYPTO_CORE_15

    idx = pd.date_range("2024-01-01", periods=3000, freq="h", tz="UTC")
    rng = np.random.RandomState(3)
    px = pd.Series(100 * np.cumprod(1 + rng.normal(0, 0.004, 3000)), index=idx)
    bars = pd.DataFrame({"open": px, "high": px, "low": px, "close": px,
                         "volume": 1e6}, index=idx)
    a = CRYPTO_CORE.strategy().target_position(bars)
    b = CRYPTO_CORE_15.strategy().target_position(bars)
    pd.testing.assert_series_equal(b, (a * 1.154).clip(-1, 1), check_names=False)
