"""health: one command that says whether the machine is trustworthy right now.

Checks every stored bar series (integrity + freshness), the per-contract
futures store, and every paper book (heartbeat freshness + halt markers).
WARN lines are actionable; a clean run prints only PASS/INFO.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from ..data.store import BarStore
from ..presets import PRESETS
from .paper import DEFAULT_ROOT

# freshness tolerance per bar timeframe (calendar quirks: weekends, holidays)
STALE_AFTER = {"1h": pd.Timedelta(hours=12), "4h": pd.Timedelta(hours=24),
               "1d": pd.Timedelta(days=6)}
RECENT = 200  # bars inspected for NaN/gap checks


def _check_series(store: BarStore, market: str, symbol: str, tf: str,
                  now: pd.Timestamp) -> tuple[list[str], bool]:
    """(integrity issues, is_stale). Staleness is informational for research
    archives — the live paths fetch fresh data themselves — so it must never
    drown integrity WARNs in alert fatigue."""
    issues = []
    df = store.load(market, symbol, tf)
    tail = df.tail(RECENT)
    if not df.index.is_monotonic_increasing:
        issues.append("index not monotonic")
    if df.index.has_duplicates:
        issues.append(f"{int(df.index.duplicated().sum())} duplicate timestamps")
    nan_frac = float(tail.isna().any(axis=1).mean())
    if nan_frac > 0:
        issues.append(f"NaN rows in last {len(tail)}: {nan_frac:.1%}")
    if (tail["close"] <= 0).any():
        issues.append("non-positive closes")
    limit = STALE_AFTER.get(tf)
    stale = limit is not None and (now - df.index[-1]) > limit
    return issues, stale


def run_health() -> int:
    now = pd.Timestamp.now("UTC")
    store = BarStore()
    warns = 0
    print(f"══════ qtrade health · {now:%Y-%m-%d %H:%M} UTC ══════")

    print("\n--- bar store ---")
    cov = store.coverage()
    stale_count = 0
    for _, row in cov.iterrows():
        m, s, tf = row["market"], row["symbol"], row["timeframe"]
        try:
            issues, stale = _check_series(store, m, s, tf, now)
        except Exception as e:  # noqa: BLE001 — a broken file IS the finding
            issues, stale = [f"unreadable: {e}"], False
        for msg in issues:
            print(f"WARN  {m}/{s}/{tf}: {msg}")
        warns += len(issues)
        stale_count += stale
    print(f"{'PASS' if warns == 0 else '    '}  {len(cov)} series checked, "
          f"integrity issues above; {stale_count} research archives older than "
          "tolerance (INFO — live paths fetch fresh data)")

    print("\n--- contract store ---")
    from ..data.cn_futures import CONTRACT_DIR
    files = list(CONTRACT_DIR.glob("*.parquet"))
    marker = CONTRACT_DIR / ".last_refresh"
    mark = marker.read_text().strip() if marker.exists() else "never"
    print(f"INFO  {len(files)} contract files, last refresh {mark}")

    print("\n--- paper books ---")
    for name, preset in PRESETS.items():
        d = DEFAULT_ROOT / name
        eq_file = d / "equity.csv"
        if not eq_file.exists():
            print(f"INFO  {name}: no paper record")
            continue
        eq = pd.read_csv(eq_file, parse_dates=["ts"])
        last = pd.Timestamp(eq["ts"].max())
        last = last.tz_localize("UTC") if last.tzinfo is None else last.tz_convert("UTC")
        age_h = (now - last).total_seconds() / 3600
        tol_h = {"1h": 3, "4h": 6, "1d": 30}.get(preset.timeframe, 24)
        if age_h > tol_h:
            print(f"WARN  {name}: heartbeat stale, last mark {age_h:.1f}h ago "
                  f"(is the hourly loop running?)")
            warns += 1
        else:
            print(f"PASS  {name}: last mark {age_h:.1f}h ago, equity {eq['equity'].iloc[-1]:.2f}")
        if (d / "HALTED").exists():
            print(f"WARN  {name}: HALTED marker present — book is flattened, "
                  "needs human review")
            warns += 1

    print(f"\n{'ALL CLEAR' if warns == 0 else f'{warns} WARNING(S)'}")
    return warns
