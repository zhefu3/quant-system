"""Fetch CSI500 (中证500 小盘) constituents' daily bars — resumable, parallel."""
import sys
from multiprocessing import Pool
from pathlib import Path
import pandas as pd
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from qtrade.data.adapters.ashare_baostock import AShareAdapter
from qtrade.data.store import BarStore

START = pd.Timestamp("2021-07-01", tz="Asia/Shanghai")

def list_universe():
    import baostock as bs
    bs.login()
    rs = bs.query_zz500_stocks()
    codes = []
    while rs.next():
        codes.append(rs.get_row_data()[1])
    bs.logout()
    return [f"{c.split('.')[1]}.{c.split('.')[0].upper()}" for c in codes]

_adapter = None
def _init():
    global _adapter
    _adapter = AShareAdapter()

def _one(sym):
    store = BarStore()
    try:
        df = _adapter.fetch_ohlcv(sym, "1d", START)
        store.save(df, "ashare_csi500", sym, "1d")
        return f"ok {sym}"
    except Exception as e:
        return f"FAIL {sym}: {e}"

if __name__ == "__main__":
    store = BarStore()
    uni = list_universe()
    todo = [s for s in uni if not store.path("ashare_csi500", s, "1d").exists()]
    print(f"universe {len(uni)}, todo {len(todo)}", flush=True)
    with Pool(6, initializer=_init) as pool:
        for i, msg in enumerate(pool.imap_unordered(_one, todo), 1):
            if msg.startswith("FAIL") or i % 50 == 0:
                print(f"[{i}/{len(todo)}] {msg}", flush=True)
    print("DONE")
