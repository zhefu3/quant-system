"""rebalance-report: monthly rebalance quality for the CN paper books.

Built 2026-07-21 ahead of the first realistic rebalances (Aug): with
execution realism in place (suspensions / limit boards / deferred retries),
"did the rebalance execute cleanly?" becomes a measurable question. For the
month, per book: planned vs filled vs deferred orders, retry latencies, and
the cost of being locked out — a deferred order's eventual fill price vs the
original decision-day reference (the delay cost the frozen slippage model
never sees). Reads exec_log.csv / monthly/<M>.json / trades.csv; degrades
gracefully before the first realistic rebalance has produced them.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from .paper import DEFAULT_ROOT

BOOKS = ("ashare_ml", "cb_double_low")


def _month_key(month: str | None) -> str:
    return month or pd.Timestamp.now(tz="Asia/Shanghai").strftime("%Y-%m")


def run_rebal_report(month: str | None = None) -> None:
    mk = _month_key(month)
    print(f"══════ 换仓质量报告 · {mk} ══════")
    for book in BOOKS:
        d = DEFAULT_ROOT / book
        print(f"\n--- {book} ---")
        cache = d / "monthly" / f"{mk}.json"
        if not cache.exists():
            print(f"本月尚无决策记录 ({cache.name})")
            continue
        plan = json.loads(cache.read_text())
        n_target = len(plan.get("weights", {}))
        deferred = plan.get("deferred", [])
        print(f"目标持仓 {n_target} 只 | 决策日递延 {len(deferred)} 单"
              + (f": {', '.join(deferred[:6])}{' …' if len(deferred) > 6 else ''}"
                 if deferred else ""))

        exec_f = d / "exec_log.csv"
        if not exec_f.exists():
            print("exec_log 尚无记录（首次真实换仓后出现）")
            continue
        log = pd.read_csv(exec_f)
        log["ts"] = pd.to_datetime(log["ts"], format="mixed", utc=True)
        log = log[log["ts"].dt.strftime("%Y-%m") == mk]
        if not len(log):
            print(f"exec_log 本月无条目")
            continue

        by = log["status"].value_counts().to_dict()
        print("执行结果: " + " | ".join(f"{k}: {v}" for k, v in sorted(by.items())))

        # retry latency + lockout cost: eventual fill vs decision-day reference
        retries = log[log["status"] == "fill_retry"]
        if len(retries):
            first_try = log[log["retry"] == 0].set_index("symbol")["ts"].to_dict()
            ref0 = log[log["retry"] == 0].set_index("symbol")["ref_px"].to_dict()
            lat, cost = [], []
            for _, r in retries.iterrows():
                t0 = first_try.get(r["symbol"])
                if t0 is not None:
                    lat.append((r["ts"] - t0).total_seconds() / 86400)
                p0 = ref0.get(r["symbol"])
                if p0 and pd.notna(r.get("fill_px")):
                    cost.append((float(r["fill_px"]) / float(p0) - 1) * 1e4)
            if lat:
                print(f"递延成交: {len(retries)} 单, 等待中位 {pd.Series(lat).median():.1f} 天")
            if cost:
                s = pd.Series(cost)
                print(f"锁定期代价(成交价 vs 决策日参考, bp): 中位 {s.median():+.0f} "
                      f"/ 最差 {s.max():+.0f} — 冻结滑点模型看不见的部分")
        still = json.loads((d / "state.json").read_text()).get("pending", {}) \
            if (d / "state.json").exists() else {}
        if still:
            print(f"仍未成交: {len(still)} 单 ({', '.join(sorted(still)[:6])})")
