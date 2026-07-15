"""Manual-account benchmark: the user's discretionary record vs the books.

The eighth curve. The user reports one number per period (from the broker
statement, e.g. 同花顺 对账单); the weekly report puts it next to the paper
books. Six months from now "me vs the system" is two curves, not a feeling.

Nothing here is advice — it's a mirror.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

FILE = Path(__file__).resolve().parents[2] / "outputs" / "manual_account.csv"


def log_period(period: str, pnl_pct: float, bench_pct: float | None = None,
               note: str = "") -> None:
    row = {"recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "period": period, "pnl_pct": pnl_pct, "bench_pct": bench_pct, "note": note}
    pd.DataFrame([row]).to_csv(FILE, mode="a", header=not FILE.exists(), index=False)
    print(f"recorded: {period} {pnl_pct:+.2f}% "
          f"(bench {bench_pct:+.2f}%)" if bench_pct is not None else
          f"recorded: {period} {pnl_pct:+.2f}%")


def weekly_section() -> None:
    if not FILE.exists():
        print("--- 手动账户对照 ---\n(无记录; 每月: qtrade.cli manual-log "
              "--period 2026-07 --pnl-pct X --bench-pct Y)")
        return
    df = pd.read_csv(FILE)
    print("--- 手动账户对照（第八条曲线）---")
    for _, r in df.tail(6).iterrows():
        bench = f" vs 基准 {r['bench_pct']:+.2f}%" if pd.notna(r.get("bench_pct")) else ""
        alpha = (f"  [超额 {r['pnl_pct'] - r['bench_pct']:+.2f}pp]"
                 if pd.notna(r.get("bench_pct")) else "")
        print(f"{r['period']}: {r['pnl_pct']:+.2f}%{bench}{alpha}"
              f"{'  # ' + str(r['note']) if r.get('note') else ''}")
