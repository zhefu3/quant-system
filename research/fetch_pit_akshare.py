"""E43 part-2b: fill the union universe via akshare (eastmoney backend).

baostock blacklisted us for parallel hammering — lesson logged. This
fetcher is deliberately polite: single-threaded, rate-limited, resumable.
akshare also returns DELISTED stocks' history (verified: 300104), which is
exactly what a survivorship-corrected universe needs.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qtrade.data.schema import normalize_ohlcv  # noqa: E402
from qtrade.data.store import BarStore  # noqa: E402

PIT = Path(__file__).resolve().parents[1] / "data_store" / "pit"
COLMAP = {"开盘": "open", "最高": "high", "最低": "low", "收盘": "close", "成交量": "volume"}


def fetch_one(code: str) -> str:
    import akshare as ak

    digits = code.split(".")[1]
    sym = f"{digits}.{code.split('.')[0].upper()}"
    store = BarStore()
    if store.path("ashare_pit", sym, "1d").exists():
        return f"skip {sym}"
    try:
        df = ak.stock_zh_a_hist(symbol=digits, period="daily",
                                start_date="20140101", end_date="20991231",
                                adjust="hfq")
        if df is None or df.empty:
            return f"EMPTY {sym}"
        df = df.rename(columns=COLMAP)[list(COLMAP.values())].assign(
            ts=pd.to_datetime(df["日期"]))
        df = df.set_index("ts")
        df.index = (df.index + pd.Timedelta(hours=15)).tz_localize("Asia/Shanghai").tz_convert("UTC")
        store.save(normalize_ohlcv(df.astype(float)), "ashare_pit", sym, "1d")
        return f"ok {sym} {len(df)}"
    except Exception as e:  # noqa: BLE001
        return f"FAIL {sym}: {str(e)[:60]}"


def main():
    codes = sorted(pd.read_parquet(PIT / "hs300_membership.parquet")["code"].unique())
    store = BarStore()
    todo = [c for c in codes
            if not store.path("ashare_pit",
                              f"{c.split('.')[1]}.{c.split('.')[0].upper()}", "1d").exists()]
    print(f"todo {len(todo)}/{len(codes)}", flush=True)
    fails = 0
    for i, code in enumerate(todo, 1):
        msg = fetch_one(code)
        if msg.startswith(("FAIL", "EMPTY")):
            fails += 1
        if msg.startswith(("FAIL", "EMPTY")) or i % 50 == 0:
            print(f"[{i}/{len(todo)}] {msg}", flush=True)
        time.sleep(0.3)  # be polite: we got blacklisted once already
    print(f"DONE fails={fails}")


if __name__ == "__main__":
    main()
