"""E27: perp-spot basis extremity fade — a third, distinct return source?

basis_t = swap_close / spot_close - 1. Persistent positive basis = crowded
longs paying funding; extreme readings mean-revert (the market's positioning
gauge). Signal: short when basis z-score > entry, long when < -entry, exit
inside |z| < exit_z. Discrete states on purpose — E25/E26 taught us that
continuous weights bleed to fees at our cost structure.

Gates: standalone IS/OOS sanity AND low correlation to crypto_core AND the
combined book must clear the pre-registered adoption bar (log 2026-07-10).
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qtrade.backtest.portfolio import run_portfolio  # noqa: E402
from qtrade.data.store import BarStore  # noqa: E402
from qtrade.markets.rules import CRYPTO_PERP  # noqa: E402
from qtrade.presets import CRYPTO_CORE  # noqa: E402
from qtrade.strategies.base import Strategy  # noqa: E402
from qtrade.strategies.overlays import VolTarget  # noqa: E402


class PrecomputedWeights(Strategy):
    """Wrap an externally computed weight series (e.g. multi-source signals
    that need more than one symbol's bars)."""

    name = "precomputed"

    def __init__(self, w: pd.Series, label: str = "precomputed"):
        self.w = w
        self.name = label

    def target_position(self, bars: pd.DataFrame) -> pd.Series:
        return self.w.reindex(bars.index).ffill().fillna(0.0)


def basis_fade_weights(spot: pd.Series, swap: pd.Series,
                       window: int = 336, entry_z: float = 2.0,
                       exit_z: float = 0.5) -> pd.Series:
    idx = spot.index.intersection(swap.index)
    basis = (swap.reindex(idx) / spot.reindex(idx) - 1.0)
    z = (basis - basis.rolling(window).mean()) / basis.rolling(window).std()
    zv = z.to_numpy()
    w = np.zeros(len(zv))
    state = 0.0
    for i in range(len(zv)):
        if np.isnan(zv[i]):
            continue
        if state == 0.0:
            if zv[i] >= entry_z:
                state = -1.0   # crowded longs -> fade short
            elif zv[i] <= -entry_z:
                state = 1.0
        elif abs(zv[i]) <= exit_z:
            state = 0.0
        w[i] = state
    return pd.Series(w, index=idx)


def main():
    store = BarStore()
    cov = store.coverage()
    swap_syms = sorted(cov[(cov["market"] == "crypto_swap")]["symbol"])
    print(f"swap data available: {swap_syms}")

    bars, wcols = {}, {}
    for sym in swap_syms:
        spot = store.load("crypto", sym, "1h")
        swap = store.load("crypto_swap", sym, "1h")
        w = basis_fade_weights(spot["close"], swap["close"])
        # per-symbol vol targeting, same convention as the core book
        realized = (spot["close"].pct_change().rolling(168).std()
                    * np.sqrt(8760)).reindex(w.index)
        scale = (0.4 / realized).clip(upper=1.0)
        wcols[sym] = (w * scale).fillna(0.0)
        bars[sym] = spot[spot.index >= w.index[0]]

    class MatrixBook:
        """Portfolio-level: serve the precomputed, equal-allocated matrix."""

        name = "basis_fade_book"

        def target_weights(self, closes: pd.DataFrame) -> pd.DataFrame:
            W = pd.DataFrame(wcols).reindex(closes.index).ffill().fillna(0.0)
            return (W / len(W.columns)).clip(-1.0, 1.0)

        def describe(self):
            return f"basis_fade_book({len(wcols)} symbols)"

    res = run_portfolio(MatrixBook(), bars, CRYPTO_PERP, "1h",
                        rebalance_eps=0.05, align="ffill")
    print("\n=== basis fade standalone book ===")
    print(res[["return_pct", "bench_ew_bh_pct", "edge_pp", "sharpe",
               "max_dd_pct", "trades", "fees"]].to_string())


if __name__ == "__main__":
    main()
