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
        params[k] = _parse_value(v)
    strategy = STRATEGIES[args.strategy](**params)

    bars = BarStore().load(args.market, args.symbol, args.timeframe)
    engine = Engine(BY_NAME[args.rules or args.market])
    result = engine.run(strategy, bars, symbol=args.symbol, timeframe=args.timeframe)
    print(render_text(result))
    print(f"\nreport -> {save_markdown(result)}")


def _parse_value(v: str):
    if v.lower() in ("true", "false"):
        return v.lower() == "true"
    return float(v) if "." in v else int(v)


def _parse_grid(pairs: list[str]) -> dict[str, list]:
    grid = {}
    for kv in pairs:
        k, vs = kv.split("=", 1)
        grid[k] = [_parse_value(v) for v in vs.split(",")]
    return grid


def cmd_scan(args):
    from .research import grid_scan, sensitivity_heatmap

    grid = _parse_grid(args.grid)
    fixed = {}
    for kv in args.param or []:
        k, v = kv.split("=", 1)
        fixed[k] = _parse_value(v)
    bars = BarStore().load(args.market, args.symbol, args.timeframe)
    rules = BY_NAME[args.rules or args.market]
    res = grid_scan(STRATEGIES[args.strategy], bars, grid, rules, args.timeframe, fixed=fixed)
    print(res.to_string())
    if len(grid) == 2:
        x, y = list(grid)
        safe = args.symbol.replace("/", "_")
        png = sensitivity_heatmap(
            res, x, y, out_path=f"outputs/sensitivity_{args.strategy}_{safe}_{args.timeframe}.png"
        )
        print(f"\nheatmap -> {png}")


def cmd_walkforward(args):
    from .research import walk_forward
    from .research.walkforward import wf_verdict

    grid = _parse_grid(args.grid)
    fixed = {}
    for kv in args.param or []:
        k, v = kv.split("=", 1)
        fixed[k] = _parse_value(v)
    bars = BarStore().load(args.market, args.symbol, args.timeframe)
    rules = BY_NAME[args.rules or args.market]
    wf = walk_forward(
        STRATEGIES[args.strategy], bars, grid, rules, args.timeframe,
        n_folds=args.folds, fixed=fixed,
    )
    print(wf.to_string())
    print("\n" + wf_verdict(wf))


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
    b.add_argument("--market", default="crypto", help="which store partition the bars live in")
    b.add_argument("--rules", default=None, choices=list(BY_NAME),
                   help="cost/constraint pack; defaults to --market")
    b.add_argument("--symbol", required=True)
    b.add_argument("--timeframe", default="5m")
    b.add_argument("--strategy", required=True, choices=list(STRATEGIES))
    b.add_argument("--param", action="append", help="k=v, repeatable")
    b.set_defaults(fn=cmd_backtest)

    for name, fn, extra in [
        ("scan", cmd_scan, "parameter grid scan + sensitivity heatmap"),
        ("walkforward", cmd_walkforward, "rolling train/test validation"),
    ]:
        g = sub.add_parser(name, help=extra)
        g.add_argument("--market", default="crypto")
        g.add_argument("--rules", default=None, choices=list(BY_NAME))
        g.add_argument("--symbol", required=True)
        g.add_argument("--timeframe", default="5m")
        g.add_argument("--strategy", required=True, choices=list(STRATEGIES))
        g.add_argument("--grid", action="append", required=True,
                       help="k=v1,v2,v3 (repeatable; scanned dimensions)")
        g.add_argument("--param", action="append", help="k=v fixed params (repeatable)")
        if name == "walkforward":
            g.add_argument("--folds", type=int, default=5)
        g.set_defaults(fn=fn)

    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
