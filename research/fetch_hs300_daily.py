"""Parallel HS300 daily-bar fetch via baostock (one login per worker process).

baostock streams rows one at a time and is slow (~20s per 5y symbol); six
workers bring 300 names down to ~15-20 min. Already-stored symbols are skipped,
so the script is resumable.
"""

from __future__ import annotations

import sys
from multiprocessing import Pool
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qtrade.data.adapters.ashare_baostock import AShareAdapter  # noqa: E402
from qtrade.data.store import BarStore  # noqa: E402

START = pd.Timestamp("2021-07-01", tz="Asia/Shanghai")


def list_universe() -> list[str]:
    import baostock as bs

    bs.login()
    rs = bs.query_hs300_stocks()
    codes = []
    while rs.next():
        codes.append(rs.get_row_data()[1])  # sh.600000
    bs.logout()
    return [f"{c.split('.')[1]}.{c.split('.')[0].upper()}" for c in codes]


_adapter: AShareAdapter | None = None


def _init_worker():
    global _adapter
    _adapter = AShareAdapter()  # each process logs in lazily on first fetch


def _fetch_one(sym: str) -> str:
    store = BarStore()
    try:
        df = _adapter.fetch_ohlcv(sym, "1d", START)
        store.save(df, "ashare", sym, "1d")
        return f"ok {sym} {len(df)}"
    except Exception as e:  # noqa: BLE001
        return f"FAIL {sym}: {e}"


def main():
    store = BarStore()
    universe = list_universe()
    todo = [s for s in universe if not store.path("ashare", s, "1d").exists()]
    print(f"universe {len(universe)}, already stored {len(universe) - len(todo)}, fetching {len(todo)}")
    with Pool(6, initializer=_init_worker) as pool:
        for i, msg in enumerate(pool.imap_unordered(_fetch_one, todo), 1):
            if msg.startswith("FAIL") or i % 25 == 0:
                print(f"[{i}/{len(todo)}] {msg}", flush=True)
    print("DONE")


if __name__ == "__main__":
    main()
