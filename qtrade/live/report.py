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

    _print_regime_context(p)


def run_ab(name_a: str, name_b: str):
    """Side-by-side paper records — the arbiter for parallel-preset promotion."""
    print(f"=== paper A/B: {name_a} vs {name_b} ===")
    rows = []
    for name in (name_a, name_b):
        f = DEFAULT_ROOT / name / "equity.csv"
        if not f.exists():
            print(f"{name}: no record yet")
            continue
        eq = pd.read_csv(f, parse_dates=["ts"]).drop_duplicates("ts").set_index("ts")["equity"]
        days = max((eq.index[-1] - eq.index[0]).total_seconds() / 86400, 1e-9)
        dd = float((eq / eq.cummax() - 1).min())
        rows.append({"preset": name, "days": round(days, 1),
                     "pnl_pct": round((eq.iloc[-1] / eq.iloc[0] - 1) * 100, 3),
                     "max_dd_pct": round(dd * 100, 3), "marks": len(eq)})
    if rows:
        print(pd.DataFrame(rows).set_index("preset").to_string())
        if len(rows) == 2 and min(r["days"] for r in rows) < 60:
            print("\n提示: 晋升裁决需要 ≥1 个季度的并行记录, 现在的差异只是噪声。")


def _print_regime_context(preset):
    """Where does the current market sit vs the strategy's known weak regime?

    E23 showed the book bleeds in one-way melt-ups (2024: -14%) and earns in
    chop/bear. This prints the basket's trailing 90d return percentile vs the
    full stored history — context for reading live P&L, NOT a trading signal.
    """
    from ..data.store import BarStore

    try:
        store = BarStore()
        closes = {}
        for sym in preset.symbols:
            closes[sym] = store.load(preset.market, sym, preset.timeframe)["close"]
        df = pd.DataFrame(closes).dropna()
        basket = df.div(df.iloc[0]).mean(axis=1)
        win = 90 * 24
        r90 = basket.pct_change(win).dropna()
        cur = float(r90.iloc[-1])
        pct = float((r90 <= cur).mean()) * 100
        if pct >= 90:
            zone = "单边暴涨区 — 策略的已知弱势 regime (参照2024): 预期跑输基准甚至小亏"
        elif pct <= 10:
            zone = "深跌区 — 策略历史上相对基准最强的 regime"
        else:
            zone = "常态区间 — 策略的主场"
        print(f"\n--- regime context (as of stored data {df.index[-1]:%Y-%m-%d %H:%M}) ---")
        print(f"basket 90d return {cur:+.1%}, percentile {pct:.0f}% of 7y history -> {zone}")
    except Exception as e:  # noqa: BLE001 — context is best-effort, never block the report
        print(f"\n(regime context unavailable: {e})")
