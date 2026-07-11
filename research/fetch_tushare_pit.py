"""E43-v2: full PIT dataset from Tushare Pro (the real thing).

Downloads once into local parquet — backtests run on local data forever:
  part 1  monthly index membership + WEIGHTS (399300.SZ, 2014->now)
  part 2  per-stock: hfq daily bars, daily_basic (pe/pb/mv/turnover),
          fina_indicator (with ann_date) for the union universe
  part 3  north-bound flows (market level)

Resumable; self rate-limited (90 calls/min) — one throttling incident on
this project was one too many.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qtrade.data.adapters.ashare_tushare import TushareData  # noqa: E402
from qtrade.data.schema import normalize_ohlcv  # noqa: E402
from qtrade.data.store import BarStore  # noqa: E402

PIT = Path(__file__).resolve().parents[1] / "data_store" / "pit_ts"


def main():
    td = TushareData(calls_per_min=90)
    PIT.mkdir(parents=True, exist_ok=True)

    # -- part 1: membership + weights ---------------------------------------
    mw_file = PIT / "hs300_weights.parquet"
    if not mw_file.exists():
        mw = td.index_members_monthly("399300.SZ", start="20140101")
        mw.to_parquet(mw_file)
        print(f"membership+weights: {len(mw)} rows, "
              f"{mw['trade_date'].nunique()} snapshots", flush=True)
    mw = pd.read_parquet(mw_file)
    union = sorted(mw["con_code"].unique())
    print(f"union universe: {len(union)} codes", flush=True)

    # -- part 2: per-stock panels --------------------------------------------
    store = BarStore()
    (PIT / "daily_basic").mkdir(exist_ok=True)
    (PIT / "fina").mkdir(exist_ok=True)
    for i, code in enumerate(union, 1):
        sym = code.replace(".", "_")
        try:
            if not store.path("ashare_ts", code, "1d").exists():
                bars = td.daily_bars(code)
                if bars is not None and len(bars):
                    bars = bars.rename(columns={"vol": "volume"})
                    bars["ts"] = pd.to_datetime(bars["trade_date"])
                    bars = bars.set_index("ts").sort_index()
                    bars.index = bars.index.tz_localize("Asia/Shanghai").tz_convert("UTC")
                    store.save(normalize_ohlcv(bars[["open", "high", "low", "close", "volume"]]),
                               "ashare_ts", code, "1d")
            if not (PIT / "daily_basic" / f"{sym}.parquet").exists():
                db = td.daily_basic(code)
                if len(db):
                    db.to_parquet(PIT / "daily_basic" / f"{sym}.parquet")
            if not (PIT / "fina" / f"{sym}.parquet").exists():
                fi = td.fina_indicator(code)
                if len(fi):
                    fi.to_parquet(PIT / "fina" / f"{sym}.parquet")
        except Exception as e:  # noqa: BLE001
            print(f"[{i}/{len(union)}] FAIL {code}: {str(e)[:60]}", flush=True)
        if i % 25 == 0:
            print(f"[{i}/{len(union)}] ...", flush=True)

    # -- part 3: north-bound flows -------------------------------------------
    hsgt_file = PIT / "moneyflow_hsgt.parquet"
    if not hsgt_file.exists():
        chunks = []
        for y in range(2014, 2027):
            df = td._call(td.pro.moneyflow_hsgt,
                          start_date=f"{y}0101", end_date=f"{y}1231")
            if len(df):
                chunks.append(df)
        pd.concat(chunks, ignore_index=True).to_parquet(hsgt_file)
        print("north-bound flows saved", flush=True)
    print("DONE")


if __name__ == "__main__":
    main()
