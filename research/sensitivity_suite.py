"""E16: overfitting audit for the slow trend + meanrev ensemble.

(a) Parameter perturbation: shift every knob ±25-50% — a real edge degrades
    gracefully; an overfit one falls off a cliff.
(b) Per-symbol consistency: params were never tuned per symbol, so the book
    should be roughly positive on most symbols individually.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qtrade.backtest.portfolio import run_portfolio  # noqa: E402
from qtrade.data.store import BarStore  # noqa: E402
from qtrade.markets.rules import CRYPTO_PERP  # noqa: E402
from qtrade.strategies.composite import Composite  # noqa: E402
from qtrade.strategies.cta import CTATrend  # noqa: E402
from qtrade.strategies.meanrev import BollingerRevert  # noqa: E402
from qtrade.strategies.overlays import VolTarget  # noqa: E402


def vt(strategy, tv=0.4):
    return VolTarget(strategy, target_vol=tv, vol_window=168, bars_per_year=8760)


def ensemble(horizons=(96, 288, 720), mr_window=96, entry_z=2.0, regime=720):
    trend = vt(CTATrend(h1=horizons[0], h2=horizons[1], h3=horizons[2]))
    meanrev = vt(BollingerRevert(window=mr_window, entry_z=entry_z, side="both",
                                 regime_window=regime))
    return Composite([(trend, 0.5), (meanrev, 0.5)])


def main():
    store = BarStore()
    cov = store.coverage()
    syms = sorted(cov[(cov["market"] == "crypto") & (cov["timeframe"] == "1h")]["symbol"])
    bars = {s: store.load("crypto", s, "1h") for s in syms}

    print("=== (a) parameter perturbation, full 3y portfolio ===")
    variants = {
        "base(96/288/720, mr96, z2.0, rg720, eps.05)": (ensemble(), 0.05),
        "horizons -25% (72/216/540)": (ensemble(horizons=(72, 216, 540)), 0.05),
        "horizons +25% (120/360/900)": (ensemble(horizons=(120, 360, 900)), 0.05),
        "regime 480": (ensemble(regime=480), 0.05),
        "regime 1080": (ensemble(regime=1080), 0.05),
        "mr window 72": (ensemble(mr_window=72), 0.05),
        "mr window 144": (ensemble(mr_window=144), 0.05),
        "entry_z 1.5": (ensemble(entry_z=1.5), 0.05),
        "entry_z 2.5": (ensemble(entry_z=2.5), 0.05),
        "eps 0.03": (ensemble(), 0.03),
        "eps 0.08": (ensemble(), 0.08),
    }
    rows = []
    for name, (strat, eps) in variants.items():
        res = run_portfolio(strat, bars, CRYPTO_PERP, "1h", allocation="equal",
                            rebalance_eps=eps)
        f, o = res.loc["full"], res.loc["out_of_sample"]
        rows.append({
            "variant": name,
            "full_ret_pct": f["return_pct"], "full_sharpe": f["sharpe"],
            "full_dd_pct": f["max_dd_pct"],
            "oos_ret_pct": o["return_pct"], "oos_dd_pct": o["max_dd_pct"],
            "fees": f["fees"],
        })
        print(pd.DataFrame(rows[-1:]).to_string(index=False, header=(len(rows) == 1)))

    print("\n=== (b) per-symbol consistency (base params) ===")
    rows = []
    for sym in syms:
        res = run_portfolio(ensemble(), {sym: bars[sym]}, CRYPTO_PERP, "1h",
                            allocation="equal", rebalance_eps=0.05)
        f = res.loc["full"]
        rows.append({"symbol": sym, "full_ret_pct": f["return_pct"],
                     "sharpe": f["sharpe"], "dd_pct": f["max_dd_pct"],
                     "bench_pct": f["bench_ew_bh_pct"]})
    df = pd.DataFrame(rows)
    print(df.to_string(index=False))
    print(f"\npositive symbols: {(df['full_ret_pct'] > 0).sum()}/{len(df)}")


if __name__ == "__main__":
    main()
