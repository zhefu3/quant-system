"""E25: replace the single-parameter meanrev leg with a neighborhood ensemble.

Instead of betting on (window=96, z=2.0), average six neighbors:
{72,96,144} x {1.75,2.25}. Parameter diversification is the cheap version of
robustness: if the edge is a plateau, the ensemble keeps it while cutting the
variance of any single cell; if the ensemble collapses, the plateau was thin.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qtrade.backtest.portfolio import run_portfolio  # noqa: E402
from qtrade.data.store import BarStore  # noqa: E402
from qtrade.markets.rules import CRYPTO_PERP  # noqa: E402
from qtrade.presets import CRYPTO_CORE  # noqa: E402
from qtrade.strategies.composite import Composite  # noqa: E402
from qtrade.strategies.cta import CTATrend  # noqa: E402
from qtrade.strategies.meanrev import BollingerRevert  # noqa: E402
from qtrade.strategies.overlays import VolTarget  # noqa: E402


def vt(s):
    return VolTarget(s, target_vol=0.4, vol_window=168, bars_per_year=8760)


def ensemble_book():
    trend = vt(CTATrend(h1=96, h2=288, h3=720))
    mrs = [
        vt(BollingerRevert(window=w, entry_z=z, side="both", regime_window=720))
        for w in (72, 96, 144)
        for z in (1.75, 2.25)
    ]
    legs = [(trend, 0.5)] + [(m, 0.5 / len(mrs)) for m in mrs]
    return Composite(legs)


def main():
    p = CRYPTO_CORE
    store = BarStore()
    bars = {s: store.load(p.market, s, p.timeframe) for s in p.symbols}
    start = max(b.index[0] for b in bars.values())
    bars = {s: b[b.index >= start] for s, b in bars.items()}

    for name, strat in [("base (single mr 96/2.0)", p.strategy()),
                        ("mr param ensemble 3x2", ensemble_book())]:
        res = run_portfolio(strat, bars, CRYPTO_PERP, p.timeframe,
                            allocation="equal", rebalance_eps=p.rebalance_eps)
        print(f"\n=== {name} ===")
        print(res[["return_pct", "bench_ew_bh_pct", "edge_pp", "sharpe",
                   "max_dd_pct", "trades", "fees"]].to_string())


if __name__ == "__main__":
    main()
