"""E68-A: what did picking the 2019 winners in hindsight buy us?

The flagship Sharpe was earned on six coins that we chose knowing how the
next seven years went. This prices that choice: run the SAME frozen strategy
on the six largest coins as ranked ON 2019-06-30, and compare.

The ranking is archival, not remembered — CoinMarketCap's dated snapshot
(URL below), read once and frozen into PIT_TOP6. Two of the incumbent six
were nowhere near the top on that date (ADA #11, LINK #16), and two coins
that were (BCH #5, EOS #6) are absent from the incumbent book.

EOS is the hard part, and it is the point. Every venue we can reach has
delisted it, so the free data path erases exactly the asset whose fate the
experiment is meant to capture. Preregistered (log 2026-07-27): it is run as
four scenarios rather than dropped, and — the retracted claim — these are
stress scenarios, NOT bounds. Sharpe is not monotone in a missing asset's
path for a long/short vol-targeted book, so if the scenarios straddle the
verdict threshold the answer is INCONCLUSIVE_DATA_GAP, not the convenient one.

Usage: .venv/bin/python research/e68_pit_universe.py
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

# --- frozen inputs (do not edit without a preregistration amendment) --------
RANKING_SOURCE = "https://coinmarketcap.com/historical/20190630/"
# top 6 by market cap on 2019-06-30, ex-stablecoin (USDT #8 excluded)
PIT_TOP6 = ["BTC/USDT", "ETH/USDT", "XRP/USDT", "LTC/USDT", "BCH/USDT", "EOS/USDT"]
PIT_RANKS = {"BTC/USDT": 1, "ETH/USDT": 2, "XRP/USDT": 3, "LTC/USDT": 4,
             "BCH/USDT": 5, "EOS/USDT": 6, "ADA/USDT": 11, "LINK/USDT": 16}
INCUMBENT6 = ["BTC/USDT", "ETH/USDT", "XRP/USDT", "ADA/USDT", "LTC/USDT",
              "LINK/USDT"]
START = pd.Timestamp("2019-07-01", tz="UTC")
# EOS scenarios: terminal loss lands at the window's end; the monotone path
# decays to 1% of its opening price across the whole window
TERMINAL_FRACTION = 0.01
COARSE_HAIRCUT_BP = 25  # daily-resolution signal deserves worse fills


def load_panel(store: BarStore, symbols: list[str]) -> dict[str, pd.DataFrame]:
    out = {}
    for s in symbols:
        try:
            b = store.load("crypto", s, "1h")
        except Exception:
            continue
        b = b[b.index >= START]
        if len(b) > 8760:  # a year of hourly bars, else it cannot carry a window
            out[s] = b
    return out


def synth_eos(index: pd.DatetimeIndex, mode: str,
              observed: pd.Series | None = None) -> pd.DataFrame:
    """Build an EOS price path for a preregistered scenario."""
    if mode == "terminal_total_loss":
        px = pd.Series(1.0, index=index)
        px.iloc[-1] = TERMINAL_FRACTION          # solvent, then gone
    elif mode == "monotone_delisting":
        px = pd.Series(np.geomspace(1.0, TERMINAL_FRACTION, len(index)), index=index)
    elif mode == "observed_coarse":
        assert observed is not None
        px = observed.reindex(index).ffill().bfill()
    else:
        raise ValueError(mode)
    return pd.DataFrame({"open": px, "high": px, "low": px, "close": px,
                         "volume": 1e6}, index=index)


def fetch_eos_daily() -> pd.Series | None:
    """Independent low-frequency EOS history (Yahoo, not our venue path)."""
    try:
        import yfinance as yf
        d = yf.download("EOS-USD", start="2019-06-25", interval="1d",
                        progress=False, auto_adjust=True)
        if d is None or len(d) == 0:
            return None
        close = d["Close"]
        if isinstance(close, pd.DataFrame):
            close = close.iloc[:, 0]
        close.index = pd.DatetimeIndex(close.index).tz_localize("UTC") \
            if close.index.tz is None else close.index.tz_convert("UTC")
        return close.dropna()
    except Exception as e:
        print(f"  (yfinance EOS unavailable: {str(e)[:60]})")
        return None


def book(bars: dict[str, pd.DataFrame], rules=CRYPTO_PERP) -> dict:
    p = CRYPTO_CORE
    r = run_portfolio(p.strategy(), bars, rules, "1h", allocation="equal",
                      rebalance_eps=p.rebalance_eps, oos_fraction=0.0001).loc["full"]
    return {"sharpe": float(r["sharpe"]) if pd.notna(r["sharpe"]) else float("nan"),
            "ret": float(r.get("ann_return", np.nan)),
            "dd": float(r.get("max_dd", np.nan))}


def main() -> None:
    store = BarStore()
    print(f"E68-A · PIT ranking from {RANKING_SOURCE} (2019-06-30, ex-stablecoin)")
    print(f"  PIT top-6 : {[s.split('/')[0] for s in PIT_TOP6]}")
    print(f"  incumbent : {[s.split('/')[0] for s in INCUMBENT6]}"
          f"  (ADA ranked #{PIT_RANKS['ADA/USDT']}, "
          f"LINK #{PIT_RANKS['LINK/USDT']} on that date)\n")

    inc = load_panel(store, INCUMBENT6)
    missing_inc = [s for s in INCUMBENT6 if s not in inc]
    if missing_inc:
        print(f"  incumbent panel incomplete: {missing_inc} — abort")
        return
    start = max(b.index[0] for b in inc.values())
    inc = {s: b[b.index >= start] for s, b in inc.items()}
    base = book(inc)
    print(f"[control] incumbent six, from {start.date()}: "
          f"Sharpe {base['sharpe']:.3f}\n")

    avail = load_panel(store, PIT_TOP6)
    missing = [s for s in PIT_TOP6 if s not in avail]
    print(f"[E68-A] PIT six — obtainable {sorted(s.split('/')[0] for s in avail)}, "
          f"missing {[s.split('/')[0] for s in missing]}")
    if not missing:
        print("  (no data gap: single verdict)")
        r = book({s: b[b.index >= start] for s, b in avail.items()})
        print(f"  Sharpe {r['sharpe']:.3f} vs control {base['sharpe']:.3f}")
        return

    avail = {s: b[b.index >= start] for s, b in avail.items()}
    idx = avail["BTC/USDT"].index
    observed = fetch_eos_daily()

    results = {"missing_as_cash": book(avail)}  # the 6th slot sits in cash
    for mode in ("terminal_total_loss", "monotone_delisting", "observed_coarse"):
        if mode == "observed_coarse" and observed is None:
            print("  scenario observed_coarse: SKIPPED (no independent source)")
            continue
        rules = CRYPTO_PERP
        if mode == "observed_coarse":
            rules = CRYPTO_PERP.__class__(
                **{**CRYPTO_PERP.__dict__,
                   "slippage": CRYPTO_PERP.slippage + COARSE_HAIRCUT_BP / 1e4})
        panel = dict(avail)
        panel["EOS/USDT"] = synth_eos(idx, mode, observed)
        results[mode] = book(panel, rules)

    print(f"\n  {'scenario':22s} {'Sharpe':>8s} {'vs control':>11s}")
    for k, v in results.items():
        print(f"  {k:22s} {v['sharpe']:8.3f} {v['sharpe'] - base['sharpe']:+11.3f}")

    spread = [v["sharpe"] for v in results.values()]
    print(f"\n  scenario spread: {min(spread):.3f} .. {max(spread):.3f}")
    print("  NOTE: scenarios are stress cases, NOT bounds — the truth need not"
          " lie between them (log 2026-07-27 retraction).")


if __name__ == "__main__":
    main()
