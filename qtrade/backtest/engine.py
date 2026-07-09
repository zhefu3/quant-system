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


def _enforce_t_plus_one(pos: pd.Series, tz: str) -> pd.Series:
    """Delay any same-trading-day exit to the next day's first bar.

    `pos` is already in execution terms (post-shift): pos[i]==1 means we hold
    during bar i. A buy filling on date D cannot be sold until date > D.
    """
    dates = pos.index.tz_convert(tz).date
    out = pos.to_numpy().copy()
    holding = False
    entry_date = None
    for i in range(len(out)):
        if not holding:
            if out[i] == 1.0:
                holding, entry_date = True, dates[i]
        else:
            if out[i] == 0.0:
                if dates[i] == entry_date:
                    out[i] = 1.0  # forced hold: T+1 forbids same-day exit
                else:
                    holding = False
    return pd.Series(out, index=pos.index)


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
        pfs = self.portfolios(strategy, bars, timeframe, oos_fraction)
        segments = [
            SegmentResult(label=label, stats=pf.stats(), portfolio=pf)
            for label, pf in pfs.items()
        ]
        return BacktestResult(
            strategy=strategy.describe(), symbol=symbol, timeframe=timeframe, segments=segments
        )

    def portfolios(
        self,
        strategy: Strategy,
        bars: pd.DataFrame,
        timeframe: str = "?",
        oos_fraction: float = 0.3,
    ) -> dict:
        """Simulate and return {segment_label: vbt.Portfolio}.

        Positions are computed on the FULL window before slicing, so the
        out-of-sample segment keeps indicator warm-up context — but its trades
        are simulated on out-of-sample bars only.
        """
        pos = strategy.target_position(bars).reindex(bars.index).fillna(0.0)
        if not pos.isin([-1.0, 0.0, 1.0]).all():
            raise ValueError("target_position values must be in {-1, 0, 1}")
        if (pos < 0).any() and not self.rules.allow_short:
            raise ValueError(
                f"strategy emits short positions but market '{self.rules.market}' "
                "disallows shorting (use e.g. crypto_perp rules)"
            )

        # Execute on the NEXT bar: what you decide at t's close fills at t+1.
        pos = pos.shift(1).fillna(0.0)

        if self.rules.t_plus_one:
            pos = _enforce_t_plus_one(pos, self.rules.tz)

        split = int(len(bars) * (1 - oos_fraction))
        slices = {"full": slice(None)}
        if 0 < oos_fraction < 1:
            slices["in_sample"] = slice(None, split)
            slices["out_of_sample"] = slice(split, None)

        pfs = {}
        for label, sl in slices.items():
            seg_bars, seg_pos = bars.iloc[sl], pos.iloc[sl]
            prev = seg_pos.shift(1).fillna(0.0)
            pfs[label] = vbt.Portfolio.from_signals(
                seg_bars["close"],
                entries=(seg_pos == 1) & (prev != 1),
                exits=(seg_pos != 1) & (prev == 1),
                short_entries=(seg_pos == -1) & (prev != -1),
                short_exits=(seg_pos != -1) & (prev == -1),
                fees=self.rules.fee_rate,
                slippage=self.rules.slippage,
                init_cash=self.init_cash,
                freq=timeframe if timeframe != "?" else None,
            )
        return pfs
