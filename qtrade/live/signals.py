"""Shared live signal path: paper and real execution MUST compute targets
identically — one function, two consumers."""

from __future__ import annotations

import pandas as pd

from ..data.adapters import make_adapter
from ..presets import BookPreset

WARMUP_BARS = 1100  # covers regime_window 720 + vol_window 168 + slack


def _drop_in_progress(bars: pd.DataFrame, now: pd.Timestamp,
                      tf_delta: pd.Timedelta) -> pd.DataFrame:
    # open-stamped bars (crypto convention): complete once the next bar starts
    return bars[bars.index + tf_delta <= now]


def fetch_live_bars(preset: BookPreset, adapter=None) -> dict[str, pd.DataFrame]:
    """Fresh warm-up-deep bars per symbol, completed bars only."""
    adapter = adapter or make_adapter(preset.market)
    now = pd.Timestamp.now("UTC")
    tf_delta = pd.Timedelta(preset.timeframe)
    start = now - tf_delta * WARMUP_BARS
    drop = getattr(adapter, "drop_in_progress", _drop_in_progress)
    out = {}
    for sym in preset.symbols:
        bars = adapter.fetch_ohlcv(sym, preset.timeframe, start)
        out[sym] = drop(bars, now, tf_delta)
    return out


def compute_targets(
    preset: BookPreset,
    adapter=None,
    bars_by_symbol: dict[str, pd.DataFrame] | None = None,
) -> tuple[dict[str, float], dict[str, float]]:
    """Return ({symbol: target_weight}, {symbol: last_close}).

    Weights are the per-symbol strategy output scaled by equal allocation.
    """
    bars_by_symbol = bars_by_symbol or fetch_live_bars(preset, adapter)
    closes, targets = {}, {}
    for sym, bars in bars_by_symbol.items():
        closes[sym] = float(bars["close"].iloc[-1])
        raw = preset.strategy().target_position(bars)
        targets[sym] = float(raw.iloc[-1]) / len(preset.symbols)  # equal alloc
    return targets, closes
