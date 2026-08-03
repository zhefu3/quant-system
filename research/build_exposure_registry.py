"""Data Exposure Registry: which data has already answered which questions.

The reviewer's rule (adopted 2026-07-27): once a data segment has been used
to look, tune, or hypothesize, it can never again be sold as out-of-sample.
This derives the registry mechanically from the trial ledger — every trial
row stamps its panel — so the claim "this segment is unexposed" becomes
checkable instead of remembered.

Registry semantics per panel: exposed_by (experiments), first/last exposure,
and the standing consequence line. Future verdicts append here; the only
truly unexposed data is data that does not exist yet (the forward record)
and any future purchased source before its first query.

Usage: .venv/bin/python research/build_exposure_registry.py
"""

from __future__ import annotations

import json
from pathlib import Path

ART = Path(__file__).resolve().parents[1] / "research" / "artifacts"

PANEL_DESC = {
    "crypto": "OKX(+kucoin BNB) 1h spot bars, 2019-07 起(6 币)/2021-01 起(10 币)",
    "cn_fut": "新浪逐合约日线 2018-05 起, 14 品种(+E55 的 16 扩展品种)",
    "ashare": "A股日线(baostock/tushare hfq)+ HS300/CSI500 时点宇宙 + PIT 基本面",
    "us": "yfinance 美股/ETF 日线(SPY 1993 起, 各 ETF 按上市日)",
    "us_fut": "yfinance 连续合约 26 年 + IBKR CONTFUT(共同窗 2.5 年)",
    "cb": "akshare 可转债 1016 只含退市, 2007 起",
    "multi": "跨账本月收益(组合层)",
    "crypto_fwd": "llm_agents 前瞻记录(不可回测, 无历史暴露)",
}


def main() -> None:
    ledger = json.loads((ART / "trial_ledger.json").read_text())
    panels: dict[str, dict] = {}
    for row in ledger["entries"]:
        if not row["is_trial"]:
            continue
        tag = row["panel_tag"]
        p = panels.setdefault(tag, {"description": PANEL_DESC.get(tag, ""),
                                    "exposed_by": [], "n_trials": 0})
        p["exposed_by"].append(row["exp"])
        p["n_trials"] += 1

    registry = {
        "generated": "2026-08-03",
        "rule": ("任何数据段一旦被用于看图/调参/构思假设即为已暴露, 不得再称 OOS。"
                 "未暴露数据仅两类: 尚不存在的未来(前瞻记录)与未查询过的新购数据源。"
                 "新购数据到手先切保留段进 vault, 首次查询前登记于此。"),
        "panels": panels,
        "unexposed": {
            "forward_records": "九本纸面账的未来记录 — 事实上的 holdout",
            "vault": [],
        },
    }
    out = ART / "data_exposure.json"
    out.write_text(json.dumps(registry, ensure_ascii=False, indent=1))
    for tag, p in sorted(panels.items()):
        print(f"{tag:11s} {p['n_trials']:3d} trials  ({len(set(p['exposed_by']))} exps)")
    print(f"saved -> {out}")


if __name__ == "__main__":
    main()
