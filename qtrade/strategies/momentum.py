"""Time-series momentum with a volatility filter.

Long when trailing return over `lookback` bars is positive AND realized vol is
below its own trailing median (momentum works poorly in churn); flat otherwise.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .base import Strategy


class TSMomentum(Strategy):
    name = "ts_momentum"

    def __init__(
        self,
        lookback: int = 288,
        vol_window: int = 288,
        vol_filter: bool = True,
        allow_short: bool = False,
    ):
        self.lookback = lookback
        self.vol_window = vol_window
        self.vol_filter = vol_filter
        self.allow_short = allow_short

    def target_position(self, bars: pd.DataFrame) -> pd.Series:
        close = bars["close"]
        ret = close.pct_change(self.lookback)
        if self.allow_short:
            pos = pd.Series(np.sign(ret).fillna(0.0), index=bars.index)
        else:
            pos = (ret > 0).astype(float)

        if self.vol_filter:
            bar_ret = close.pct_change()
            vol = bar_ret.rolling(self.vol_window).std()
            vol_median = vol.rolling(self.vol_window * 4, min_periods=self.vol_window).median()
            calm = (vol <= vol_median) | vol_median.isna()
            pos = pos.where(calm, 0.0)

        warmup = max(self.lookback, self.vol_window if self.vol_filter else 0)
        pos.iloc[:warmup] = 0.0
        return pos.astype(float)
