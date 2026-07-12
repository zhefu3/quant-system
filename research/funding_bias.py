"""E56 measurement: how big is the unmodeled funding-rate bias in crypto_core?

Pulls 180 days of funding history (gate, free, no key) for the 10-symbol
universe, aligns it with the backtest's actually-held weights over the same
window, and prices the funding P&L the backtest ignores:

    funding_pnl(t) = sum_i  -w_i(t) * funding_rate_i(t)     (longs pay positive)

Measurement only — no signal, no gate. Output: annualized drag in bps and the
position-funding correlation story (trend books tend to be long exactly when
funding is positive, so the naive symbol-mean understates the true cost).
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import ccxt
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qtrade.backtest.portfolio import run_portfolio  # noqa: E402
from qtrade.data.store import BarStore  # noqa: E402
from qtrade.markets.rules import CRYPTO_PERP  # noqa: E402
from qtrade.presets import PRESETS  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "data_store" / "funding_gate.parquet"


def fetch_funding(symbols: list[str]) -> pd.DataFrame:
    ex = ccxt.gate({"enableRateLimit": True})
    ex.load_markets()
    since = int((pd.Timestamp.now("UTC") - pd.Timedelta(days=179)).timestamp() * 1000)
    frames = {}
    now_ms = int(pd.Timestamp.now("UTC").timestamp() * 1000)
    for s in symbols:
        rows, cursor = [], since
        while cursor < now_ms - 8 * 3600 * 1000:
            h = ex.fetch_funding_rate_history(f"{s}:USDT", since=cursor, limit=100)
            if not h:
                break
            rows.extend(h)
            nxt = h[-1]["timestamp"] + 1
            if nxt <= cursor:
                break
            cursor = nxt
            time.sleep(0.3)
        ser = pd.Series({pd.Timestamp(r["timestamp"], unit="ms", tz="UTC"): float(r["fundingRate"])
                         for r in rows}).sort_index()
        frames[s] = ser[~ser.index.duplicated()]
        print(f"{s}: {len(frames[s])} funding marks "
              f"({frames[s].index[0]:%m-%d} -> {frames[s].index[-1]:%m-%d}), "
              f"mean {frames[s].mean() * 3 * 365:+.1%}/yr", flush=True)
        time.sleep(0.5)
    return pd.DataFrame(frames)


def main():
    p = PRESETS["crypto_core"]
    if OUT.exists():
        fr = pd.read_parquet(OUT)
    else:
        fr = fetch_funding(p.symbols)
        fr.to_parquet(OUT)

    store = BarStore()
    bars = {s: store.load(p.market, s, p.timeframe) for s in p.symbols}
    start = fr.index.min() - pd.Timedelta(days=90)  # warmup before window
    bars = {s: b[b.index >= start] for s, b in bars.items()}
    _, d = run_portfolio(p.strategy(), bars, CRYPTO_PERP, "1h", allocation="equal",
                         rebalance_eps=p.rebalance_eps, return_details=True)
    W = d["weights"]

    # weights at each funding mark (funding accrues on the position held then)
    Wf = W.reindex(fr.index, method="ffill").dropna(how="all")
    fr = fr.reindex(Wf.index)
    pnl = -(Wf * fr).sum(axis=1)  # per-mark funding P&L as fraction of equity

    days = (pnl.index[-1] - pnl.index[0]).total_seconds() / 86400
    total = pnl.sum()
    ann_bps = total / days * 365 * 1e4
    print(f"\nwindow: {pnl.index[0]:%Y-%m-%d} -> {pnl.index[-1]:%Y-%m-%d} ({days:.0f}d)")
    print(f"funding P&L on backtest positions: {total * 1e4:+.1f} bps "
          f"({ann_bps:+.0f} bps/yr annualized)")
    print(f"mean |gross| at marks: {Wf.abs().sum(axis=1).mean():.2f}")

    naive = -(Wf.abs().sum(axis=1).mean() * fr.mean().mean()) * 3 * 365 * 1e4
    print(f"naive estimate (mean funding x mean gross): {naive:+.0f} bps/yr — "
          f"difference vs realized = position-funding correlation effect")

    for s in p.symbols:
        both = pd.DataFrame({"w": Wf[s], "f": fr[s]}).dropna()
        if len(both) > 20 and both["w"].std() > 0 and both["f"].std() > 0:
            print(f"  {s:10s} corr(w, funding) {both['w'].corr(both['f']):+.2f}  "
                  f"mean funding {fr[s].mean() * 3 * 365:+.1%}/yr")


if __name__ == "__main__":
    main()
