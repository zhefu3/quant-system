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
from .data.adapters.ashare_baostock import AShareAdapter
from .data.adapters.crypto_ccxt import CryptoAdapter
from .data.adapters.us_yfinance import USAdapter
from .data.store import BarStore
from .markets.rules import BY_NAME
from .strategies.cta import CTATrend
from .strategies.dual_ma import DualMA
from .strategies.meanrev import BollingerRevert
from .strategies.momentum import TSMomentum
from .strategies.overlays import with_vol_target

STRATEGIES = {
    "dual_ma": DualMA,
    "ts_momentum": TSMomentum,
    "boll_revert": BollingerRevert,
    "cta_trend": CTATrend,
}

# Portfolio-level strategies (need the whole universe, portfolio command only).
from .strategies.xsection import XSectionMomentum  # noqa: E402

PORTFOLIO_STRATEGIES = {"xs_momentum": XSectionMomentum}


def _strategy_cls(args):
    cls = STRATEGIES[args.strategy]
    if getattr(args, "vol_target", None):
        cls = with_vol_target(
            cls,
            target_vol=args.vol_target,
            vol_window=args.vol_window,
            bars_per_year=args.bars_per_year,
        )
    return cls
ADAPTERS = {"crypto": CryptoAdapter, "ashare": AShareAdapter, "us": USAdapter}


def cmd_fetch(args):
    adapter = ADAPTERS[args.market]()
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
    strategy = _strategy_cls(args)(**params)

    bars = BarStore().load(args.market, args.symbol, args.timeframe)
    engine = Engine(BY_NAME[args.rules or args.market])
    result = engine.run(strategy, bars, symbol=args.symbol, timeframe=args.timeframe)
    print(render_text(result))
    print(f"\nreport -> {save_markdown(result)}")


def _parse_value(v: str):
    if v.lower() in ("true", "false"):
        return v.lower() == "true"
    try:
        return float(v) if "." in v else int(v)
    except ValueError:
        return v  # plain string param, e.g. side=long


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
    res = grid_scan(_strategy_cls(args), bars, grid, rules, args.timeframe, fixed=fixed)
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
        _strategy_cls(args), bars, grid, rules, args.timeframe,
        n_folds=args.folds, fixed=fixed,
    )
    print(wf.to_string())
    print("\n" + wf_verdict(wf))


def cmd_portfolio(args):
    from .backtest.portfolio import run_portfolio

    params = {}
    for kv in args.param or []:
        k, v = kv.split("=", 1)
        params[k] = _parse_value(v)
    if args.strategy in PORTFOLIO_STRATEGIES:
        strategy = PORTFOLIO_STRATEGIES[args.strategy](**params)
    else:
        strategy = _strategy_cls(args)(**params)

    store = BarStore()
    if args.symbols:
        symbols = args.symbols.split(",")
    else:
        cov = store.coverage()
        cov = cov[(cov["market"] == args.market) & (cov["timeframe"] == args.timeframe)]
        symbols = sorted(cov["symbol"])
    bars = {s: store.load(args.market, s, args.timeframe) for s in symbols}
    rules = BY_NAME[args.rules or args.market]
    res = run_portfolio(
        strategy, bars, rules, args.timeframe, allocation=args.allocation
    )
    print(f"strategy : {strategy.describe()}")
    print(f"universe : {', '.join(symbols)}  ({args.timeframe}, alloc={args.allocation})\n")
    print(res.to_string())


def cmd_paper(args):
    import socket

    from .live.paper import run_tick

    # A single stalled network read must fail the tick, not hang it: launchd
    # runs one instance per label, so a hung tick silences ALL books' hourly
    # heartbeats (2026-07-14 incident: akshare stall blocked the loop for 9h).
    socket.setdefaulttimeout(120)
    run_tick(args.preset, state_dir=args.state_dir)


def cmd_paper_report(args):
    from .live.report import run_report

    run_report(args.preset, state_dir=args.state_dir)


def cmd_explain(args):
    from .live.explain import run_explain

    run_explain(args.preset, state_dir=args.state_dir)


def cmd_live(args):
    from .live.broker import OKXExecutor
    from .presets import PRESETS

    executor = OKXExecutor(PRESETS[args.preset], capital=args.capital)
    executor.run(send=args.send, flatten=args.flatten)


def _add_vt_args(parser):
    parser.add_argument("--vol-target", type=float, default=None,
                        help="annualized vol target, e.g. 0.3 (wraps strategy in VolTarget)")
    parser.add_argument("--vol-window", type=int, default=168)
    parser.add_argument("--bars-per-year", type=float, default=8760,
                        help="8760 for 1h crypto, 105120 for 5m crypto, 252 for daily stocks")


def main():
    p = argparse.ArgumentParser(prog="qtrade")
    sub = p.add_subparsers(dest="cmd", required=True)

    f = sub.add_parser("fetch", help="download bars into the local store")
    f.add_argument("--market", default="crypto", choices=list(ADAPTERS))
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
    _add_vt_args(b)
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
        _add_vt_args(g)
        if name == "walkforward":
            g.add_argument("--folds", type=int, default=5)
        g.set_defaults(fn=fn)

    pf = sub.add_parser("portfolio", help="multi-symbol portfolio backtest, shared cash")
    pf.add_argument("--market", default="crypto")
    pf.add_argument("--rules", default=None, choices=list(BY_NAME))
    pf.add_argument("--symbols", default=None, help="comma-separated; default = all in store")
    pf.add_argument("--timeframe", default="1h")
    pf.add_argument("--strategy", required=True,
                    choices=list(STRATEGIES) + list(PORTFOLIO_STRATEGIES))
    pf.add_argument("--param", action="append", help="k=v, repeatable")
    pf.add_argument("--allocation", default="inv_vol", choices=["equal", "inv_vol"])
    _add_vt_args(pf)
    pf.set_defaults(fn=cmd_portfolio)

    pp = sub.add_parser("paper", help="paper-trade a validated preset (one tick per call)")
    pp.add_argument("--preset", default="crypto_core")
    pp.add_argument("--state-dir", default=None, help="override state dir (testing)")
    pp.set_defaults(fn=cmd_paper)

    pr = sub.add_parser("paper-report", help="live paper record vs backtest expectation")
    pr.add_argument("--preset", default="crypto_core")
    pr.add_argument("--state-dir", default=None)
    pr.set_defaults(fn=cmd_paper_report)

    ex = sub.add_parser("explain", help="why each position: full decision chain right now")
    ex.add_argument("--preset", default="crypto_core")
    ex.add_argument("--state-dir", default=None)
    ex.set_defaults(fn=cmd_explain)

    ab = sub.add_parser("paper-ab", help="compare two parallel paper records")
    ab.add_argument("--a", default="crypto_core")
    ab.add_argument("--b", default="crypto_core_v2")
    ab.set_defaults(fn=lambda a: __import__("qtrade.live.report", fromlist=["run_ab"]).run_ab(a.a, a.b))

    wk = sub.add_parser("weekly", help="one-command weekly digest + protocol due reminders")
    wk.set_defaults(fn=lambda a: __import__("qtrade.live.weekly", fromlist=["run_weekly"]).run_weekly())

    hc = sub.add_parser("health", help="data integrity + heartbeat + halt-marker check")
    hc.set_defaults(fn=lambda a: __import__("qtrade.live.healthcheck", fromlist=["run_health"]).run_health())

    pt = sub.add_parser("parity", help="recompute the last recorded tick's signals from data")
    pt.add_argument("--preset", default="crypto_core")
    pt.set_defaults(fn=lambda a: __import__("qtrade.live.parity", fromlist=["run_parity"]).run_parity(a.preset))

    al = sub.add_parser("allocate", help="inverse-vol capital split across deployed books")
    al.add_argument("--capital", type=float, required=True)
    al.set_defaults(fn=lambda a: __import__("qtrade.live.allocate", fromlist=["run_allocate"]).run_allocate(a.capital))

    lv = sub.add_parser("live", help="real execution on OKX swaps (dry-run unless --send)")
    lv.add_argument("--preset", default="crypto_core")
    lv.add_argument("--capital", type=float, required=True,
                    help="max USDT this book may manage (hard cap)")
    lv.add_argument("--send", action="store_true", help="actually place orders")
    lv.add_argument("--flatten", action="store_true", help="close all positions")
    lv.set_defaults(fn=cmd_live)

    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
