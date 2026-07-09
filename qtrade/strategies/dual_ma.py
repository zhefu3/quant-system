"""Dual moving-average crossover: long when fast MA above slow MA, flat otherwise."""

from __future__ import annotations

import pandas as pd

from .base import Strategy


class DualMA(Strategy):
    name = "dual_ma"

    def __init__(self, fast: int = 20, slow: int = 100):
        if fast >= slow:
            raise ValueError("fast window must be shorter than slow window")
        self.fast = fast
        self.slow = slow

    def target_position(self, bars: pd.DataFrame) -> pd.Series:
        fast_ma = bars["close"].rolling(self.fast).mean()
        slow_ma = bars["close"].rolling(self.slow).mean()
        pos = (fast_ma > slow_ma).astype(float)
        pos[slow_ma.isna()] = 0.0  # warm-up period: stay flat
        return pos
