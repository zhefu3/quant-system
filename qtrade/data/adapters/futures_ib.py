"""IBKR futures adapter: back-adjusted CONTFUT daily bars via IB Gateway.

Read-only connection to the paper gateway (port 4002) — this adapter never
places orders; the observation book simulates fills internally like every
other paper book.

Two conventions that differ from the other adapters:

  - Bars are date-stamped at 00:00 America/New_York (as IBKR returns them).
    A bar for trade date D is complete at the 17:00 ET session close, so
    `drop_in_progress` keeps bars with index + 17h <= now. Grain products
    close earlier (14:20 ET); waiting until 17:00 is merely conservative.
  - NEVER persist these bars incrementally into the BarStore: the whole
    back-adjusted series shifts at every contract roll, so merging bars
    fetched at different roll epochs corrupts the archive. Research wants
    this data? Refetch the full depth wholesale.
"""

from __future__ import annotations

import pandas as pd

from ..schema import normalize_ohlcv

HOST, PORT, CLIENT_ID = "127.0.0.1", 4002, 7  # paper gateway, read-only
SESSION_CLOSE = pd.Timedelta(hours=17)  # 17:00 ET close, bars stamped 00:00 ET

EXCHANGE = {
    "ES": "CME", "NQ": "CME",
    "ZN": "CBOT", "ZC": "CBOT",
    "GC": "COMEX", "SI": "COMEX", "HG": "COMEX",
    "CL": "NYMEX", "NG": "NYMEX",
}


class IbkrFuturesAdapter:
    market = "futures_ibkr"

    def __init__(self):
        self._ib = None

    def _connect(self):
        if self._ib is None:
            from ib_async import IB

            ib = IB()
            # Per-request cap: when an IB data farm flaps, qualifyContracts/
            # reqHistoricalData hang indefinitely — without this the tick eats
            # the full 900s wall-clock and dies UNGRACEFULLY (no disconnect),
            # leaking server-side account-summary subscriptions until the
            # session throws Error 322 (2026-07-23: 31h of exactly that).
            # A bounded failure exits through cleanup instead.
            ib.RequestTimeout = 60
            try:
                ib.connect(HOST, PORT, clientId=CLIENT_ID, timeout=15, readonly=True)
            except (OSError, ConnectionError, TimeoutError) as e:
                raise RuntimeError(
                    f"IB Gateway not reachable at {HOST}:{PORT} — start/login the "
                    "paper gateway; this tick is skipped, positions unchanged"
                ) from e
            self._ib = ib
        return self._ib

    def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        start: pd.Timestamp,
        end: pd.Timestamp | None = None,
    ) -> pd.DataFrame:
        if timeframe != "1d":
            raise ValueError(f"futures_ibkr supports 1d only, got {timeframe}")
        if symbol not in EXCHANGE:
            raise ValueError(f"unknown IBKR futures symbol {symbol}")
        from ib_async import ContFuture, util

        ib = self._connect()
        contract = ContFuture(symbol, exchange=EXCHANGE[symbol], currency="USD")
        try:
            ib.qualifyContracts(contract)
        except Exception:
            # bounded failure (RequestTimeout) — disconnect NOW so the server
            # never accumulates half-dead sessions across aborted ticks
            self.close()
            raise
        days = max(1, (pd.Timestamp.now("UTC") - pd.Timestamp(start)).days)
        years = min(10, days // 365 + 1)
        # useRTH=True verified bar-identical to the E40b research archive
        # (2026-07-13, 750-bar ES overlap, zero diff) — research and live
        # must consume the same series.
        try:
            raw = ib.reqHistoricalData(
                contract, endDateTime="", durationStr=f"{years} Y",
                barSizeSetting="1 day", whatToShow="ADJUSTED_LAST",
                useRTH=True, formatDate=2,
            )
        except Exception:
            self.close()  # bounded failure exits through a clean disconnect
            raise
        if not raw:
            raise RuntimeError(f"IBKR returned no CONTFUT bars for {symbol}")
        df = util.df(raw).rename(columns=str.lower)
        idx = pd.DatetimeIndex(pd.to_datetime(df["date"])).tz_localize("America/New_York")
        df = df.set_index(idx)
        bars = normalize_ohlcv(df)
        bars = bars[bars.index >= pd.Timestamp(start).tz_convert("UTC")]
        if end is not None:
            bars = bars[bars.index <= pd.Timestamp(end).tz_convert("UTC")]
        return bars

    @staticmethod
    def drop_in_progress(bars: pd.DataFrame, now: pd.Timestamp,
                         tf_delta: pd.Timedelta) -> pd.DataFrame:
        return bars[bars.index + SESSION_CLOSE <= now]

    def close(self):
        if self._ib is not None:
            self._ib.disconnect()
            self._ib = None
