"""Bollinger z-score mean reversion.

Enter when price stretches `entry_z` std devs from its rolling mean, exit on
reversion to the mean (z crosses 0). A trend veto (price vs long MA) keeps it
from catching falling knives in a strong trend. Weight scales with |z| up to 1.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .base import Strategy


class BollingerRevert(Strategy):
    name = "boll_revert"

    def __init__(
        self,
        window: int = 96,
        entry_z: float = 2.0,
        long_only: bool = True,
        trend_veto_window: int = 0,  # 0 = off; else skip longs below this MA
    ):
        self.window = window
        self.entry_z = entry_z
        self.long_only = long_only
        self.trend_veto_window = trend_veto_window

    def target_position(self, bars: pd.DataFrame) -> pd.Series:
        close = bars["close"]
        mean = close.rolling(self.window).mean()
        std = close.rolling(self.window).std()
        z = (close - mean) / std

        n = len(close)
        zv = z.to_numpy()
        veto = None
        if self.trend_veto_window:
            veto = (close < close.rolling(self.trend_veto_window).mean()).to_numpy()

        w = np.zeros(n)
        state = 0.0  # current side: 0 flat, +1 long-revert, -1 short-revert
        for i in range(n):
            if np.isnan(zv[i]):
                continue
            if state == 0.0:
                if zv[i] <= -self.entry_z and not (veto is not None and veto[i]):
                    state = 1.0
                elif zv[i] >= self.entry_z and not self.long_only:
                    state = -1.0
            elif state == 1.0 and zv[i] >= 0:
                state = 0.0  # reverted to mean
            elif state == -1.0 and zv[i] <= 0:
                state = 0.0
            w[i] = state
        return pd.Series(w, index=bars.index)
