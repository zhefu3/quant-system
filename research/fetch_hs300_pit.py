"""E43: point-in-time HS300 data pipeline — the survivorship fix.

Part 1  monthly membership snapshots 2014->now  (query_hs300_stocks(date))
Part 2  daily bars for the UNION of all historical members (incl. dropped)
Part 3  quarterly fundamentals (profit+growth) with pubDate for all members

Outputs under data_store/pit/. Resumable; run parts via --part.
"""

from __future__ import annotations

import argparse
import sys
from multiprocessing import Pool
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qtrade.data.adapters.ashare_baostock import AShareAdapter  # noqa: E402
from qtrade.data.store import BarStore  # noqa: E402

PIT = Path(__file__).resolve().parents[1] / "data_store" / "pit"
START_SNAP = "2014-01-31"


def part1_membership():
    import baostock as bs

    bs.login()
    dates = pd.date_range(START_SNAP, pd.Timestamp.now(), freq="ME")
    rows = []
    for d in dates:
        rs = bs.query_hs300_stocks(date=str(d.date()))
        while rs.next():
            snap_date, code, name = rs.get_row_data()
            rows.append({"snap": str(d.date()), "code": code, "name": name})
        print(f"{d.date()}: snapshot ok", flush=True)
    bs.logout()
    df = pd.DataFrame(rows)
    PIT.mkdir(parents=True, exist_ok=True)
    df.to_parquet(PIT / "hs300_membership.parquet")
    union = sorted(df["code"].unique())
    print(f"snapshots {df['snap'].nunique()}, union {len(union)} codes")


_adapter = None


def _init():
    global _adapter
    _adapter = AShareAdapter()


def _bars_one(code: str) -> str:
    sym = f"{code.split('.')[1]}.{code.split('.')[0].upper()}"
    store = BarStore()
    if store.path("ashare_pit", sym, "1d").exists():
        return f"skip {sym}"
    try:
        df = _adapter.fetch_ohlcv(sym, "1d", pd.Timestamp("2014-01-01", tz="Asia/Shanghai"))
        store.save(df, "ashare_pit", sym, "1d")
        return f"ok {sym} {len(df)}"
    except Exception as e:  # noqa: BLE001
        return f"FAIL {sym}: {e}"


def part2_bars():
    codes = sorted(pd.read_parquet(PIT / "hs300_membership.parquet")["code"].unique())
    with Pool(6, initializer=_init) as pool:
        for i, msg in enumerate(pool.imap_unordered(_bars_one, codes), 1):
            if msg.startswith("FAIL") or i % 50 == 0:
                print(f"[{i}/{len(codes)}] {msg}", flush=True)
    print("DONE bars")


def _fund_one(code: str) -> str:
    import baostock as bs

    out = []
    for year in range(2014, 2027):
        for q in (1, 2, 3, 4):
            try:
                rs = bs.query_profit_data(code=code, year=year, quarter=q)
                while rs.next():
                    out.append(dict(zip(rs.fields, rs.get_row_data())))
                rs = bs.query_growth_data(code=code, year=year, quarter=q)
                while rs.next():
                    row = dict(zip(rs.fields, rs.get_row_data()))
                    if out and out[-1].get("statDate") == row.get("statDate") \
                            and out[-1].get("code") == row.get("code"):
                        out[-1].update(row)
                    else:
                        out.append(row)
            except Exception:  # noqa: BLE001
                continue
    if not out:
        return f"FAIL {code}"
    pd.DataFrame(out).to_parquet(PIT / "fundamentals" / f"{code.replace('.', '_')}.parquet")
    return f"ok {code} {len(out)}"


def _fund_init():
    import baostock as bs

    bs.login()


def part3_fundamentals():
    (PIT / "fundamentals").mkdir(parents=True, exist_ok=True)
    codes = sorted(pd.read_parquet(PIT / "hs300_membership.parquet")["code"].unique())
    todo = [c for c in codes
            if not (PIT / "fundamentals" / f"{c.replace('.', '_')}.parquet").exists()]
    print(f"fundamentals todo {len(todo)}/{len(codes)}")
    with Pool(6, initializer=_fund_init) as pool:
        for i, msg in enumerate(pool.imap_unordered(_fund_one, todo), 1):
            if msg.startswith("FAIL") or i % 25 == 0:
                print(f"[{i}/{len(todo)}] {msg}", flush=True)
    print("DONE fundamentals")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--part", type=int, required=True, choices=[1, 2, 3])
    args = p.parse_args()
    {1: part1_membership, 2: part2_bars, 3: part3_fundamentals}[args.part]()
