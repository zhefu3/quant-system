"""E64 data fetch: CSI500 point-in-time membership + missing member data.

Same pipeline and timestamp conventions as the HS300 PIT assets (E43/E46):
  - csi500_weights.parquet: monthly index_weight snapshots 2014-01..now
  - for union members missing from ashare_ts: per-symbol daily bars
  - for members missing daily_basic/fina: per-symbol PIT fundamentals

Single-threaded, 0.12s sleep between calls (HANDOFF hard rule). Idempotent:
re-running skips whatever is already on disk.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qtrade.data.store import BarStore  # noqa: E402

PIT = Path(__file__).resolve().parents[1] / "data_store" / "pit_ts"
SLEEP = 0.12

# series written raw by the buggy first version — must be overwritten with hfq
_Q = Path(__file__).resolve().parents[1] / "data_store" / "_quarantine_20260715"
FORCE_OVERWRITE = set((_Q / "raw_only_codes.txt").read_text().split()) \
    if (_Q / "raw_only_codes.txt").exists() else set()


def pro_api():
    import tushare as ts

    return ts.pro_api(os.environ["TUSHARE_TOKEN"])


def fetch_membership(pro) -> pd.DataFrame:
    out_file = PIT / "csi500_weights.parquet"
    if out_file.exists():
        old = pd.read_parquet(out_file)
        print(f"csi500_weights exists: {old['trade_date'].nunique()} snapshots")
        return old
    frames = []
    months = pd.period_range("2014-01", pd.Timestamp.now().strftime("%Y-%m"), freq="M")
    for m in months:
        start, end = m.to_timestamp().strftime("%Y%m%d"), m.to_timestamp(how="end").strftime("%Y%m%d")
        try:
            w = pro.index_weight(index_code="000905.SH", start_date=start, end_date=end)
            if w is not None and len(w):
                # one snapshot per month: keep the latest trade_date in month
                last = w["trade_date"].max()
                frames.append(w[w["trade_date"] == last])
        except Exception as e:  # noqa: BLE001
            print(f"  {m}: {str(e)[:60]}")
        time.sleep(SLEEP)
    df = pd.concat(frames, ignore_index=True)
    df.to_parquet(out_file)
    print(f"csi500_weights: {df['trade_date'].nunique()} snapshots, "
          f"{df['con_code'].nunique()} unique codes")
    return df


def fetch_members(pro, codes: list[str]):
    store = BarStore()
    n_bars = n_db = n_fi = 0
    for i, code in enumerate(codes):
        sym = code.replace(".", "_")
        # hfq bars via ts.pro_bar (store convention: 后复权 + Asia/Shanghai
        # midnight stamps -> UTC, matching fetch_tushare_pit exactly).
        # 2026-07-15 incident: the first version used raw pro.daily and UTC
        # stamps — both HANDOFF conventions violated; OVERWRITE, never merge.
        bar_file = store.path("ashare_ts", code, "1d")
        needs = (not bar_file.exists()) or code in FORCE_OVERWRITE
        if needs:
            try:
                import tushare as ts

                d = ts.pro_bar(ts_code=code, adj="hfq", start_date="20130101")
                if d is not None and len(d):
                    d = d.sort_values("trade_date").rename(columns={"vol": "volume"})
                    d["ts"] = pd.to_datetime(d["trade_date"])
                    d = d.set_index("ts").sort_index()
                    d.index = d.index.tz_localize("Asia/Shanghai").tz_convert("UTC")
                    bar_file.parent.mkdir(parents=True, exist_ok=True)
                    d[["open", "high", "low", "close", "volume"]].astype(
                        "float64").to_parquet(bar_file)
                    n_bars += 1
            except Exception:  # noqa: BLE001
                pass
            time.sleep(SLEEP)
        f_db = PIT / "daily_basic" / f"{sym}.parquet"
        if not f_db.exists():
            try:
                db = pro.daily_basic(ts_code=code, start_date="20130101",
                                     fields="ts_code,trade_date,pe_ttm,pb,total_mv,turnover_rate")
                if db is not None and len(db):
                    db.sort_values("trade_date").reset_index(drop=True).to_parquet(f_db)
                    n_db += 1
            except Exception:  # noqa: BLE001
                pass
            time.sleep(SLEEP)
        f_fi = PIT / "fina" / f"{sym}.parquet"
        if not f_fi.exists():
            try:
                fi = pro.fina_indicator(ts_code=code,
                                        fields="ts_code,ann_date,end_date,roe,netprofit_yoy")
                if fi is not None and len(fi):
                    fi.to_parquet(f_fi)
                    n_fi += 1
            except Exception:  # noqa: BLE001
                pass
            time.sleep(SLEEP)
        if (i + 1) % 100 == 0:
            print(f"  {i + 1}/{len(codes)} members (new bars {n_bars}, db {n_db}, fina {n_fi})")
    print(f"done: +{n_bars} bar series, +{n_db} daily_basic, +{n_fi} fina")


def main():
    pro = pro_api()
    w = fetch_membership(pro)
    union = sorted(set(w["con_code"].unique()))
    print(f"csi500 historical union: {len(union)} codes")
    fetch_members(pro, union)


if __name__ == "__main__":
    main()
