"""E50b data: per-contract dailies for domestic futures (sina, polite).

Contract grid is generated from naming convention (e.g. RB2410 = Oct 2024).
Missing contracts are skipped; resumable. Stitching into back-adjusted
continuous series happens in a separate step.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

OUT = Path(__file__).resolve().parents[1] / "data_store" / "cn_contracts"

PRODUCTS = ["RB", "I", "J", "M", "Y", "CF", "SR", "TA", "MA", "CU", "AL", "AU", "AG", "RU"]
YEARS = range(2014, 2028)


def main():
    import akshare as ak

    # optional argv: product codes + year range override, e.g.
    #   fetch_cn_contracts.py HC FG SA --years 2017 2028   (E55 expansion)
    products, years = PRODUCTS, YEARS
    argv = sys.argv[1:]
    if "--years" in argv:
        i = argv.index("--years")
        years = range(int(argv[i + 1]), int(argv[i + 2]))
        argv = argv[:i]
    if argv:
        products = argv

    OUT.mkdir(parents=True, exist_ok=True)
    grid = [f"{p}{y % 100:02d}{m:02d}" for p in products for y in years for m in range(1, 13)]
    todo = [c for c in grid if not (OUT / f"{c}.parquet").exists()]
    print(f"grid {len(grid)}, todo {len(todo)}", flush=True)
    ok = fail = 0
    for i, c in enumerate(todo, 1):
        try:
            df = ak.futures_zh_daily_sina(symbol=c)
            if df is not None and len(df) > 20:
                df.to_parquet(OUT / f"{c}.parquet")
                ok += 1
            else:
                fail += 1
        except Exception:  # noqa: BLE001 — contract doesn't exist: normal
            fail += 1
        if i % 100 == 0:
            print(f"[{i}/{len(todo)}] ok={ok} miss={fail}", flush=True)
        time.sleep(0.4)
    print(f"DONE ok={ok} miss={fail}")


if __name__ == "__main__":
    main()
