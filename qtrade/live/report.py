"""paper-report: is the live paper book behaving like its backtest said it would?

Compares the realized equity curve against the preset's backtest expectations
and prints explicit PASS/WARN verdicts. The point is to catch divergence early
— a live book that draws down deeper or churns harder than its backtest is a
broken assumption, not bad luck, until proven otherwise.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from ..presets import PRESETS
from .paper import DEFAULT_ROOT

# crypto_core full-cycle backtest reference (E15/E16, research/log.md).
BACKTEST_REF = {
    "crypto_core": {
        "ann_return": 0.14,     # ~14%/yr over the 3y cycle
        "ann_vol": 0.12,        # implied by sharpe ~1.16
        "max_dd": 0.152,        # full-cycle max drawdown
        "target_gross": 1.0,    # weights are vol-targeted; gross rarely near 1
    }
}


def run_report(preset_name: str, state_dir: str | None = None):
    p = PRESETS[preset_name]
    d = Path(state_dir) if state_dir else DEFAULT_ROOT / preset_name
    eq_file, state_file = d / "equity.csv", d / "state.json"
    if not eq_file.exists():
        print(f"no paper record yet under {d} — run `qtrade.cli paper` first")
        return

    eq = pd.read_csv(eq_file, parse_dates=["ts"]).drop_duplicates("ts").set_index("ts")
    state = json.loads(state_file.read_text()) if state_file.exists() else {}
    ref = BACKTEST_REF.get(preset_name, {})

    equity = eq["equity"]
    start_eq, cur_eq = equity.iloc[0], equity.iloc[-1]
    days = max((equity.index[-1] - equity.index[0]).total_seconds() / 86400, 1e-9)
    total_ret = cur_eq / start_eq - 1
    running_max = equity.cummax()
    dd = float((equity / running_max - 1).min())

    print(f"=== paper report: {preset_name} ===")
    print(f"period   : {equity.index[0]:%Y-%m-%d %H:%M} -> {equity.index[-1]:%Y-%m-%d %H:%M} UTC ({days:.1f}d)")
    print(f"equity   : {start_eq:.2f} -> {cur_eq:.2f}  ({total_ret:+.2%})")
    print(f"max dd   : {dd:.2%}")
    print(f"gross    : now {eq['gross_exposure'].iloc[-1]:.0%}, avg {eq['gross_exposure'].mean():.0%}")
    print(f"positions: {state.get('positions', {})}")
    fills = eq["n_fills"].sum()
    print(f"fills    : {int(fills)} total ({fills / days:.1f}/day)")

    if len(equity) >= 48:
        hourly_ret = equity.pct_change().dropna()
        ann_vol = float(hourly_ret.std() * np.sqrt(8760))
        print(f"ann vol  : {ann_vol:.1%} (realized, hourly marks)")
    else:
        ann_vol = None
        print("ann vol  : n/a (need >= 48 hourly marks)")

    if not ref:
        return
    print("\n--- vs backtest expectation ---")
    checks = []
    checks.append(("drawdown within backtest max",
                   abs(dd) <= ref["max_dd"],
                   f"live {dd:.1%} vs backtest max {-ref['max_dd']:.1%}"))
    if ann_vol is not None:
        checks.append(("realized vol <= 2x backtest",
                       ann_vol <= 2 * ref["ann_vol"],
                       f"live {ann_vol:.1%} vs backtest {ref['ann_vol']:.0%}"))
    if days >= 30:
        ann_ret = (1 + total_ret) ** (365 / days) - 1
        lower = ref["ann_return"] - 2 * ref["ann_vol"]
        checks.append(("annualized return above 2-sigma floor",
                       ann_ret >= lower,
                       f"live {ann_ret:+.1%} vs floor {lower:+.1%}"))
    else:
        print(f"(return check activates after 30d; {days:.1f}d so far)")
    for name, ok, detail in checks:
        print(f"{'PASS' if ok else 'WARN'}  {name}: {detail}")
    if any(not ok for _, ok, _ in checks):
        print("\nWARN present: investigate before adding capital or going live.")
