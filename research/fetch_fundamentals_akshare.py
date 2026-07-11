"""E44b data: quarterly fundamental indicators via akshare (sina), politely.

ak.stock_financial_analysis_indicator gives per-stock quarterly indicators
(ROE, EPS growth, margins, leverage...) with report dates. We store raw and
let the factor layer enforce publication-lag discipline (use statDate+90d
as a conservative availability proxy; sina lacks pubDate).

Run ONLY after the bars backfill finishes — one polite stream at a time.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

PIT = Path(__file__).resolve().parents[1] / "data_store" / "pit"
OUT = PIT / "fundamentals_ak"


def main():
    import akshare as ak

    OUT.mkdir(parents=True, exist_ok=True)
    codes = sorted(pd.read_parquet(PIT / "hs300_membership.parquet")["code"].unique())
    todo = [c for c in codes
            if not (OUT / f"{c.replace('.', '_')}.parquet").exists()]
    print(f"fundamentals todo {len(todo)}/{len(codes)}", flush=True)
    fails = 0
    for i, code in enumerate(todo, 1):
        digits = code.split(".")[1]
        try:
            df = ak.stock_financial_analysis_indicator(symbol=digits, start_year="2014")
            if df is None or df.empty:
                fails += 1
                print(f"[{i}/{len(todo)}] EMPTY {code}", flush=True)
            else:
                df.to_parquet(OUT / f"{code.replace('.', '_')}.parquet")
        except Exception as e:  # noqa: BLE001
            fails += 1
            print(f"[{i}/{len(todo)}] FAIL {code}: {str(e)[:50]}", flush=True)
        if i % 25 == 0:
            print(f"[{i}/{len(todo)}] ...", flush=True)
        time.sleep(1.0)
    print(f"DONE fails={fails}")


if __name__ == "__main__":
    main()
