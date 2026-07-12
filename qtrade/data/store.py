"""Parquet-backed bar store, one file per (market, symbol, timeframe).

Layout:  <root>/<market>/<SYMBOL with / -> _>/<timeframe>.parquet
Appends are idempotent: overlapping timestamps are deduped (new wins).
DuckDB can query the files in place for ad-hoc research.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pandas as pd

from .schema import normalize_ohlcv, validate_ohlcv

DEFAULT_ROOT = Path(__file__).resolve().parents[2] / "data_store"


class BarStore:
    def __init__(self, root: Path | str = DEFAULT_ROOT):
        self.root = Path(root)

    def path(self, market: str, symbol: str, timeframe: str) -> Path:
        safe_symbol = symbol.replace("/", "_")
        return self.root / market / safe_symbol / f"{timeframe}.parquet"

    def save(self, df: pd.DataFrame, market: str, symbol: str, timeframe: str) -> Path:
        """Merge new bars into the existing file (dedup by ts, new wins)."""
        df = normalize_ohlcv(df)
        p = self.path(market, symbol, timeframe)
        if p.exists():
            old = pd.read_parquet(p)
            df = pd.concat([old, df])
            df = df[~df.index.duplicated(keep="last")].sort_index()
        validate_ohlcv(df)
        p.parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(p)
        return p

    def load(
        self,
        market: str,
        symbol: str,
        timeframe: str,
        start: str | pd.Timestamp | None = None,
        end: str | pd.Timestamp | None = None,
    ) -> pd.DataFrame:
        p = self.path(market, symbol, timeframe)
        if not p.exists():
            raise FileNotFoundError(
                f"no data for {market}/{symbol}/{timeframe}; fetch it first (see qtrade.cli fetch)"
            )
        df = pd.read_parquet(p)
        validate_ohlcv(df)
        if start is not None:
            df = df[df.index >= pd.Timestamp(start, tz="UTC")]
        if end is not None:
            df = df[df.index <= pd.Timestamp(end, tz="UTC")]
        return df

    def coverage(self) -> pd.DataFrame:
        """Summary of what's on disk: market, symbol, timeframe, span, rows."""
        rows = []
        for p in sorted(self.root.glob("*/*/*.parquet")):
            market, symbol, tf = p.parts[-3], p.parts[-2], p.stem
            try:
                meta = duckdb.sql(
                    f"SELECT min(ts) lo, max(ts) hi, count(*) n FROM '{p}'"
                ).fetchone()
            except duckdb.Error:
                continue  # non-bar parquet living under the store root (e.g. pit aux data)
            rows.append(
                {
                    "market": market,
                    "symbol": symbol.replace("_", "/"),
                    "timeframe": tf,
                    "start": meta[0],
                    "end": meta[1],
                    "bars": meta[2],
                }
            )
        return pd.DataFrame(rows)
