"""Tests for OHLCV resampling and book-level vol scaling helpers."""

import numpy as np
import pandas as pd

from qtrade.backtest.portfolio import _scale_to_book_vol
from qtrade.data.schema import resample_ohlcv


def test_resample_preserves_ohlcv_semantics():
    idx = pd.date_range("2024-01-01", periods=8, freq="1h", tz="UTC")
    df = pd.DataFrame(
        {
            "open": [1, 2, 3, 4, 5, 6, 7, 8],
            "high": [2, 3, 4, 5, 6, 7, 8, 9],
            "low": [0.5, 1, 2, 3, 4, 5, 6, 7],
            "close": [1.5, 2.5, 3.5, 4.5, 5.5, 6.5, 7.5, 8.5],
            "volume": [1] * 8,
        },
        index=idx,
    ).rename_axis("ts").astype(float)
    out = resample_ohlcv(df, "4h")
    assert len(out) == 2
    first = out.iloc[0]
    assert first["open"] == 1 and first["close"] == 4.5
    assert first["high"] == 5 and first["low"] == 0.5 and first["volume"] == 4
    assert out.index[0] == idx[0]  # bar-open labelling


def test_book_vol_scaler_derisks_hot_books_and_caps_gross():
    rng = np.random.RandomState(1)
    idx = pd.date_range("2024-01-01", periods=600, freq="1h", tz="UTC")
    closes = pd.DataFrame(
        {c: 100 * np.exp(np.cumsum(rng.normal(0, 0.02, 600))) for c in "AB"},
        index=idx,
    )
    W = pd.DataFrame(0.6, index=idx, columns=list("AB"))  # gross 1.2, hot vol
    scaled = _scale_to_book_vol(W, closes, "1h", target=0.10, window=48, cap=2.0)
    # 2% hourly vol is ~190% annualized: the scaler must shrink hard.
    assert scaled.iloc[100:].abs().mean().mean() < 0.3
    assert (scaled.abs().sum(axis=1) <= 1.0 + 1e-9).all()
