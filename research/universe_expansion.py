"""E28: does widening the universe 10 -> 16 improve the book?

Same strategy, same window (intersection of all 16 histories), only the
universe changes. Diversification is the one free lunch — but crypto is one
beta, so measure, don't assume. Mild selection bias on the record: the six
additions were picked for liquidity TODAY.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qtrade.backtest.portfolio import run_portfolio  # noqa: E402
from qtrade.data.store import BarStore  # noqa: E402
from qtrade.markets.rules import CRYPTO_PERP  # noqa: E402
from qtrade.presets import CRYPTO_CORE  # noqa: E402

EXTRA = ["UNI/USDT", "ATOM/USDT", "FIL/USDT", "NEAR/USDT", "TRX/USDT", "BCH/USDT"]


def main():
    p = CRYPTO_CORE
    store = BarStore()
    all_syms = p.symbols + EXTRA
    bars_all = {}
    for s in all_syms:
        try:
            bars_all[s] = store.load(p.market, s, p.timeframe)
        except FileNotFoundError:
            print(f"missing {s}, skipping")
    start = max(b.index[0] for b in bars_all.values())
    bars_all = {s: b[b.index >= start] for s, b in bars_all.items()}
    print(f"common window starts {start.date()}, {len(bars_all)} symbols")

    for label, syms in [("10-symbol base", p.symbols),
                        (f"{len(bars_all)}-symbol expanded", list(bars_all))]:
        bars = {s: bars_all[s] for s in syms if s in bars_all}
        res = run_portfolio(p.strategy(), bars, CRYPTO_PERP, p.timeframe,
                            allocation="equal", rebalance_eps=p.rebalance_eps)
        print(f"\n=== {label} ===")
        print(res[["return_pct", "bench_ew_bh_pct", "edge_pp", "sharpe",
                   "max_dd_pct", "trades", "fees"]].to_string())


if __name__ == "__main__":
    main()
