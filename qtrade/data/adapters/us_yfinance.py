"""US equities adapter via yfinance (free; splits/dividends auto-adjusted).

Yahoo limits intraday history depth (5m ~60 days, 1h ~730 days), so this
adapter is daily-first: use 1d for anything longer than those windows. When a
paid source (e.g. Polygon) lands, it should replace this behind the same
interface.
"""

from __future__ import annotations

import pandas as pd

from ..schema import normalize_ohlcv

INTERVAL_MAP = {"5m": "5m", "15m": "15m", "30m": "30m", "1h": "1h", "1d": "1d"}


class USAdapter:
    market = "us"

    def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        start: pd.Timestamp,
        end: pd.Timestamp | None = None,
    ) -> pd.DataFrame:
        if timeframe not in INTERVAL_MAP:
            raise ValueError(f"yfinance supports {list(INTERVAL_MAP)}, got {timeframe}")
        import yfinance as yf

        df = yf.download(
            symbol,
            start=pd.Timestamp(start).date(),
            end=pd.Timestamp(end).date() if end is not None else None,
            interval=INTERVAL_MAP[timeframe],
            auto_adjust=True,  # backtests need split/dividend-adjusted prices
            progress=False,
        )
        if df is None or df.empty:
            raise RuntimeError(f"yfinance returned no data for {symbol} {timeframe}")
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df = df.rename(columns=str.lower)
        # Daily bars come back tz-naive (session dates): pin them to UTC;
        # intraday bars are already tz-aware.
        return normalize_ohlcv(df)
