"""Clean-room replication of crypto_core: same spec, none of the same code.

The reviewer's top-ranked institutional practice, with one aggravation this
repo must own: strategy, backtester, and audit scripts were all written by
the same AI, so common-cause bugs correlate MORE than for a human solo
author, not less. This file therefore imports nothing from qtrade — no
Strategy classes, no engine, no store. Prices come straight off parquet;
mechanics are re-derived from the frozen written spec below; the comparison
against the reference harness runs at the DAILY EQUITY level, where a real
divergence cannot hide.

Frozen spec (docs/external-review-brief.md §crypto_core, log E14-E23):
  trend leg    CTA vote over horizons 96/288/720h: sign(close - close[-h]),
               averaged -> position in [-1, 1]
  meanrev leg  Bollinger z = (close - MA96) / SD96; enter -sign(z) when
               |z| >= 2, exit at |z| < 0.5; both directions, but shorts only
               below MA720 and longs only above (regime filter)
  each leg     vol-targeted: 40% annualized over a 168h realized window,
               scale capped at 1
  combine      50/50 legs, equal 1/6 capital per symbol, weights clipped
  execution    rebalance throttle eps=0.05 per symbol, cost 10bp per unit
               |dW| (5bp fee + 5bp slip)

A divergence past tolerance is REPORTED, then investigated against the
reference — never silently reconciled; whatever the investigation finds goes
in the log either way.

Usage: .venv/bin/python research/clean_room.py [--source crypto|crypto_kucoin]
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SYMS = ["BTC_USDT", "ETH_USDT", "XRP_USDT", "ADA_USDT", "LTC_USDT", "LINK_USDT"]
START = "2019-07-01"
FEE_SLIP = 0.0010          # per unit of |dW|, both legs of the trade
EPS = 0.05
ANN = np.sqrt(8760)


def load_closes(source: str) -> pd.DataFrame:
    cols = {}
    for s in SYMS:
        p = ROOT / "data_store" / source / s / "1h.parquet"
        df = pd.read_parquet(p)
        cols[s] = df["close"]
    px = pd.DataFrame(cols).dropna()
    return px[px.index >= START]


def leg_trend(close: pd.Series) -> pd.Series:
    votes = sum(np.sign(close - close.shift(h)) for h in (96, 288, 720))
    return (votes / 3.0).fillna(0.0)


def leg_meanrev(close: pd.Series) -> pd.Series:
    ma, sd = close.rolling(96).mean(), close.rolling(96).std()
    z = (close - ma) / sd
    regime_up = close > close.rolling(720).mean()
    raw = np.zeros(len(close))
    pos = 0.0
    zv, up = z.to_numpy(), regime_up.to_numpy()
    for i in range(len(close)):
        if not np.isfinite(zv[i]):
            raw[i] = pos = 0.0
            continue
        if pos == 0.0:
            if zv[i] <= -2.0 and up[i]:
                pos = 1.0
            elif zv[i] >= 2.0 and not up[i]:
                pos = -1.0
        elif abs(zv[i]) < 0.5:
            pos = 0.0
        elif pos > 0 and not up[i]:
            pos = 0.0   # regime flipped against an open long
        elif pos < 0 and up[i]:
            pos = 0.0
        raw[i] = pos
    return pd.Series(raw, index=close.index)


def vol_target(sig: pd.Series, close: pd.Series) -> pd.Series:
    realized = close.pct_change().rolling(168).std() * ANN
    scale = (0.4 / realized).clip(upper=1.0)
    return (sig * scale).fillna(0.0)


def run(source: str) -> pd.Series:
    px = load_closes(source)
    W = pd.DataFrame(index=px.index, columns=px.columns, dtype=float)
    for s in px.columns:
        c = px[s]
        w = 0.5 * vol_target(leg_trend(c), c) + 0.5 * vol_target(leg_meanrev(c), c)
        W[s] = w.clip(-1.0, 1.0) / len(px.columns)

    # eps throttle, per symbol
    Wv = W.to_numpy()
    held = np.zeros_like(Wv)
    prev = np.zeros(Wv.shape[1])
    for i in range(len(Wv)):
        move = np.abs(Wv[i] - prev)
        prev = np.where(move > EPS, Wv[i], prev)
        held[i] = prev
    held = pd.DataFrame(held, index=W.index, columns=W.columns)

    ret = (held.shift(1) * px.pct_change()).sum(axis=1)
    cost = held.diff().abs().sum(axis=1) * FEE_SLIP
    net = (ret - cost).fillna(0.0)
    eq = (1 + net).cumprod()
    daily = eq.resample("1D").last().pct_change().dropna()
    return daily


def summarize(daily: pd.Series, label: str) -> None:
    sr = daily.mean() / daily.std() * np.sqrt(365)
    eq = (1 + daily).cumprod()
    dd = (eq / eq.cummax() - 1).min()
    print(f"{label:16s} SR {sr:+.3f}  annRet {eq.iloc[-1]**(365/len(daily))-1:+.1%}  "
          f"maxDD {dd:+.1%}  ({len(daily)}d)")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="crypto")
    ap.add_argument("--syms", default=None,
                    help="comma-separated override (e.g. the 5 coins both venues carry)")
    args = ap.parse_args()
    if args.syms:
        global SYMS
        SYMS = args.syms.split(",")
    daily = run(args.source)
    summarize(daily, f"clean-room/{args.source}")
    tag = args.source + (f"_{len(SYMS)}c" if args.syms else "")
    out = ROOT / "outputs" / f"cleanroom_daily_{tag}.csv"
    daily.rename("ret").to_csv(out)
    print(f"daily returns -> {out}")


if __name__ == "__main__":
    main()
