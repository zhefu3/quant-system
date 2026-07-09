"""Canonical OHLCV schema shared by every market adapter.

All bar data in the system is a DataFrame with:
  - index: DatetimeIndex named "ts", tz-aware UTC, ascending, unique
  - columns: open, high, low, close, volume (float64)

Adapters must return this shape; the store and backtest layers assume it.
"""

from __future__ import annotations

import pandas as pd

OHLCV_COLUMNS = ["open", "high", "low", "close", "volume"]


def normalize_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    """Coerce a raw OHLCV frame into the canonical schema, or raise."""
    missing = [c for c in OHLCV_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"OHLCV frame missing columns: {missing}")

    out = df[OHLCV_COLUMNS].astype("float64").copy()

    if not isinstance(out.index, pd.DatetimeIndex):
        raise ValueError("OHLCV frame must be indexed by a DatetimeIndex")
    if out.index.tz is None:
        out.index = out.index.tz_localize("UTC")
    else:
        out.index = out.index.tz_convert("UTC")
    out.index.name = "ts"

    out = out[~out.index.duplicated(keep="last")].sort_index()
    return out


def resample_ohlcv(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    """Downsample canonical bars (ts = bar open time) to a coarser timeframe."""
    out = df.resample(rule, label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    )
    return out.dropna(subset=["close"])


def validate_ohlcv(df: pd.DataFrame) -> None:
    """Raise if a frame violates the canonical schema."""
    if list(df.columns) != OHLCV_COLUMNS:
        raise ValueError(f"columns must be exactly {OHLCV_COLUMNS}, got {list(df.columns)}")
    if not isinstance(df.index, pd.DatetimeIndex) or df.index.tz is None:
        raise ValueError("index must be a tz-aware DatetimeIndex")
    if not df.index.is_monotonic_increasing:
        raise ValueError("index must be ascending")
    if df.index.has_duplicates:
        raise ValueError("index must be unique")
