"""weekly: the one command to run each week — everything you need to know
about the book in 30 seconds, plus reminders when protocols are due."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from ..presets import PRESETS
from .paper import DEFAULT_ROOT
from .report import run_ab, run_report

REVAL_HISTORY = Path(__file__).resolve().parents[2] / "research" / "revalidation_history.csv"


def run_weekly():
    now = datetime.now(timezone.utc)
    print(f"══════ qtrade 周报 · {now:%Y-%m-%d} UTC ══════\n")

    run_report("crypto_core")
    print()
    run_report("cn_futures")
    print()
    run_ab("crypto_core", "crypto_core_v2")

    print()
    from .decay import run_decay

    run_decay()

    print()
    from .manual import weekly_section

    weekly_section()

    print()
    _llm_cost_section()

    print("\n--- 制度到期提醒 ---")
    if REVAL_HISTORY.exists():
        hist = pd.read_csv(REVAL_HISTORY, parse_dates=["run_at"])
        last = hist["run_at"].max()
        age = (now.replace(tzinfo=None) - last.to_pydatetime()).days
        flag = "⚠ 已到期" if age >= 30 else "ok"
        print(f"月度重校验: 上次 {last:%Y-%m-%d}（{age} 天前）[{flag}] -> research/revalidate.py")
    else:
        print("月度重校验: 从未运行 ⚠ -> research/revalidate.py")

    # Quarterly items keyed off the v2 A/B start (2026-07-10).
    ab_start = datetime(2026, 7, 9, 21, 56, tzinfo=timezone.utc)  # v2 record began
    days_ab = (now - ab_start).days
    print(f"季度重评(品种池+v2晋升): A/B 已并行 {days_ab} 天 / 90 天 "
          f"[{'⚠ 可裁决' if days_ab >= 90 else '继续积累'}]")

    # Paper record depth vs the live-money gate.
    eq_file = DEFAULT_ROOT / "crypto_core" / "equity.csv"
    if eq_file.exists():
        eq = pd.read_csv(eq_file, parse_dates=["ts"])
        days = (eq["ts"].max() - eq["ts"].min()).total_seconds() / 86400
        print(f"实盘前置(模拟盘≥30天): 已积累 {days:.1f} 天 "
              f"[{'✓ 达标' if days >= 30 else '未达标'}]")

    print()
    from .healthcheck import run_health
    run_health()


def _llm_cost_section():
    """E60 prereg guard: monthly llm_agents API spend vs the frozen $30 cap."""
    import json

    ddir = DEFAULT_ROOT / "llm_agents" / "decisions"
    if not ddir.exists():
        return
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    cost = 0.0
    for f in ddir.glob(f"{month}-*.json"):
        for u in json.loads(f.read_text()).get("usage", []):
            haiku = "haiku" in u.get("model", "")
            cost += u["in"] * (1 if haiku else 3) / 1e6 + u["out"] * (5 if haiku else 15) / 1e6
    flag = "⚠ 超预注册上限$30 → 降频或停(E60)" if cost > 30 else "ok"
    print(f"--- llm_agents API 成本 ---\n{month} 月累计 ~${cost:.2f} / $30 上限 [{flag}]")
