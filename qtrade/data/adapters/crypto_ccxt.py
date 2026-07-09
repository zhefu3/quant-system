"""Crypto adapter via ccxt public REST (no API key needed for OHLCV).

Tries exchanges in order until one responds — binance is geo-blocked in some
regions, so okx is the default fallback.
"""

from __future__ import annotations

import time

import ccxt
import pandas as pd
from tqdm import tqdm

from ..schema import normalize_ohlcv

TIMEFRAME_MS = {
    "1m": 60_000,
    "5m": 300_000,
    "15m": 900_000,
    "30m": 1_800_000,
    "1h": 3_600_000,
    "4h": 14_400_000,
    "1d": 86_400_000,
}


class CryptoAdapter:
    market = "crypto"

    def __init__(self, exchanges: list[str] | None = None):
        self._exchange_ids = exchanges or ["binance", "okx"]
        self._exchange: ccxt.Exchange | None = None

    @property
    def exchange(self) -> ccxt.Exchange:
        if self._exchange is None:
            last_err: Exception | None = None
            for ex_id in self._exchange_ids:
                ex = getattr(ccxt, ex_id)({"enableRateLimit": True})
                try:
                    ex.load_markets()
                    self._exchange = ex
                    break
                except Exception as e:  # geo-block / network — try next venue
                    last_err = e
            if self._exchange is None:
                raise ConnectionError(
                    f"no exchange reachable among {self._exchange_ids}: {last_err}"
                )
        return self._exchange

    def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        start: pd.Timestamp,
        end: pd.Timestamp | None = None,
    ) -> pd.DataFrame:
        if timeframe not in TIMEFRAME_MS:
            raise ValueError(f"unsupported timeframe {timeframe}")
        ex = self.exchange
        since = int(pd.Timestamp(start).tz_convert("UTC").timestamp() * 1000)
        end_ms = int(
            (pd.Timestamp(end).tz_convert("UTC") if end is not None else pd.Timestamp.now("UTC"))
            .timestamp() * 1000
        )
        step = TIMEFRAME_MS[timeframe]
        limit = 1000

        rows: list[list[float]] = []
        expected_batches = max(1, (end_ms - since) // (step * limit) + 1)
        with tqdm(total=expected_batches, desc=f"{ex.id} {symbol} {timeframe}", unit="batch") as bar:
            while since < end_ms:
                batch = self._fetch_with_retry(ex, symbol, timeframe, since, limit)
                if not batch:
                    break
                rows.extend(batch)
                new_since = batch[-1][0] + step
                if new_since <= since:  # defensive: avoid infinite loop
                    break
                since = new_since
                bar.update(1)

        if not rows:
            raise RuntimeError(f"{ex.id} returned no data for {symbol} {timeframe}")

        df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])
        df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
        df = df.set_index("ts")
        df = df[df.index <= pd.Timestamp(end_ms, unit="ms", tz="UTC")]
        return normalize_ohlcv(df)

    @staticmethod
    def _fetch_with_retry(ex, symbol, timeframe, since, limit, retries=3):
        for attempt in range(retries):
            try:
                return ex.fetch_ohlcv(symbol, timeframe, since=since, limit=limit)
            except (ccxt.NetworkError, ccxt.ExchangeNotAvailable):
                if attempt == retries - 1:
                    raise
                time.sleep(2**attempt)
