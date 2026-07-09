"""Shared live signal path: paper and real execution MUST compute targets
identically — one function, two consumers."""

from __future__ import annotations

import pandas as pd

from ..data.adapters.crypto_ccxt import CryptoAdapter
from ..presets import BookPreset

WARMUP_BARS = 1100  # covers regime_window 720 + vol_window 168 + slack


def compute_targets(
    preset: BookPreset, adapter: CryptoAdapter | None = None
) -> tuple[dict[str, float], dict[str, float]]:
    """Fetch fresh bars and return ({symbol: target_weight}, {symbol: last_close}).

    Weights are the per-symbol strategy output scaled by equal allocation,
    computed on completed bars only (the in-progress bar is dropped).
    """
    adapter = adapter or CryptoAdapter()
    now = pd.Timestamp.now("UTC")
    tf_delta = pd.Timedelta(preset.timeframe)
    start = now - tf_delta * WARMUP_BARS

    closes, targets = {}, {}
    for sym in preset.symbols:
        bars = adapter.fetch_ohlcv(sym, preset.timeframe, start)
        bars = bars[bars.index + tf_delta <= now]  # completed bars only
        closes[sym] = float(bars["close"].iloc[-1])
        raw = preset.strategy().target_position(bars)
        targets[sym] = float(raw.iloc[-1]) / len(preset.symbols)  # equal alloc
    return targets, closes
