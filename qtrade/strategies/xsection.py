"""Cross-sectional momentum rotation: hold the relatively strongest symbols.

Unlike time-series strategies (one symbol at a time), this ranks the whole
universe: every `rebalance` bars, long the top_k symbols by trailing return
(equal split), optionally short the bottom_k. Relative strength is a distinct
return source from both trend and mean reversion — that's why it earns a slot.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


class XSectionMomentum:
    """Portfolio-level strategy: emits a weight DataFrame over aligned closes."""

    name = "xs_momentum"

    def __init__(
        self,
        lookback: int = 168,
        top_k: int = 3,
        rebalance: int = 24,
        long_short: bool = False,
        gross: float = 1.0,  # total gross exposure budget
    ):
        self.lookback = lookback
        self.top_k = top_k
        self.rebalance = rebalance
        self.long_short = long_short
        self.gross = gross

    def target_weights(self, closes: pd.DataFrame) -> pd.DataFrame:
        mom = closes.pct_change(self.lookback)
        w = pd.DataFrame(0.0, index=closes.index, columns=closes.columns)

        rb_mask = np.zeros(len(closes), dtype=bool)
        rb_mask[self.lookback :: self.rebalance] = True

        for i in np.flatnonzero(rb_mask):
            row = mom.iloc[i].dropna()
            if len(row) < self.top_k * 2:
                continue
            ranked = row.sort_values(ascending=False)
            top = w.columns.get_indexer(ranked.index[: self.top_k])
            if self.long_short:
                bottom = w.columns.get_indexer(ranked.index[-self.top_k :])
                per_side = self.gross / 2
                w.iloc[i, top] = per_side / self.top_k
                w.iloc[i, bottom] = -per_side / self.top_k
            else:
                w.iloc[i, top] = self.gross / self.top_k

        # Hold weights between rebalances.
        w[~rb_mask] = np.nan
        return w.ffill().fillna(0.0)

    def describe(self) -> str:
        return (
            f"xs_momentum(lookback={self.lookback}, top_k={self.top_k}, "
            f"rebalance={self.rebalance}, long_short={self.long_short})"
        )
