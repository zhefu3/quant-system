"""Adapter interface: every market data source implements this."""

from __future__ import annotations

from typing import Protocol

import pandas as pd


class DataAdapter(Protocol):
    market: str  # "crypto" | "ashare" | "us"

    def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        start: pd.Timestamp,
        end: pd.Timestamp | None = None,
    ) -> pd.DataFrame:
        """Return bars in the canonical OHLCV schema (see qtrade.data.schema)."""
        ...
