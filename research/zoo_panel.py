"""Wide OHLCV panels for the factor zoo, on E47's exact universe/data.

Loads every ashare_ts daily bar series into wide frames (index=UTC date,
columns=ts_code) and derives the two extra columns some zoo alphas need:

  - amount: close * volume. A-share volume is stored in 手 (lots of 100);
    the missing x100 is a constant scale factor, irrelevant to every
    rank/corr-based alpha. Do NOT use this for absolute-turnover research.
  - vwap: (high + low + close) / 3 approximation — the store has no true
    intraday VWAP. Consistent across the cross-section, fine for ranks.

Reuses the HS300 point-in-time membership map exactly as ml_enhance.py
(E47) does, so zoo features and E47 features see the same universe.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qtrade.data.store import BarStore  # noqa: E402

PIT = Path(__file__).resolve().parents[1] / "data_store" / "pit_ts"
COLS = ["open", "high", "low", "close", "volume"]


def build_panel() -> tuple[dict[str, pd.DataFrame], pd.DataFrame]:
    """Return (panel, membership). panel maps column -> wide frame."""
    store = BarStore()
    weights = pd.read_parquet(PIT / "hs300_weights.parquet")
    union = sorted(weights["con_code"].unique())

    per_col: dict[str, dict[str, pd.Series]] = {c: {} for c in COLS}
    for code in union:
        try:
            bars = store.load("ashare_ts", code, "1d")
        except FileNotFoundError:
            continue
        for c in COLS:
            per_col[c][code] = bars[c]

    panel = {c: pd.DataFrame(d) for c, d in per_col.items()}
    ref = panel["close"]
    panel = {c: f.reindex(ref.index) for c, f in panel.items()}
    panel["amount"] = panel["close"] * panel["volume"]
    panel["vwap"] = (panel["high"] + panel["low"] + panel["close"]) / 3.0

    membership = weights.rename(columns={"trade_date": "snap", "con_code": "code"})
    membership["snap"] = pd.to_datetime(membership["snap"]).dt.strftime("%Y-%m-%d")
    return panel, membership


if __name__ == "__main__":
    panel, membership = build_panel()
    ref = panel["close"]
    print(f"panel: {ref.shape[1]} codes x {len(ref)} days "
          f"({ref.index[0].date()} -> {ref.index[-1].date()})")
    print(f"columns: {sorted(panel)}")
    print(f"membership snapshots: {membership['snap'].nunique()}")
