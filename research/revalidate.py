"""Monthly revalidation protocol: rerun the live book's backtest on the
latest data and append the result to a dated history.

A strategy is a hypothesis with an expiry date. Run this monthly (and before
any capital decision):

    .venv/bin/python research/revalidate.py

Red flags to act on:
  - full-cycle Sharpe drops materially below the audit band (0.65-1.24)
  - the latest-90d segment departs from the paper record (assumption break)
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qtrade.backtest.portfolio import run_portfolio  # noqa: E402
from qtrade.data.adapters.crypto_ccxt import CryptoAdapter  # noqa: E402
from qtrade.data.store import BarStore  # noqa: E402
from qtrade.presets import CRYPTO_CORE  # noqa: E402

HISTORY = Path(__file__).parent / "revalidation_history.csv"


def main():
    p = CRYPTO_CORE
    store, adapter = BarStore(), CryptoAdapter()

    # Top up the store to now, then rerun the exact preset.
    for sym in p.symbols:
        last = store.load(p.market, sym, p.timeframe).index[-1]
        try:
            fresh = adapter.fetch_ohlcv(sym, p.timeframe, last - pd.Timedelta(hours=2))
            store.save(fresh, p.market, sym, p.timeframe)
        except Exception as e:  # noqa: BLE001 — a stale symbol shouldn't kill the run
            print(f"top-up failed for {sym}: {e}")

    bars = {s: store.load(p.market, s, p.timeframe) for s in p.symbols}
    res = run_portfolio(p.strategy(), bars, p.rules, p.timeframe,
                        allocation="equal", rebalance_eps=p.rebalance_eps)
    f = res.loc["full"]

    n_bars = len(next(iter(bars.values())))
    last90 = {s: b.iloc[-24 * 90 :] for s, b in bars.items()}
    r90 = run_portfolio(p.strategy(), last90, p.rules, p.timeframe,
                        allocation="equal", rebalance_eps=p.rebalance_eps).loc["full"]

    row = {
        "run_at": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "bars": n_bars,
        "full_ret_pct": f["return_pct"],
        "full_sharpe": f["sharpe"],
        "full_dd_pct": f["max_dd_pct"],
        "last90d_ret_pct": r90["return_pct"],
        "last90d_dd_pct": r90["max_dd_pct"],
    }
    pd.DataFrame([row]).to_csv(HISTORY, mode="a", header=not HISTORY.exists(), index=False)
    print(pd.DataFrame([row]).to_string(index=False))
    if HISTORY.exists():
        print(f"\nhistory -> {HISTORY}")
    band = (0.65, 1.24)
    sharpe = row["full_sharpe"]
    verdict = "PASS" if band[0] <= sharpe else "WARN: sharpe below audit band"
    print(f"{verdict} (audit band {band[0]}-{band[1]}, current {sharpe})")


if __name__ == "__main__":
    main()
