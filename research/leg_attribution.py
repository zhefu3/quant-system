"""E30: which leg earns when? Seven-year, year-by-year attribution.

Runs trend-only, meanrev-only, and the 50/50 book on the 6-major panel per
calendar year. Understanding, not tuning: no parameter changes downstream —
this informs expectations and future research directions.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qtrade.backtest.portfolio import run_portfolio  # noqa: E402
from qtrade.data.store import BarStore  # noqa: E402
from qtrade.markets.rules import CRYPTO_PERP  # noqa: E402
from qtrade.presets import CRYPTO_CORE  # noqa: E402
from qtrade.strategies.composite import Composite  # noqa: E402
from qtrade.strategies.cta import CTATrend  # noqa: E402
from qtrade.strategies.meanrev import BollingerRevert  # noqa: E402
from qtrade.strategies.overlays import VolTarget  # noqa: E402

MAJORS = ["BTC/USDT", "ETH/USDT", "XRP/USDT", "ADA/USDT", "LTC/USDT", "LINK/USDT"]


def vt(s):
    return VolTarget(s, target_vol=0.4, vol_window=168, bars_per_year=8760)


def books():
    trend = lambda: vt(CTATrend(h1=96, h2=288, h3=720))  # noqa: E731
    mr = lambda: vt(BollingerRevert(window=96, entry_z=2.0, side="both",  # noqa: E731
                                    regime_window=720))
    return {
        "trend": trend(),
        "meanrev": mr(),
        "book": Composite([(trend(), 0.5), (mr(), 0.5)]),
    }


def main():
    store = BarStore()
    bars = {s: store.load("crypto", s, "1h") for s in MAJORS}
    start = max(b.index[0] for b in bars.values())
    bars = {s: b[b.index >= start] for s, b in bars.items()}

    rows = []
    for year in range(2020, 2027):
        y0 = pd.Timestamp(f"{year}-01-01", tz="UTC")
        y1 = pd.Timestamp(f"{year + 1}-01-01", tz="UTC")
        yb = {s: b[(b.index >= y0 - pd.Timedelta(hours=1100)) & (b.index < y1)]
              for s, b in bars.items()}
        if min(len(b) for b in yb.values()) < 2000:
            continue
        row = {"year": year}
        for name, strat in books().items():
            r = run_portfolio(strat, yb, CRYPTO_PERP, "1h", allocation="equal",
                              rebalance_eps=CRYPTO_CORE.rebalance_eps,
                              oos_fraction=0.0001).loc["full"]
            row[f"{name}_ret"] = r["return_pct"]
            row[f"{name}_dd"] = r["max_dd_pct"]
        rows.append(row)
    df = pd.DataFrame(rows).set_index("year")
    print(df.to_string())
    print("\ncorrelation of yearly returns trend vs meanrev:",
          round(df["trend_ret"].corr(df["meanrev_ret"]), 2))


if __name__ == "__main__":
    main()
