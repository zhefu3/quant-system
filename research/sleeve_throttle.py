"""E31: throttle x composite interaction — sleeve-level vs book-level throttling.

E30 found the book != average of its legs: compositing halves each leg's
weights, pushing legitimate signal changes under the rebalance throttle.
Alternative architecture: throttle each sleeve at its own scale FIRST, then
sum the effective sleeve weights into the order stream (no second throttle).

PRE-REGISTERED (before running, see log 2026-07-10): adopt only if on BOTH
panels (3y 10-symbol AND 7y 6-major): Sharpe >= base+0.1, maxDD <= base+2pp,
fees <= 1.5x base. Otherwise: record and keep current architecture.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import vectorbt as vbt

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qtrade.backtest.engine import _throttle_rebalance  # noqa: E402
from qtrade.backtest.portfolio import run_portfolio  # noqa: E402
from qtrade.data.store import BarStore  # noqa: E402
from qtrade.markets.rules import CRYPTO_PERP  # noqa: E402
from qtrade.presets import CRYPTO_CORE  # noqa: E402
from qtrade.strategies.cta import CTATrend  # noqa: E402
from qtrade.strategies.meanrev import BollingerRevert  # noqa: E402
from qtrade.strategies.overlays import VolTarget  # noqa: E402

MAJORS = ["BTC/USDT", "ETH/USDT", "XRP/USDT", "ADA/USDT", "LTC/USDT", "LINK/USDT"]


def vt(s):
    return VolTarget(s, target_vol=0.4, vol_window=168, bars_per_year=8760)


def legs():
    return [vt(CTATrend(h1=96, h2=288, h3=720)),
            vt(BollingerRevert(window=96, entry_z=2.0, side="both", regime_window=720))]


def sleeve_book(bars: dict, sleeve_eps: float):
    """Simulate with per-sleeve throttling; returns quick metrics dict."""
    common = None
    for df in bars.values():
        common = df.index if common is None else common.intersection(df.index)
    closes = pd.DataFrame({s: df.loc[common, "close"] for s, df in bars.items()})
    n_sym = len(bars)

    eff_total = pd.DataFrame(0.0, index=common, columns=list(bars))
    for leg in legs():
        for sym, df in bars.items():
            w = leg.target_position(df.loc[common]).reindex(common).fillna(0.0)
            w = (w * 0.5 / n_sym).shift(1).fillna(0.0)          # scale + next-bar exec
            orders = _throttle_rebalance(w, sleeve_eps)          # per-sleeve throttle
            eff_total[sym] += orders.ffill().fillna(0.0)

    changed = eff_total.ne(eff_total.shift(1))
    orders_total = eff_total.where(changed, np.nan)
    orders_total.iloc[0] = eff_total.iloc[0]
    pf = vbt.Portfolio.from_orders(
        closes, size=orders_total, size_type="targetpercent", direction="both",
        fees=CRYPTO_PERP.fee_rate, slippage=CRYPTO_PERP.slippage,
        init_cash=10_000, freq="1h", group_by=True, cash_sharing=True)
    ret = float(pf.total_return()) * 100
    sharpe = float(pf.sharpe_ratio())
    return {"ret": round(ret, 2), "sharpe": round(sharpe, 2),
            "dd": round(float(pf.max_drawdown()) * -100, 2),
            "fees": round(float(pf.orders.fees.sum()), 1),
            "trades": int(pf.trades.count())}


def main():
    store = BarStore()
    p = CRYPTO_CORE
    panels = {}
    b10 = {s: store.load("crypto", s, "1h") for s in p.symbols}
    s10 = max(b.index[0] for b in b10.values())
    panels["10sym"] = {s: b[b.index >= s10] for s, b in b10.items()}
    b6 = {s: store.load("crypto", s, "1h") for s in MAJORS}
    s6 = max(b.index[0] for b in b6.values())
    panels["6maj-7y"] = {s: b[b.index >= s6] for s, b in b6.items()}

    for name, bars in panels.items():
        base = run_portfolio(p.strategy(), bars, CRYPTO_PERP, "1h",
                             allocation="equal", rebalance_eps=0.05,
                             oos_fraction=0.0001).loc["full"]
        print(f"\n=== panel {name} ===")
        print(f"base  book-throttle .05 : ret {base['return_pct']:+8.2f}%  "
              f"sharpe {base['sharpe']:5.2f}  dd {base['max_dd_pct']:5.1f}%  "
              f"fees {base['fees']:7.1f}  trades {base['trades']}")
        for eps in (0.025, 0.05):
            m = sleeve_book(bars, eps)
            print(f"sleeve-throttle {eps:5.3f}  : ret {m['ret']:+8.2f}%  "
                  f"sharpe {m['sharpe']:5.2f}  dd {m['dd']:5.1f}%  "
                  f"fees {m['fees']:7.1f}  trades {m['trades']}")


if __name__ == "__main__":
    main()
