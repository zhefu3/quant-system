"""Deflated Sharpe Ratio for the flagship, under three trial-count scenarios.

The reviewer's ruling (frozen 2026-07-27): with most historical candidate
return series unsaved, a single DSR number would be false precision. So this
reports a scenario table and never averages it:

  family-floor   N = distinct economic families that queried the crypto panel
                 (ledger lower bound; closed-form E[maxSR])
  reconstructed  joint circular block bootstrap over M regenerated candidates
                 from the strategy's own family — covers the PARAMETER
                 dimension of the search only, and says so
  independent-N  every logged candidate treated as an independent trial
                 (ledger upper bound; closed-form E[maxSR]; most punishing)

Mechanics: Bailey & Lopez de Prado (2014). PSR(SR*) = Phi(((SR-SR*)sqrt(T-1))
/ sqrt(1 - g3*SR + (g4-1)/4*SR^2)) in per-period units; DSR = PSR(E[maxSR]).
E[maxSR] closed form uses the cross-candidate dispersion of the SR estimator;
here that dispersion is MEASURED from the E69-A random-parameter family (334
draws) rather than assumed.

All numbers live in this harness's coordinates (the same engine as E68/E69,
control ~0.68 full-window) — differences read within the table; nothing here
restates the archival 1.11.

Usage: .venv/bin/python research/dsr.py [--m 100] [--n-family 30] [--n-upper 1200]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import vectorbt as vbt
from scipy import stats as sps

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qtrade.backtest.portfolio import _allocations  # noqa: E402
from qtrade.backtest.engine import Engine  # noqa: E402
from qtrade.data.store import BarStore  # noqa: E402
from qtrade.markets.rules import CRYPTO_PERP  # noqa: E402
from qtrade.presets import CRYPTO_CORE  # noqa: E402

from e69_placebo import PANEL, START, _random_params_strategy  # noqa: E402

ART = Path(__file__).resolve().parents[1] / "research" / "artifacts"
R_DRAWS = Path(__file__).resolve().parents[1] / "outputs" / "e69a_draws.csv"
BLOCK_DAYS = 21          # frozen: monthly-scale blocks, not searched
N_BOOT = 2000
SEED = 42
EULER = 0.5772156649015329


def _panel() -> dict[str, pd.DataFrame]:
    store = BarStore()
    return {s: (lambda b: b[b.index >= START])(store.load("crypto", s, "1h"))
            for s in PANEL}


def net_hourly_returns(strategy, bars: dict[str, pd.DataFrame]) -> pd.Series:
    """Engine-faithful net returns: the same vbt mechanics as run_portfolio."""
    common = None
    for df in bars.values():
        common = df.index if common is None else common.intersection(df.index)
    closes = pd.DataFrame({s: df.loc[common, "close"] for s, df in bars.items()})
    alloc = _allocations(closes, "equal")
    engine = Engine(CRYPTO_PERP, init_cash=10_000.0,
                    rebalance_eps=CRYPTO_CORE.rebalance_eps)
    orders, effective = {}, {}
    for sym, df in bars.items():
        raw = strategy.target_position(df).reindex(common).ffill().fillna(0.0)
        w = (raw * alloc[sym]).clip(-1.0, 1.0)
        orders[sym], effective[sym] = engine.process_weights(w, common)
    orders = pd.DataFrame(orders)
    orders.iloc[0] = pd.DataFrame(effective).iloc[0]
    pf = vbt.Portfolio.from_orders(
        closes, size=orders, size_type="targetpercent", direction="both",
        fees=CRYPTO_PERP.fee_rate, slippage=CRYPTO_PERP.slippage,
        init_cash=10_000.0, freq="1h", group_by=True, cash_sharing=True)
    return pf.returns()


def to_daily(hourly: pd.Series) -> pd.Series:
    return (1 + hourly).resample("1D").prod() - 1


def ann_sr(daily: np.ndarray) -> float:
    sd = daily.std(ddof=1)
    return float(daily.mean() / sd * np.sqrt(365)) if sd > 0 else np.nan


def psr(sr_daily: float, sr0_daily: float, t: int, skew: float, kurt: float) -> float:
    denom = np.sqrt(max(1 - skew * sr_daily + (kurt - 1) / 4 * sr_daily ** 2, 1e-12))
    return float(sps.norm.cdf((sr_daily - sr0_daily) * np.sqrt(t - 1) / denom))


def emax_closed(n: int, v_daily: float) -> float:
    """BLdP expected max of n iid SR estimators with variance v (daily units)."""
    z1 = sps.norm.ppf(1 - 1 / n)
    z2 = sps.norm.ppf(1 - 1 / (n * np.e))
    return float(np.sqrt(v_daily) * ((1 - EULER) * z1 + EULER * z2))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--m", type=int, default=100)
    ap.add_argument("--n-family", type=int, required=True)
    ap.add_argument("--n-upper", type=int, required=True)
    args = ap.parse_args()
    ART.mkdir(exist_ok=True)

    bars = _panel()
    print("control: frozen strategy, engine-faithful net returns ...")
    ctrl_d = to_daily(net_hourly_returns(CRYPTO_CORE.strategy(), bars)).dropna()
    r = ctrl_d.to_numpy()
    sr_ann, t = ann_sr(r), len(r)
    sr_d = sr_ann / np.sqrt(365)
    skew, kurt = float(sps.skew(r)), float(sps.kurtosis(r, fisher=False))
    print(f"  SR {sr_ann:.3f} ann | T={t}d | skew {skew:.2f} | kurt {kurt:.1f} "
          f"| PSR(0) {psr(sr_d, 0.0, t, skew, kurt):.4f}")

    cache = ART / "dsr_candidates_daily.parquet"
    if cache.exists():
        cand = pd.read_parquet(cache)
        print(f"candidates: {cand.shape[1]} cached")
    else:
        print(f"candidates: generating {args.m} family variants ...")
        cols = {}
        for i in range(args.m):
            seed = 2000 + i  # the same seeds as E69-A's R family
            cols[f"c{seed}"] = to_daily(
                net_hourly_returns(_random_params_strategy(seed), bars))
            if (i + 1) % 10 == 0:
                print(f"  {i + 1}/{args.m}", flush=True)
        cand = pd.DataFrame(cols).dropna()
        cand.to_parquet(cache)

    # Two dispersion figures. The closed-form rows deflate for ACROSS-family
    # selection, where the trials are heterogeneous hypotheses on one panel —
    # the standard no-edge estimator variance V = (1+SR^2/2)/T applies there.
    # The within-family measured dispersion (E69-A's 334 draws) is printed as
    # a diagnostic: it is smaller, because same-family candidates co-move.
    v_daily = (1 + sr_d ** 2 / 2) / t
    rfam = pd.read_csv(R_DRAWS).query("family == 'R'")["sharpe"].dropna()
    print(f"V[SR]: null-theoretical ann sd {np.sqrt(v_daily * 365):.3f} "
          f"(closed-form rows) | measured within-family ann sd "
          f"{rfam.std(ddof=1):.3f} across {len(rfam)} draws (diagnostic)")

    # reconstructed scenario: joint block bootstrap, one time-index per rep
    x = cand.to_numpy()
    n_days, m = x.shape
    rng = np.random.default_rng(SEED)
    n_blocks = int(np.ceil(n_days / BLOCK_DAYS))
    maxes = np.empty(N_BOOT)
    for b in range(N_BOOT):
        starts = rng.integers(0, n_days, size=n_blocks)
        idx = np.concatenate(
            [(s + np.arange(BLOCK_DAYS)) % n_days for s in starts])[:n_days]
        xb = x[idx]
        srs = xb.mean(axis=0) / xb.std(axis=0, ddof=1) * np.sqrt(365)
        maxes[b] = np.nanmax(srs)
    emax_boot_ann = float(maxes.mean())

    scenarios = {
        "family-floor":  {"n": args.n_family,
                          "sr0_ann": emax_closed(args.n_family, v_daily) * np.sqrt(365)},
        "reconstructed": {"n": m, "sr0_ann": emax_boot_ann,
                          "note": "assumes max-of-family selection; E69-A puts "
                                  "the frozen config at the family's 40th pct, "
                                  "so this row over-deflates by construction"},
        "independent-N": {"n": args.n_upper,
                          "sr0_ann": emax_closed(args.n_upper, v_daily) * np.sqrt(365)},
    }
    print(f"\n=== DSR table (harness SR {sr_ann:.3f} ann, T={t}d) ===")
    out = {"sr_ann": sr_ann, "t_days": t, "skew": skew, "kurt": kurt,
           "psr0": psr(sr_d, 0.0, t, skew, kurt), "scenarios": {}}
    for name, sc in scenarios.items():
        d = psr(sr_d, sc["sr0_ann"] / np.sqrt(365), t, skew, kurt)
        out["scenarios"][name] = {**sc, "dsr": d}
        print(f"  {name:14s} N={sc['n']:5d}  E[maxSR]={sc['sr0_ann']:+.3f} ann"
              f"  DSR={d:.4f}" + ("  (" + sc.get("note", "") + ")" if sc.get("note") else ""))
    (ART / "dsr_result.json").write_text(json.dumps(out, indent=1))
    print(f"\nsaved -> {ART / 'dsr_result.json'}")


if __name__ == "__main__":
    main()
