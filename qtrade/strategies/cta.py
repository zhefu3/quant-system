"""CTA-style trend following: an ensemble of EWMA crossovers.

One horizon is an opinion; an ensemble is a position. Each horizon h votes
sign(EWMA(h/4) - EWMA(h)); the weight is the average vote, so conviction is
graded in [-1, 1] instead of all-in/all-out. This is the public-literature
skeleton of what CTA desks run (before their execution and sizing secret sauce).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .base import Strategy


class CTATrend(Strategy):
    name = "cta_trend"

    def __init__(self, h1: int = 48, h2: int = 120, h3: int = 288, long_only: bool = False):
        self.h1, self.h2, self.h3 = h1, h2, h3
        self.long_only = long_only

    def target_position(self, bars: pd.DataFrame) -> pd.Series:
        close = bars["close"]
        votes = []
        for h in (self.h1, self.h2, self.h3):
            fast = close.ewm(span=max(2, h // 4), adjust=False).mean()
            slow = close.ewm(span=h, adjust=False).mean()
            votes.append(np.sign(fast - slow))
        w = pd.concat(votes, axis=1).mean(axis=1)
        if self.long_only:
            w = w.clip(lower=0.0)
        w.iloc[: max(self.h1, self.h2, self.h3)] = 0.0  # warm-up
        return w.fillna(0.0)
