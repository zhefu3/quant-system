"""Risk overlays: wrap any strategy and reshape its weights.

VolTarget is the workhorse of institutional books: hold risk, not notional.
Exposure scales inversely with realized volatility so a position in a calm
regime is larger than the same signal in a storm, capped at max_weight
(no leverage by default).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .base import Strategy


class VolTarget(Strategy):
    name = "vol_target"

    def __init__(
        self,
        base: Strategy,
        target_vol: float = 0.30,      # annualized, e.g. 0.30 = 30%
        vol_window: int = 168,
        bars_per_year: float = 8760,   # 1h crypto default; pass per timeframe
        max_weight: float = 1.0,
    ):
        self.base = base
        self.target_vol = target_vol
        self.vol_window = vol_window
        self.bars_per_year = bars_per_year
        self.max_weight = max_weight

    def target_position(self, bars: pd.DataFrame) -> pd.Series:
        raw = self.base.target_position(bars)
        realized = (
            bars["close"].pct_change().rolling(self.vol_window).std()
            * np.sqrt(self.bars_per_year)
        )
        scale = (self.target_vol / realized).clip(upper=self.max_weight)
        w = (raw * scale).clip(-self.max_weight, self.max_weight)
        return w.fillna(0.0)

    def describe(self) -> str:
        return (
            f"vol_target({self.base.describe()}, tv={self.target_vol}, "
            f"win={self.vol_window}, cap={self.max_weight})"
        )


def with_vol_target(base_cls, **vt_kwargs):
    """Class factory: `base_cls` wrapped in VolTarget, still grid-scannable
    (grid/walk-forward instantiate by keyword params, so the overlay must be
    baked into a class, not an instance)."""

    class Wrapped(Strategy):
        name = f"{base_cls.name}+vt"

        def __init__(self, **params):
            self._impl = VolTarget(base_cls(**params), **vt_kwargs)

        def target_position(self, bars: pd.DataFrame) -> pd.Series:
            return self._impl.target_position(bars)

        def describe(self) -> str:
            return self._impl.describe()

    return Wrapped
