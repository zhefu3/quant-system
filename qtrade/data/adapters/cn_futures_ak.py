"""Domestic futures adapter: akshare per-contract refresh + E50b stitching.

No account needed. Bars are daily, close-stamped at 15:00 Asia/Shanghai — a
bar is complete the moment its timestamp has passed, so `drop_in_progress`
keeps bars with index <= now (unlike open-stamped crypto bars).
"""

from __future__ import annotations

import pandas as pd

from ..cn_futures import stitch, update_contracts
from ..schema import normalize_ohlcv


class CnFuturesAdapter:
    market = "cnfutures"

    def __init__(self, refresh: bool = True):
        self._refresh = refresh
        self._refreshed = False

    def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        start: pd.Timestamp,
        end: pd.Timestamp | None = None,
    ) -> pd.DataFrame:
        if timeframe != "1d":
            raise ValueError(f"cnfutures supports 1d only, got {timeframe}")
        if self._refresh and not self._refreshed:
            update_contracts()  # marker-gated: hits the network once per session
            self._refreshed = True
        bars, _ = stitch(symbol)
        if bars is None or bars.empty:
            raise RuntimeError(f"no contract data for {symbol}")
        bars = bars[bars.index >= pd.Timestamp(start).tz_convert("UTC")]
        if end is not None:
            bars = bars[bars.index <= pd.Timestamp(end).tz_convert("UTC")]
        return normalize_ohlcv(bars)

    @staticmethod
    def drop_in_progress(bars: pd.DataFrame, now: pd.Timestamp,
                         tf_delta: pd.Timedelta) -> pd.DataFrame:
        return bars[bars.index <= now]  # close-stamped: past timestamp = complete
