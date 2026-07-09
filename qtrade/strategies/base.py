"""Strategy interface: a strategy maps bars -> target position, nothing else.

Strategies are engine-agnostic. They receive canonical OHLCV bars and return a
target position Series in {0, 1} (long/flat; -1 reserved for markets that allow
shorting). The position at bar t may only use information up to and including
bar t's close — the engine delays execution to bar t+1, so peeking at the
current bar's close is safe, peeking beyond it is a bug in the strategy.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

import pandas as pd


class Strategy(ABC):
    name: str = "strategy"

    @abstractmethod
    def target_position(self, bars: pd.DataFrame) -> pd.Series:
        """Return desired position per bar, aligned to bars.index, values in {0, 1}."""

    def describe(self) -> str:
        params = {k: v for k, v in vars(self).items() if not k.startswith("_")}
        return f"{self.name}({', '.join(f'{k}={v}' for k, v in params.items())})"
