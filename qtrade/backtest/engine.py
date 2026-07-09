"""Backtest engine: thin, honest wrapper over vectorbt.

Non-negotiable defaults, enforced here rather than left to strategy authors:

1. **No lookahead**: the target position computed on bar t is executed on bar
   t+1 (positions are shifted by one bar before simulation).
2. **No free lunch**: fees and slippage come from MarketRules and must be > 0.
3. **Out-of-sample split**: every run reports in-sample and out-of-sample
   segments separately; judging a strategy on the full period only is how
   people fool themselves.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
import vectorbt as vbt

from ..markets.rules import MarketRules
from ..strategies.base import Strategy


@dataclass
class SegmentResult:
    label: str
    stats: pd.Series
    portfolio: object  # vbt.Portfolio; kept for plotting in research notebooks

    @property
    def summary(self) -> dict:
        s = self.stats
        return {
            "segment": self.label,
            "start": str(s["Start"]),
            "end": str(s["End"]),
            "total_return_pct": round(float(s["Total Return [%]"]), 2),
            "benchmark_return_pct": round(float(s["Benchmark Return [%]"]), 2),
            "max_drawdown_pct": round(float(s["Max Drawdown [%]"]), 2),
            "sharpe": round(float(s["Sharpe Ratio"]), 2),
            "trades": int(s["Total Trades"]),
            "win_rate_pct": round(float(s["Win Rate [%]"]), 2) if pd.notna(s["Win Rate [%]"]) else None,
            "total_fees": round(float(s["Total Fees Paid"]), 2),
        }


@dataclass
class BacktestResult:
    strategy: str
    symbol: str
    timeframe: str
    segments: list[SegmentResult]

    def to_frame(self) -> pd.DataFrame:
        return pd.DataFrame([seg.summary for seg in self.segments]).set_index("segment")


class Engine:
    def __init__(self, rules: MarketRules, init_cash: float = 10_000.0):
        self.rules = rules
        self.init_cash = init_cash

    def run(
        self,
        strategy: Strategy,
        bars: pd.DataFrame,
        symbol: str = "?",
        timeframe: str = "?",
        oos_fraction: float = 0.3,
    ) -> BacktestResult:
        """Backtest with a chronological in-sample / out-of-sample split."""
        pos = strategy.target_position(bars).reindex(bars.index).fillna(0.0)
        if not pos.isin([0.0, 1.0]).all():
            raise ValueError("target_position must be in {0,1} (long/flat) for now")

        # Execute on the NEXT bar: what you decide at t's close fills at t+1.
        pos = pos.shift(1).fillna(0.0)

        split = int(len(bars) * (1 - oos_fraction))
        segments = []
        for label, sl in [
            ("full", slice(None)),
            ("in_sample", slice(None, split)),
            ("out_of_sample", slice(split, None)),
        ]:
            seg_bars, seg_pos = bars.iloc[sl], pos.iloc[sl]
            entries = (seg_pos > 0) & (seg_pos.shift(1).fillna(0.0) == 0)
            exits = (seg_pos == 0) & (seg_pos.shift(1).fillna(0.0) > 0)
            pf = vbt.Portfolio.from_signals(
                seg_bars["close"],
                entries,
                exits,
                fees=self.rules.fee_rate,
                slippage=self.rules.slippage,
                init_cash=self.init_cash,
                freq=timeframe if timeframe != "?" else None,
            )
            segments.append(SegmentResult(label=label, stats=pf.stats(), portfolio=pf))

        return BacktestResult(
            strategy=strategy.describe(), symbol=symbol, timeframe=timeframe, segments=segments
        )
