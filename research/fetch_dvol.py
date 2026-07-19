"""E66 data repository: Deribit DVOL (30-day constant-maturity implied vol).

Layout (data_store/deribit/):
  dvol_<CCY>_1d.parquet    daily DVOL candles, UTC-stamped, columns
                           open/high/low/close (annualized vol in %)

Public API, no key. Paginated via `continuation`; idempotent — reruns merge.
Every call carries a hard timeout (HANDOFF rule); polite sleep between pages.
Timestamp convention (frozen in the E66 prereg): a row stamped day D holds
D's daily candle; consumers may use its close from D+1 00:00 UTC onward.
"""

from __future__ import annotations

import json
import sys
import time
import urllib.request
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1] / "data_store" / "deribit"
API = "https://www.deribit.com/api/v2/public/get_volatility_index_data"
EPOCH_START = 1262304000000  # 2010-01-01, safely before DVOL existed
SLEEP = 0.3


def _get(url: str, timeout: float = 30.0) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "qtrade-research"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def fetch_dvol(ccy: str) -> pd.DataFrame:
    end = int(time.time() * 1000)
    rows: list[list] = []
    for _ in range(50):  # hard page cap — ~50k days is beyond any reality
        url = (f"{API}?currency={ccy}&start_timestamp={EPOCH_START}"
               f"&end_timestamp={end}&resolution=1D")
        res = _get(url)["result"]
        data = res.get("data", [])
        if not data:
            break
        rows = data + rows
        cont = res.get("continuation")
        if not cont:
            break
        end = cont
        time.sleep(SLEEP)
    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close"])
    df.index = pd.to_datetime(df["ts"], unit="ms", utc=True)
    df = df.drop(columns=["ts"]).sort_index()
    return df[~df.index.duplicated(keep="last")]


def main() -> None:
    ROOT.mkdir(parents=True, exist_ok=True)
    for ccy in ("BTC", "ETH"):
        fresh = fetch_dvol(ccy)
        f = ROOT / f"dvol_{ccy}_1d.parquet"
        if f.exists():
            old = pd.read_parquet(f)
            fresh = pd.concat([old, fresh])
            fresh = fresh[~fresh.index.duplicated(keep="last")].sort_index()
        fresh.to_parquet(f)
        print(f"{ccy}: {len(fresh)} rows, {fresh.index[0].date()} -> "
              f"{fresh.index[-1].date()}, close last={fresh['close'].iloc[-1]:.1f}")


if __name__ == "__main__":
    sys.exit(main())
