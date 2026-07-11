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


def _save(df: pd.DataFrame, sym: str) -> str:
    df = df.set_index("ts")
    df.index = (df.index + pd.Timedelta(hours=15)).tz_localize("Asia/Shanghai").tz_convert("UTC")
    BarStore().save(normalize_ohlcv(df.astype(float)), "ashare_pit", sym, "1d")
    return f"ok {sym} {len(df)}"


def fetch_one(code: str) -> str:
    import akshare as ak

    digits = code.split(".")[1]
    ex = code.split(".")[0]
    sym = f"{digits}.{ex.upper()}"
    store = BarStore()
    if store.path("ashare_pit", sym, "1d").exists():
        return f"skip {sym}"

    # Route 1: sina — fast, but does NOT cover delisted names.
    try:
        df = ak.stock_zh_a_daily(symbol=f"{ex}{digits}", start_date="20140101",
                                 end_date="20991231", adjust="hfq")
        if df is not None and not df.empty:
            df = df.rename(columns={"date": "ts"})
            out = df[["open", "high", "low", "close", "volume"]].assign(
                ts=pd.to_datetime(df["ts"]))
            return _save(out, sym)
    except Exception:  # noqa: BLE001 — likely delisted: fall through to eastmoney
        pass

    # Route 2: eastmoney — covers delisted; throttled, so back off politely.
    for attempt in range(3):
        try:
            df = ak.stock_zh_a_hist(symbol=digits, period="daily",
                                    start_date="20140101", end_date="20991231",
                                    adjust="hfq")
            if df is None or df.empty:
                return f"EMPTY {sym}"
            out = df.rename(columns=COLMAP)[list(COLMAP.values())].assign(
                ts=pd.to_datetime(df["日期"]))
            return _save(out, sym)
        except Exception as e:  # noqa: BLE001
            if attempt == 2:
                return f"FAIL {sym}: {str(e)[:60]}"
            time.sleep(30 * (attempt + 1))
    return f"FAIL {sym}"


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
        if msg.startswith(("FAIL", "EMPTY")) or i % 25 == 0:
            print(f"[{i}/{len(todo)}] {msg}", flush=True)
        time.sleep(1.2)  # be VERY polite: two throttling incidents already
    print(f"DONE fails={fails}")


if __name__ == "__main__":
    main()
