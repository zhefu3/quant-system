"""Factor zoo registry: discovery, sanity gates, deterministic compute."""

import numpy as np
import pandas as pd
import pytest

from qtrade.factors import FactorRegistry


@pytest.fixture(scope="module")
def reg():
    return FactorRegistry()


@pytest.fixture(scope="module")
def panel():
    rng = np.random.RandomState(0)
    idx = pd.date_range("2023-01-01", periods=300, freq="B", tz="UTC")
    codes = [f"S{i:03d}" for i in range(20)]
    close = pd.DataFrame(100 * np.exp(np.cumsum(rng.normal(0, 0.02, (300, 20)), axis=0)),
                         index=idx, columns=codes)
    vol = pd.DataFrame(rng.lognormal(10, 1, (300, 20)), index=idx, columns=codes)
    p = {"close": close, "open": close.shift(1).bfill() * 1.001,
         "high": close * 1.01, "low": close * 0.99, "volume": vol}
    p["amount"] = p["close"] * p["volume"]
    p["vwap"] = (p["high"] + p["low"] + p["close"]) / 3.0
    return p


def test_discovers_full_zoo(reg):
    assert len(reg) == 461
    assert len(reg.list()) >= 400  # OHLCV(+amount/vwap)-computable subset


def test_known_alpha_computes_and_is_deterministic(reg, panel):
    out1 = reg.compute("gtja191_001", panel)
    out2 = reg.compute("gtja191_001", panel)
    assert out1.shape == panel["close"].shape
    pd.testing.assert_frame_equal(out1, out2)


def test_meta_contract(reg):
    for aid in ["gtja191_001", "alpha101_001"]:
        m = reg.meta(aid)
        assert m["id"] == aid
        assert "columns_required" in m and "theme" in m


def test_missing_column_raises(reg, panel):
    p = {k: v for k, v in panel.items() if k != "volume"}
    with pytest.raises(KeyError):
        reg.compute("gtja191_001", p)
