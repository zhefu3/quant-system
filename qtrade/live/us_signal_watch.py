"""US signal-change watcher: when the frozen rules flip, the human hears.

Layer 1 of the US monitor (2026-08-04, user-requested): etf_trend's frozen
trend rules already produce a long/flat target for ten US ETFs every day —
this watcher diffs the latest signal snapshot against the previous session,
and on any flip pushes a macOS notification and appends a dated entry to the
digest file the daily brief reads.

This REPORTS what a preregistered system decided; it advises nothing. The
digest line carries the mechanical reason (trend votes) so the reader can
judge the signal, not just obey it.

State: outputs/us_watch_state.json (last seen per-symbol stance)
Digest: outputs/us_digest.md (append-only, newest first not required)
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from ..timeconv import utc_now
from .alerts import _notify

REPO = Path(__file__).resolve().parents[2]
SIGNALS = REPO / "outputs" / "paper" / "etf_trend" / "signals.csv"
STATE = REPO / "outputs" / "us_watch_state.json"
DIGEST = REPO / "outputs" / "us_digest.md"
# a stance is LONG when target weight clears this floor — tiny residual
# weights from vol scaling are noise, not conviction
LONG_EPS = 0.005


def current_stances() -> dict[str, str]:
    df = pd.read_csv(SIGNALS)
    df["ts"] = pd.to_datetime(df["ts"], format="mixed", utc=True)
    last = df[df["ts"] == df["ts"].max()]
    return {r["symbol"]: ("LONG" if float(r["target_w"]) > LONG_EPS else "FLAT")
            for _, r in last.iterrows()}


def run_watch() -> list[str]:
    """Diff stances vs state; notify + digest on flips. Returns flip lines."""
    if not SIGNALS.exists():
        return []
    cur = current_stances()
    prev = {}
    if STATE.exists():
        try:
            prev = json.loads(STATE.read_text()).get("stances", {})
        except Exception:
            prev = {}

    flips = []
    for sym, stance in sorted(cur.items()):
        old = prev.get(sym)
        if old is not None and old != stance:
            arrow = "转持有" if stance == "LONG" else "转现金"
            flips.append(f"{sym} {arrow} ({old}→{stance})")

    STATE.write_text(json.dumps(
        {"stances": cur, "ts": str(utc_now())}, indent=1))

    if flips:
        day = utc_now().strftime("%Y-%m-%d")
        n_long = sum(1 for s in cur.values() if s == "LONG")
        entry = (f"\n## {day} 信号变化\n"
                 + "".join(f"- {f}\n" for f in flips)
                 + f"- 当前持有 {n_long}/10:"
                 + " ".join(s for s, v in sorted(cur.items()) if v == "LONG")
                 + "\n- 性质:预注册系统 etf_trend 的机械信号(三周期趋势投票),"
                   "非投资建议;决策在人。\n")
        with DIGEST.open("a") as f:
            f.write(entry)
        _notify("qtrade 美股信号", "; ".join(flips)[:180])
    return flips


if __name__ == "__main__":
    flips = run_watch()
    print("; ".join(flips) if flips else "(no flips)")
