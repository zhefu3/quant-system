"""Transfer suite: run the same walk-forward config across many symbols.

The single most important robustness gate: an edge that only exists on the
symbol you tuned it on is not an edge. Usage:

    .venv/bin/python research/transfer_suite.py --strategy boll_revert \
        --grid window=48,96,192 --grid entry_z=1.5,2.0,2.5 --param long_only=false
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qtrade.cli import STRATEGIES, _parse_grid, _parse_value  # noqa: E402
from qtrade.data.store import BarStore  # noqa: E402
from qtrade.markets.rules import BY_NAME  # noqa: E402
from qtrade.research import walk_forward  # noqa: E402
from qtrade.strategies.overlays import with_vol_target  # noqa: E402


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--market", default="crypto")
    p.add_argument("--rules", default="crypto_perp")
    p.add_argument("--timeframe", default="1h")
    p.add_argument("--strategy", required=True, choices=list(STRATEGIES))
    p.add_argument("--grid", action="append", required=True)
    p.add_argument("--param", action="append")
    p.add_argument("--folds", type=int, default=5)
    p.add_argument("--vol-target", type=float, default=None)
    p.add_argument("--vol-window", type=int, default=168)
    p.add_argument("--bars-per-year", type=float, default=8760)
    p.add_argument("--symbols", default=None, help="comma-separated; default = all in store")
    args = p.parse_args()

    store = BarStore()
    cov = store.coverage()
    cov = cov[(cov["market"] == args.market) & (cov["timeframe"] == args.timeframe)]
    symbols = args.symbols.split(",") if args.symbols else sorted(cov["symbol"])

    cls = STRATEGIES[args.strategy]
    if args.vol_target:
        cls = with_vol_target(
            cls, target_vol=args.vol_target, vol_window=args.vol_window,
            bars_per_year=args.bars_per_year,
        )
    grid = _parse_grid(args.grid)
    fixed = {}
    for kv in args.param or []:
        k, v = kv.split("=", 1)
        fixed[k] = _parse_value(v)
    rules = BY_NAME[args.rules]

    summary = []
    for sym in symbols:
        bars = store.load(args.market, sym, args.timeframe)
        wf = walk_forward(cls, bars, grid, rules, args.timeframe,
                          n_folds=args.folds, fixed=fixed)
        if wf.empty:
            continue
        summary.append(
            {
                "symbol": sym,
                "folds_win": f"{int((wf['test_edge_pct'] > 0).sum())}/{len(wf)}",
                "mean_edge_pp": round(wf["test_edge_pct"].mean(), 2),
                "median_edge_pp": round(wf["test_edge_pct"].median(), 2),
                "worst_fold_pp": round(wf["test_edge_pct"].min(), 2),
                "mean_test_sharpe": round(wf["test_sharpe"].mean(), 2),
                "mean_return_pct": round(wf["test_return_pct"].mean(), 2),
                "worst_dd_pct": round(wf["test_max_dd_pct"].max(), 2),
            }
        )
    df = pd.DataFrame(summary)
    print("\n=== transfer summary ===")
    print(df.to_string(index=False))
    wins = (df["mean_edge_pp"] > 0).sum()
    print(
        f"\n{wins}/{len(df)} symbols positive mean edge; "
        f"cross-symbol mean {df['mean_edge_pp'].mean():+.1f}pp, "
        f"median {df['mean_edge_pp'].median():+.1f}pp"
    )


if __name__ == "__main__":
    main()
