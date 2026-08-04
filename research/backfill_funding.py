"""Backfill perpetual funding-rate history from Binance's public data archive.

E56 (2026-07-13) ruled the free funding axis blocked: binance API 451s from
this host, bybit 403s, OKX caps at 3 months. The external-strategy-map scorer
(2026-08-05) found the door E56 didn't try: data.binance.vision — a public
S3-style archive of monthly funding CSVs, verified HTTP 200 from this host,
with history back to 2020 for the majors. This backfills the whole axis and
turns the extreme-funding-reversal candidate (shortlisted) from "wait 6-12
months for self-collected data" into "researchable now".

Layout: data_store/funding_hist/<SYM>.parquet  (funding_time UTC, rate)
Usage: .venv/bin/python research/backfill_funding.py
"""

from __future__ import annotations

import io
import time
import zipfile
from pathlib import Path

import pandas as pd
import requests

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "data_store" / "funding_hist"
BASE = "https://data.binance.vision/data/futures/um/monthly/fundingRate"
SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT", "ADAUSDT",
           "AVAXUSDT", "DOTUSDT", "LINKUSDT", "LTCUSDT", "BNBUSDT"]
MONTHS = pd.period_range("2019-09", "2026-07", freq="M")


def fetch_month(sym: str, ym: str) -> pd.DataFrame | None:
    url = f"{BASE}/{sym}/{sym}-fundingRate-{ym}.zip"
    r = requests.get(url, timeout=30)
    if r.status_code != 200:
        return None
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        with z.open(z.namelist()[0]) as f:
            df = pd.read_csv(f)
    # archive schema drifted over the years; normalize defensively
    cols = {c.lower(): c for c in df.columns}
    tcol = cols.get("calc_time") or cols.get("fundingtime") or df.columns[0]
    rcol = cols.get("last_funding_rate") or cols.get("fundingrate") or df.columns[-1]
    out = pd.DataFrame({
        "funding_time": pd.to_datetime(df[tcol], unit="ms", utc=True, errors="coerce"),
        "rate": pd.to_numeric(df[rcol], errors="coerce"),
    }).dropna()
    return out if len(out) else None


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for sym in SYMBOLS:
        path = OUT / f"{sym}.parquet"
        have = pd.read_parquet(path) if path.exists() else None
        frames = [] if have is None else [have]
        got, miss = 0, 0
        for p in MONTHS:
            ym = str(p)
            if have is not None and len(have) and \
               have["funding_time"].dt.strftime("%Y-%m").eq(ym).any():
                continue
            df = fetch_month(sym, ym)
            if df is None:
                miss += 1
            else:
                frames.append(df)
                got += 1
            time.sleep(0.3)  # polite to a public bucket
        if frames:
            allf = (pd.concat(frames).drop_duplicates("funding_time")
                    .sort_values("funding_time").reset_index(drop=True))
            allf.to_parquet(path)
            print(f"{sym}: +{got} months (miss {miss}) -> {len(allf)} rows "
                  f"[{allf.funding_time.iloc[0].date()} .. {allf.funding_time.iloc[-1].date()}]",
                  flush=True)
        else:
            print(f"{sym}: nothing fetched (miss {miss})", flush=True)


if __name__ == "__main__":
    main()
