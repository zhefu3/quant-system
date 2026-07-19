"""TCA scaffold: recording must be side-effect-safe and the report must read
back what recording wrote (dormant until real fills, so the roundtrip IS the
test)."""

import pandas as pd

from qtrade.live.tca import record_fill


def test_record_fill_roundtrip(tmp_path):
    order = {"id": "abc123", "average": 100.5, "status": "closed"}
    record_fill(tmp_path, "2026-07-19T00:00:00", "BTC/USDT", "buy", 3, 100.0, order)
    df = pd.read_csv(tmp_path / "tca.csv")
    assert len(df) == 1
    assert df["fill_px"].iloc[0] == 100.5
    # bought at 100.5 vs arrival 100.0 -> paid 50bp
    assert abs(df["slip_bps"].iloc[0] - 50.0) < 1e-6


def test_record_fill_sell_sign(tmp_path):
    order = {"id": "x", "average": 99.5}
    record_fill(tmp_path, "t", "ETH/USDT", "sell", 1, 100.0, order)
    df = pd.read_csv(tmp_path / "tca.csv")
    # sold 50bp below arrival -> +50bp cost (sign convention: positive = paid)
    assert abs(df["slip_bps"].iloc[0] - 50.0) < 1e-6


def test_record_fill_missing_price_never_raises(tmp_path):
    record_fill(tmp_path, "t", "BTC/USDT", "buy", 1, 100.0, {"id": "y"})
    df = pd.read_csv(tmp_path / "tca.csv")
    assert len(df) == 1 and pd.isna(df.get("slip_bps", pd.Series([None])).iloc[0])
