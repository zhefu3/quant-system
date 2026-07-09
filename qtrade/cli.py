"""Command-line entry points.

    python -m qtrade.cli fetch    --symbol BTC/USDT --timeframe 5m --days 180
    python -m qtrade.cli coverage
    python -m qtrade.cli backtest --symbol BTC/USDT --timeframe 5m \
        --strategy dual_ma --param fast=20 --param slow=100
"""

from __future__ import annotations

import argparse

import pandas as pd

from .backtest.engine import Engine
from .backtest.report import render_text, save_markdown
from .data.adapters.crypto_ccxt import CryptoAdapter
from .data.store import BarStore
from .markets.rules import BY_NAME
from .strategies.dual_ma import DualMA
from .strategies.momentum import TSMomentum

STRATEGIES = {"dual_ma": DualMA, "ts_momentum": TSMomentum}


def cmd_fetch(args):
    adapter = CryptoAdapter()
    store = BarStore()
    start = pd.Timestamp.now("UTC") - pd.Timedelta(days=args.days)
    df = adapter.fetch_ohlcv(args.symbol, args.timeframe, start)
    p = store.save(df, adapter.market, args.symbol, args.timeframe)
    print(f"saved {len(df)} bars -> {p}")


def cmd_coverage(_args):
    cov = BarStore().coverage()
    print(cov.to_string() if len(cov) else "store is empty — run `fetch` first")


def cmd_backtest(args):
    params = {}
    for kv in args.param or []:
        k, v = kv.split("=", 1)
        params[k] = float(v) if "." in v else int(v)
    strategy = STRATEGIES[args.strategy](**params)

    bars = BarStore().load(args.market, args.symbol, args.timeframe)
    engine = Engine(BY_NAME[args.market])
    result = engine.run(strategy, bars, symbol=args.symbol, timeframe=args.timeframe)
    print(render_text(result))
    print(f"\nreport -> {save_markdown(result)}")


def main():
    p = argparse.ArgumentParser(prog="qtrade")
    sub = p.add_subparsers(dest="cmd", required=True)

    f = sub.add_parser("fetch", help="download bars into the local store")
    f.add_argument("--symbol", required=True)
    f.add_argument("--timeframe", default="5m")
    f.add_argument("--days", type=int, default=180)
    f.set_defaults(fn=cmd_fetch)

    c = sub.add_parser("coverage", help="show what data is on disk")
    c.set_defaults(fn=cmd_coverage)

    b = sub.add_parser("backtest", help="run a strategy on stored bars")
    b.add_argument("--market", default="crypto", choices=list(BY_NAME))
    b.add_argument("--symbol", required=True)
    b.add_argument("--timeframe", default="5m")
    b.add_argument("--strategy", required=True, choices=list(STRATEGIES))
    b.add_argument("--param", action="append", help="k=v, repeatable")
    b.set_defaults(fn=cmd_backtest)

    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
