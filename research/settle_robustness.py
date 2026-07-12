"""E57: does the cn_futures book survive settle-price execution? (prereg 2026-07-12)

Rebuilds the back-adjusted continuous series with settle everywhere close was
used (signals and fills alike), reruns the frozen E50b protocol, and compares.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qtrade.backtest.portfolio import run_portfolio  # noqa: E402
from qtrade.data.cn_futures import PRODUCTS, load_product, pick_main  # noqa: E402
from qtrade.markets.rules import CNFUTURES  # noqa: E402
from qtrade.strategies.cta import CTATrend  # noqa: E402
from qtrade.strategies.overlays import VolTarget  # noqa: E402


def settle_anchor(df: pd.DataFrame, dt: pd.Timestamp) -> float | None:
    sub = df.loc[:dt]
    if sub.empty:
        return None
    row = sub.iloc[-1]
    px = row["settle"] if pd.notna(row["settle"]) and row["settle"] > 0 else row["close"]
    return float(px) if pd.notna(px) and px > 0 else None


def stitch_settle(product: str) -> pd.DataFrame | None:
    contracts = load_product(product)
    if not contracts:
        return None
    sched = pick_main(contracts)
    rolls, factors, prev_c = [], [], None
    for dt, c in sched["contract"].items():
        if prev_c is not None and c != prev_c:
            prior = dt - pd.Timedelta(days=1)
            new_px, old_px = settle_anchor(contracts[c], prior), settle_anchor(contracts[prev_c], prior)
            rolls.append(dt)
            factors.append((new_px / old_px) if (new_px and old_px) else 1.0)
        prev_c = c
    cum, seg = 1.0, {}
    for dt, f in zip(reversed(rolls), reversed(factors)):
        cum *= f
        seg[dt] = cum
    bounds = sorted(seg)
    frames = []
    for dt, c in sched["contract"].items():
        row = contracts[c].loc[dt]
        px = row["settle"] if pd.notna(row["settle"]) and row["settle"] > 0 else row["close"]
        idx = next((b for b in bounds if dt < b), None)
        k = seg[idx] if idx is not None else 1.0
        frames.append((dt, px * k, px * k, px * k, px * k, row["volume"]))
    out = pd.DataFrame(frames, columns=["date", "open", "high", "low", "close", "volume"])
    out = out.set_index("date").sort_index()
    out.index = (out.index + pd.Timedelta(hours=15)).tz_localize("Asia/Shanghai").tz_convert("UTC")
    return out.astype(float)


def book():
    return VolTarget(CTATrend(h1=21, h2=63, h3=252), target_vol=0.30,
                     vol_window=63, bars_per_year=252)


def main():
    bars = {}
    for p in PRODUCTS:
        out = stitch_settle(p)
        if out is not None and len(out) >= 500:
            bars[p] = out
    start = max(b.index[0] for b in bars.values())
    bars = {s: b[b.index >= start] for s, b in bars.items()}
    print(f"settle panel: {len(bars)} products from {start.date()}")
    res = run_portfolio(book(), bars, CNFUTURES, "1d", allocation="equal",
                        rebalance_eps=0.02, align="ffill")
    print(res[["return_pct", "sharpe", "max_dd_pct", "trades"]].to_string())
    s = float(res.loc["full", "sharpe"])
    print(f"\nclose-version Sharpe 0.48 vs settle-version {s:.2f} "
          f"(gate: |diff| <= 0.15 -> {'ROBUST' if abs(s - 0.48) <= 0.15 else 'SENSITIVE'})")


if __name__ == "__main__":
    main()
