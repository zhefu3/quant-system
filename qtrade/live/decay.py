"""Decay state machine: mechanical health labels for every paper book.

Borrowed from Vibe-Trading's strategy_store/decay.py, re-based on what a
paper record supports (rolling Sharpe vs the book's validated backtest
reference, plus live drawdown vs reference maxDD). Thresholds are FROZEN —
the point is to replace "eyeball the weekly report" with a rule that fires
the same way every time:

  immature   < WINDOW daily marks — say nothing, judge nothing
  healthy    rolling Sharpe >= 0.5 x reference AND live DD <= 1.0 x ref maxDD
  warning    either condition breached
  decayed    rolling Sharpe < 0 (while reference is positive)
             OR live DD > 1.25 x ref maxDD  (dd_halt fires at 1.5x)

Two consecutive WEEKLY warnings => a review trigger is printed. State
history persists in outputs/paper/<book>/decay.json.

Books whose reference Sharpe is <= 0 (futures_ibkr: the gate FAILED) get the
DD check only — a ratio against a negative baseline is meaningless.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from .paper import DEFAULT_ROOT
from .report import BACKTEST_REF
from .stats import _ann_sharpe, daily_returns

WINDOW = 60          # rolling daily marks
SHARPE_RATIO_WARN = 0.5
DD_RATIO_WARN = 1.0
DD_RATIO_DECAY = 1.25
CONSECUTIVE_FOR_REVIEW = 2


def classify(returns, live_dd: float, ref: dict) -> tuple[str, list[str]]:
    """Pure logic: (state, reasons). `returns` = daily returns series."""
    if len(returns) < WINDOW:
        return "immature", [f"{len(returns)}/{WINDOW} marks"]
    roll = _ann_sharpe(np.asarray(returns[-WINDOW:], dtype=float))
    ref_sharpe = ref["ann_return"] / ref["ann_vol"] if ref["ann_vol"] else 0.0
    dd_ratio = abs(live_dd) / ref["max_dd"] if ref["max_dd"] else 0.0
    reasons = []

    if dd_ratio > DD_RATIO_DECAY:
        return "decayed", [f"live DD {live_dd:.1%} = {dd_ratio:.2f}x ref maxDD"]
    if ref_sharpe > 0 and roll < 0:
        return "decayed", [f"rolling sharpe {roll:.2f} < 0 vs ref {ref_sharpe:.2f}"]

    if dd_ratio > DD_RATIO_WARN:
        reasons.append(f"live DD {live_dd:.1%} = {dd_ratio:.2f}x ref maxDD")
    if ref_sharpe > 0 and roll < SHARPE_RATIO_WARN * ref_sharpe:
        reasons.append(f"rolling sharpe {roll:.2f} < 0.5x ref {ref_sharpe:.2f}")
    return ("warning", reasons) if reasons else \
        ("healthy", [f"rolling sharpe {roll:.2f}, DD ratio {dd_ratio:.2f}"])


def _book_state(name: str, ref: dict) -> tuple[str, list[str]] | None:
    import pandas as pd

    eq_file = DEFAULT_ROOT / name / "equity.csv"
    if not eq_file.exists():
        return None
    rets = daily_returns(eq_file)
    eq = pd.read_csv(eq_file)["equity"]
    live_dd = float((eq / eq.cummax() - 1).min())
    return classify(rets, live_dd, ref)


def run_decay() -> dict[str, str]:
    """Weekly entry point: print the table, persist history, fire triggers."""
    out = {}
    print("--- 衰减状态机（阈值冻结, 见 qtrade/live/decay.py）---")
    for name, ref in BACKTEST_REF.items():
        res = _book_state(name, ref)
        if res is None:
            continue
        state, reasons = res
        out[name] = state
        hist_file = DEFAULT_ROOT / name / "decay.json"
        hist = json.loads(hist_file.read_text()) if hist_file.exists() else []
        hist.append({"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                     "state": state})
        hist_file.write_text(json.dumps(hist[-52:], indent=2))
        recent = [h["state"] for h in hist[-CONSECUTIVE_FOR_REVIEW:]]
        icon = {"healthy": "PASS", "immature": "INFO",
                "warning": "WARN", "decayed": "FAIL"}[state]
        print(f"{icon}  {name}: {state} — {'; '.join(reasons)}")
        if state == "decayed":
            print(f"      -> 制度触发: {name} 进入 decayed, 需按预注册标准复审")
        elif recent.count("warning") >= CONSECUTIVE_FOR_REVIEW:
            print(f"      -> 制度触发: {name} 连续 {CONSECUTIVE_FOR_REVIEW} 周 warning, "
                  "触发复审")
    return out
