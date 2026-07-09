"""E23: the seven-year verdict on crypto_core's construction.

Every parameter in the book was chosen on 2023-2026 data. The 2019-2022
segment (COVID crash, 2020-21 double bull, 2022 bear) never touched any
tuning decision — it is pristine reverse out-of-sample. If the construction
only works on the data that raised it, this is where it dies.

Universe: the 6 majors with full 2019-07 history (BTC ETH XRP ADA LTC LINK).
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

MAJORS = ["BTC/USDT", "ETH/USDT", "XRP/USDT", "ADA/USDT", "LTC/USDT", "LINK/USDT"]


def show(res, label):
    print(f"\n=== {label} ===")
    print(res[["return_pct", "bench_ew_bh_pct", "edge_pp", "sharpe",
               "max_dd_pct", "trades", "fees"]].to_string())


def main():
    p = CRYPTO_CORE
    store = BarStore()
    bars = {s: store.load(p.market, s, p.timeframe) for s in MAJORS}
    start = max(b.index[0] for b in bars.values())
    bars = {s: b[b.index >= start] for s, b in bars.items()}
    n = len(next(iter(bars.values())))
    print(f"6-major panel: {n} bars, {start.date()} -> "
          f"{next(iter(bars.values())).index[-1].date()}")

    # Full seven years (IS/OOS split is chronological as usual).
    res = run_portfolio(p.strategy(), bars, CRYPTO_PERP, p.timeframe,
                        allocation="equal", rebalance_eps=p.rebalance_eps)
    show(res, "full 7y (6 majors)")

    # Pristine reverse-OOS: 2019-07 -> 2023-07 (no tuning ever saw this).
    cut = pd.Timestamp("2023-07-01", tz="UTC")
    pre = {s: b[b.index < cut] for s, b in bars.items()}
    res_pre = run_portfolio(p.strategy(), pre, CRYPTO_PERP, p.timeframe,
                            allocation="equal", rebalance_eps=p.rebalance_eps)
    show(res_pre, "PRISTINE 2019-07 -> 2023-07 (never seen by tuning)")

    # Year-by-year: where does it earn, where does it bleed?
    print("\n=== year by year ===")
    for year in range(2020, 2027):
        y0 = pd.Timestamp(f"{year}-01-01", tz="UTC")
        y1 = pd.Timestamp(f"{year + 1}-01-01", tz="UTC")
        yb = {s: b[(b.index >= y0 - pd.Timedelta(hours=1100)) & (b.index < y1)]
              for s, b in bars.items()}
        if min(len(b) for b in yb.values()) < 2000:
            continue
        r = run_portfolio(p.strategy(), yb, CRYPTO_PERP, p.timeframe,
                          allocation="equal", rebalance_eps=p.rebalance_eps,
                          oos_fraction=0.0001).loc["full"]
        print(f"{year}: ret {r['return_pct']:+7.2f}%  bench {r['bench_ew_bh_pct']:+8.2f}%  "
              f"dd {r['max_dd_pct']:5.1f}%  sharpe {r['sharpe']:5.2f}")


if __name__ == "__main__":
    main()
