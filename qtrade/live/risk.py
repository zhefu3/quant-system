"""Pre-trade risk gate: the layer between signals and orders.

Institutions never let a signal touch an order book unchecked. This gate is
shared by paper and real execution (one implementation, two consumers) and
enforces three things:

1. fresh data  — stale bars mean SKIP the tick, not trade on fiction;
2. bounds      — per-symbol and gross caps that normal operation never hits,
                 sized to contain software errors (vol~0 blowups, bad closes);
3. dd halt     — live drawdown beyond what the validated backtest ever saw
                 flattens the book and writes a HALTED marker; a human must
                 remove the marker to resume. Paper runs exercise the same
                 mechanism that will guard real money.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd


@dataclass(frozen=True)
class RiskLimits:
    max_weight: float = 0.25        # |target| per symbol, fraction of equity
    max_gross: float = 2.0          # sum of |targets|
    dd_halt: float = 0.25           # live peak-to-trough drawdown that halts
    max_data_age_bars: float = 6.0  # newest bar older than this many bars = stale

    def __post_init__(self):
        if not (0 < self.max_weight <= 1 and 0 < self.max_gross and
                0 < self.dd_halt < 1 and self.max_data_age_bars > 0):
            raise ValueError("nonsensical risk limits")


HALT_FILE = "HALTED"


class RiskGate:
    def __init__(self, limits: RiskLimits, state_dir: Path):
        self.limits = limits
        self.state_dir = Path(state_dir)

    # -- data freshness --------------------------------------------------------
    def stale_symbols(self, bars_by_symbol: dict[str, pd.DataFrame],
                      now: pd.Timestamp, timeframe: str) -> list[str]:
        max_age = pd.Timedelta(timeframe) * self.limits.max_data_age_bars
        return [s for s, b in bars_by_symbol.items()
                if b.empty or now - b.index[-1] > max_age]

    # -- halt marker ------------------------------------------------------------
    @property
    def halt_file(self) -> Path:
        return self.state_dir / HALT_FILE

    def is_halted(self) -> bool:
        return self.halt_file.exists()

    def trigger_halt(self, reason: str):
        self.halt_file.write_text(f"{pd.Timestamp.now('UTC')}\n{reason}\n"
                                  "Remove this file to resume trading.\n")

    # -- target transformation --------------------------------------------------
    def apply(self, targets: dict[str, float], live_dd: float) -> tuple[dict[str, float], list[str]]:
        """Clamp targets to bounds; flatten everything when halted.

        `live_dd` is the current peak-to-trough drawdown (negative number).
        Returns (adjusted_targets, notes) — notes are for the audit trail.
        """
        notes = []
        lm = self.limits

        if not self.is_halted() and live_dd < -lm.dd_halt:
            self.trigger_halt(f"live drawdown {live_dd:.1%} beyond limit -{lm.dd_halt:.0%}")
            notes.append(f"HALT triggered: dd {live_dd:.1%} < -{lm.dd_halt:.0%}")
        if self.is_halted():
            notes.append(f"halted ({self.halt_file}) — flattening, no new risk")
            return {s: 0.0 for s in targets}, notes

        out = {}
        for s, w in targets.items():
            if abs(w) > lm.max_weight:
                notes.append(f"{s}: weight {w:+.3f} clamped to ±{lm.max_weight}")
                w = lm.max_weight if w > 0 else -lm.max_weight
            out[s] = w
        gross = sum(abs(w) for w in out.values())
        if gross > lm.max_gross:
            scale = lm.max_gross / gross
            notes.append(f"gross {gross:.2f} > {lm.max_gross} — scaled by {scale:.2f}")
            out = {s: w * scale for s, w in out.items()}
        return out, notes
