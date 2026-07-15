"""E63 data repository: all listed+delisted convertible bonds (akshare).

Layout (data_store/cn_cb/):
  bonds.parquet            master list from bond_zh_cov (incl. delisted)
  daily/<code>.parquet     per-bond OHLCV (bond_zh_hs_cov_daily)
  value/<code>.parquet     per-bond 转股价值/溢价率 history (value_analysis)

Single-threaded + sleep (HANDOFF rule). Idempotent — reruns fill gaps only.
Endpoints are free/unstable: every per-bond failure is logged, never fatal.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ROOT = Path(__file__).resolve().parents[1] / "data_store" / "cn_cb"
SLEEP = 0.15


def fetch_list() -> pd.DataFrame:
    import akshare as ak

    f = ROOT / "bonds.parquet"
    ROOT.mkdir(parents=True, exist_ok=True)
    df = ak.bond_zh_cov()
    df.to_parquet(f)
    print(f"bonds.parquet: {len(df)} bonds, cols: {list(df.columns)[:12]}")
    return df


def _exchange_prefix(code: str) -> str:
    return f"sh{code}" if code.startswith(("11", "13")) else f"sz{code}"


def fetch_bonds(df: pd.DataFrame):
    import akshare as ak

    (ROOT / "daily").mkdir(exist_ok=True)
    (ROOT / "value").mkdir(exist_ok=True)
    codes = df["债券代码"].astype(str).tolist()
    ok_d = ok_v = 0
    for i, code in enumerate(codes):
        fd = ROOT / "daily" / f"{code}.parquet"
        if not fd.exists():
            try:
                d = ak.bond_zh_hs_cov_daily(symbol=_exchange_prefix(code))
                if d is not None and len(d):
                    d.to_parquet(fd)
                    ok_d += 1
            except Exception:  # noqa: BLE001
                pass
            time.sleep(SLEEP)
        fv = ROOT / "value" / f"{code}.parquet"
        if not fv.exists():
            try:
                v = ak.bond_zh_cov_value_analysis(symbol=code)
                if v is not None and len(v):
                    v.to_parquet(fv)
                    ok_v += 1
            except Exception:  # noqa: BLE001
                pass
            time.sleep(SLEEP)
        if (i + 1) % 100 == 0:
            print(f"  {i + 1}/{len(codes)} (daily +{ok_d}, value +{ok_v})")
    have_d = len(list((ROOT / 'daily').glob('*.parquet')))
    have_v = len(list((ROOT / 'value').glob('*.parquet')))
    print(f"repository: {have_d} daily series, {have_v} value series / {len(codes)} bonds")


if __name__ == "__main__":
    fetch_bonds(fetch_list())
