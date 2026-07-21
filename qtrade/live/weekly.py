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
    from .exposure import cross_book_section

    cross_book_section()

    print()
    _cb_ipo_section()

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


def _cb_ipo_section():
    """转债打新提醒 — 竞品调研(2026-07-21)采纳的信息层功能: CB IPO 申购是
    国内散户的制度性福利(中签≈免费期权), 系统只负责别让你错过窗口; 申购是
    真实账户动作, 永远在用户手里。数据: akshare 申购日期, 拉不到时回退到
    月度缓存的 bonds.parquet(可能滞后, 如实标注)。"""
    from .timeouts import call_with_timeout

    cached = Path(__file__).resolve().parents[2] / "data_store" / "cn_cb" / "bonds.parquet"
    df, src = None, "live"
    try:
        import akshare as ak
        df = call_with_timeout(ak.bond_zh_cov, 60.0)
    except Exception:  # noqa: BLE001 — reminder must never break the digest
        if cached.exists():
            df, src = pd.read_parquet(cached), "cache(月度, 可能滞后)"
    print("--- 转债打新提醒（申购动作在你, 系统只盯窗口）---")
    if df is None:
        print("(数据源不可用)")
        return
    today = pd.Timestamp.now(tz="Asia/Shanghai").normalize().tz_localize(None)
    dates = pd.to_datetime(df["申购日期"], errors="coerce")
    week = df[(dates >= today) & (dates <= today + pd.Timedelta(days=7))]
    if not len(week):
        print(f"未来 7 天无新券申购 [{src}]")
        return
    for _, r in week.sort_values("申购日期").iterrows():
        print(f"  {r['申购日期']} {r['债券简称']}({r['债券代码']}) "
              f"规模 {r.get('发行规模', '?')}亿 评级 {r.get('信用评级', '?')} [{src}]")


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
