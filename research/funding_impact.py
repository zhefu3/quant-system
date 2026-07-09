"""E17: how much does perp funding actually cost the crypto_core book?

Funding P&L per event = -held_weight × funding_rate (as fraction of equity).
We overlay real Gate.io funding history (180d limit on the public endpoint)
onto the effective weights the backtest actually held, then annualize.

Also reports the drag under adverse multiples, since 180d only covers the
recent regime and bull-market funding runs hotter.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import ccxt
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qtrade.backtest.portfolio import run_portfolio  # noqa: E402
from qtrade.data.store import BarStore  # noqa: E402
from qtrade.presets import CRYPTO_CORE  # noqa: E402

FUNDING_DIR = Path(__file__).resolve().parents[1] / "data_store" / "funding"


def fetch_funding(symbols: list[str], days: int = 179) -> dict[str, pd.Series]:
    ex = ccxt.gate({"enableRateLimit": True})
    since = int((pd.Timestamp.now("UTC") - pd.Timedelta(days=days)).timestamp() * 1000)
    out = {}
    FUNDING_DIR.mkdir(parents=True, exist_ok=True)
    for sym in symbols:
        perp = f"{sym}:USDT"
        rows = []
        cursor = since
        while True:
            try:
                batch = ex.fetch_funding_rate_history(perp, since=cursor, limit=1000)
            except ccxt.NetworkError:
                time.sleep(2)
                continue
            if not batch:
                break
            rows.extend(batch)
            nxt = batch[-1]["timestamp"] + 1
            if nxt <= cursor or len(batch) < 2:
                break
            cursor = nxt
        s = pd.Series(
            {pd.Timestamp(r["timestamp"], unit="ms", tz="UTC"): float(r["fundingRate"])
             for r in rows}
        ).sort_index()
        s = s[~s.index.duplicated(keep="last")]
        out[sym] = s
        s.rename("rate").to_frame().to_parquet(FUNDING_DIR / f"{sym.replace('/', '_')}.parquet")
        print(f"{sym}: {len(s)} funding events, {s.index[0]} -> {s.index[-1]}, "
              f"mean {s.mean()*100:.4f}%/8h")
    return out


def main():
    p = CRYPTO_CORE
    store = BarStore()
    bars = {s: store.load(p.market, s, p.timeframe) for s in p.symbols}
    _, details = run_portfolio(
        p.strategy(), bars, p.rules, p.timeframe, allocation="equal",
        rebalance_eps=p.rebalance_eps, return_details=True,
    )
    weights = details["weights"]

    funding = fetch_funding(p.symbols)

    total_pnl_frac = 0.0
    per_sym = []
    window_days = None
    for sym, rates in funding.items():
        w = weights[sym].reindex(rates.index, method="ffill").fillna(0.0)
        pnl = (-w * rates).sum()  # fraction of equity over the overlap window
        overlap = rates.index[-1] - rates.index[0]
        window_days = max(window_days or overlap.days, overlap.days)
        total_pnl_frac += pnl
        per_sym.append({
            "symbol": sym,
            "events": len(rates),
            "mean_rate_pct_8h": round(rates.mean() * 100, 4),
            "avg_|w|": round(w.abs().mean(), 4),
            "funding_pnl_bp": round(pnl * 1e4, 1),
        })
    df = pd.DataFrame(per_sym)
    print("\n=== funding impact on crypto_core weights (overlap window) ===")
    print(df.to_string(index=False))
    ann = total_pnl_frac * (365 / window_days)
    print(f"\nwindow: {window_days}d | total funding P&L: {total_pnl_frac*100:+.3f}% "
          f"| annualized: {ann*100:+.2f}%/yr")
    for mult in (2, 4):
        print(f"adverse x{mult}: {ann*100*mult:+.2f}%/yr")


if __name__ == "__main__":
    main()
