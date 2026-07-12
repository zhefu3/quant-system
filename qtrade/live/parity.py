"""parity: can the live book's last recorded signals be reproduced from data?

Research and execution share one signal path by construction, but construction
isn't proof. This replays the most recent tick: fetch bars as of that tick's
timestamp, recompute targets with the preset's strategy, and diff against what
signals.csv recorded. Drift beyond rounding means the deployed path and the
validated path have diverged — the exact failure mode the shared-preset
architecture exists to prevent. Run it before wiring any real executor.
"""

from __future__ import annotations

import pandas as pd

from ..data.adapters import make_adapter
from ..presets import PRESETS
from .paper import DEFAULT_ROOT
from .signals import WARMUP_BARS, _drop_in_progress, compute_targets

TOL = 5e-3  # |Δweight| beyond signals.csv's 4dp rounding = real drift


def run_parity(preset_name: str) -> bool:
    p = PRESETS[preset_name]
    sig_file = DEFAULT_ROOT / preset_name / "signals.csv"
    if not sig_file.exists():
        print(f"no signals recorded yet under {sig_file}")
        return False
    sig = pd.read_csv(sig_file, parse_dates=["ts"])
    last_ts = sig["ts"].max()
    recorded = (sig[sig["ts"] == last_ts].set_index("symbol")["target_w"] * 1.0)
    asof = pd.Timestamp(last_ts)
    asof = asof.tz_localize("UTC") if asof.tzinfo is None else asof.tz_convert("UTC")

    tf_delta = pd.Timedelta(p.timeframe)
    adapter = make_adapter(p.market)
    drop = getattr(adapter, "drop_in_progress", _drop_in_progress)
    bars = {}
    for s in p.symbols:
        raw = adapter.fetch_ohlcv(s, p.timeframe, asof - tf_delta * WARMUP_BARS, end=asof)
        bars[s] = drop(raw, asof, tf_delta)
    targets, _ = compute_targets(p, bars_by_symbol=bars)

    print(f"=== parity: {preset_name} @ {asof:%Y-%m-%d %H:%M} UTC ===")
    worst = 0.0
    for s in p.symbols:
        rec = float(recorded.get(s, 0.0))
        new = float(targets[s])
        d = abs(new - rec)
        worst = max(worst, d)
        if d > TOL:
            print(f"WARN  {s}: recorded {rec:+.4f} vs recomputed {new:+.4f} (Δ {d:.4f})")
    ok = worst <= TOL
    print(f"{'PASS' if ok else 'FAIL'}  max drift {worst:.4f} across {len(p.symbols)} symbols "
          f"(tolerance {TOL})")
    if not ok:
        print("signal path has diverged from the recorded tick — investigate before"
              " trusting any executor with real money.")
    return ok
