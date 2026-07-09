"""Composite: a risk-weighted sum of strategy legs on the same symbol.

The institutional construction — no single all-weather signal exists, so hold
a book of low-correlation return streams. Weights are summed and clipped to
[-1, 1]; each leg should already be vol-targeted so the scales mean risk, not
notional.
"""

from __future__ import annotations

import pandas as pd

from .base import Strategy


class Composite(Strategy):
    name = "composite"

    def __init__(self, legs: list[tuple[Strategy, float]]):
        if not legs:
            raise ValueError("composite needs at least one leg")
        self.legs = legs

    def target_position(self, bars: pd.DataFrame) -> pd.Series:
        total = None
        for strat, scale in self.legs:
            w = strat.target_position(bars) * scale
            total = w if total is None else total.add(w, fill_value=0.0)
        return total.clip(-1.0, 1.0).fillna(0.0)

    def describe(self) -> str:
        inner = " + ".join(f"{s:.2f}*{leg.describe()}" for leg, s in self.legs)
        return f"composite({inner})"

    def explain(self, bars: pd.DataFrame) -> dict:
        legs = [{"scale": s, **leg.explain(bars)} for leg, s in self.legs]
        return {"name": self.name, "legs": legs,
                "target": round(float(self.target_position(bars).iloc[-1]), 4)}
