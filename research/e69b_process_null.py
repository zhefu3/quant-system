"""E69-B: can this research process, run in a no-edge world, invent the flagship?

E69-A asked whether the FINAL strategy could be luck. This asks the harder
question the reviewer insisted on: seven months of asking (61 trials, 11
families on the crypto panel — research/artifacts/trial_ledger.json) ended in
a winner. In each block-shuffled null world we spend the same search budget,
with the same family structure and the same gate-shaped selection rule, and
record what the process "discovers". If the winners' distribution reaches the
real 0.68 routinely, our history proves little; if it rarely does, the search
alone cannot explain the result.

Three frozen scenarios (process-uncertainty amendment, log 2026-07-27/28):
  B1  fixed-graph replay of the 82 reconstructible candidates, 100 worlds
  B2  adaptive surrogate, budget 115, low/med/high adaptivity, 60 worlds each
  B3  conservative stress, budget 345, high adaptivity, 60 worlds
Families whose data axis does not exist in a shuffled price world (carry,
factor zoo, IV sizing — 33 logged candidates) are absent from B1 by design
and stand in as generic same-family draws in B2/B3.

Checkpoints one row per (scenario, world) so a kill resumes, not restarts.
Usage: .venv/bin/python research/e69b_process_null.py [--workers 5]
"""

from __future__ import annotations

import argparse
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qtrade.backtest.portfolio import run_portfolio  # noqa: E402
from qtrade.markets.rules import CRYPTO_PERP  # noqa: E402
from qtrade.strategies.base import Strategy  # noqa: E402
from qtrade.strategies.composite import Composite  # noqa: E402
from qtrade.strategies.cta import CTATrend  # noqa: E402
from qtrade.strategies.meanrev import BollingerRevert  # noqa: E402
from qtrade.strategies.overlays import VolTarget  # noqa: E402

from e69_placebo import R_RANGES, _shuffled_world  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "outputs" / "e69b_worlds.csv"
SR_REAL = 0.68  # run_portfolio full-window Sharpe of the frozen strategy (E68-A)

FAMILY_BUDGET = {"trend": 32, "meanrev": 21, "composite": 16, "xsmom": 2,
                 "alloc_tilt": 2, "sizing": 4, "throttle": 2, "universe": 2,
                 "entry_filter": 1}
ADAPT = {"low": (6, 0.2), "med": (3, 0.5), "high": (2, 0.8)}  # (abandon_after, refine_p)
BATCH = 4


def _logu(rng, lo, hi):
    return int(round(np.exp(rng.uniform(np.log(lo), np.log(hi)))))


def _vt(s, target=0.4):
    return VolTarget(s, target_vol=target, vol_window=168, bars_per_year=8760)


class XSMom(Strategy):
    """Cross-sectional momentum: long top-2 / short bottom-2 by trailing return."""

    def __init__(self, lookback: int):
        self.lookback = lookback

    def target_position(self, bars: pd.DataFrame) -> pd.Series:
        raise NotImplementedError  # portfolio-level: run_portfolio uses target_weights

    def target_weights(self, closes: pd.DataFrame) -> pd.DataFrame:
        mom = closes.pct_change(self.lookback)
        rank = mom.rank(axis=1)
        n = closes.shape[1]
        w = pd.DataFrame(0.0, index=closes.index, columns=closes.columns)
        w[rank >= n - 1] = 0.25
        w[rank <= 2] = -0.25
        return w.fillna(0.0)


def draw_params(family: str, rng, near: dict | None = None) -> dict:
    """Sample a candidate's parameters, optionally perturbing an incumbent."""
    if near is not None:
        return {k: (max(2, int(round(v * rng.uniform(0.8, 1.25))))
                    if isinstance(v, int) else float(v * rng.uniform(0.8, 1.25)))
                for k, v in near.items()}
    if family in ("trend",):
        h = sorted([_logu(rng, *R_RANGES["h1"]), _logu(rng, *R_RANGES["h2"]),
                    _logu(rng, *R_RANGES["h3"])])
        return {"h1": h[0], "h2": h[1], "h3": h[2]}
    if family == "meanrev":
        return {"bw": _logu(rng, *R_RANGES["bw"]),
                "bz": float(rng.uniform(*R_RANGES["bz"])),
                "rw": _logu(rng, *R_RANGES["rw"])}
    if family == "xsmom":
        return {"lb": _logu(rng, 72, 720)}
    # composite-shaped families (incl. generic stand-ins): both legs
    h = sorted([_logu(rng, *R_RANGES["h1"]), _logu(rng, *R_RANGES["h2"]),
                _logu(rng, *R_RANGES["h3"])])
    p = {"h1": h[0], "h2": h[1], "h3": h[2],
         "bw": _logu(rng, *R_RANGES["bw"]),
         "bz": float(rng.uniform(*R_RANGES["bz"])),
         "rw": _logu(rng, *R_RANGES["rw"])}
    if family == "sizing":
        p["vt"] = float(rng.choice([0.2, 0.4, 0.6]))
    if family == "throttle":
        p["eps"] = float(rng.choice([0.02, 0.08]))
    if family == "universe":
        p["drop"] = int(rng.integers(0, 6))
    return p


def build(family: str, p: dict):
    """Params -> (strategy, run_portfolio kwargs)."""
    kw = {"allocation": "equal", "rebalance_eps": p.get("eps", 0.05)}
    if family == "trend":
        return _vt(CTATrend(h1=p["h1"], h2=p["h2"], h3=p["h3"])), kw
    if family == "meanrev":
        return _vt(BollingerRevert(window=p["bw"], entry_z=p["bz"], side="both",
                                   regime_window=p["rw"])), kw
    if family == "xsmom":
        return XSMom(p["lb"]), kw
    if family == "alloc_tilt":
        kw["allocation"] = "inv_vol"
    strat = Composite([
        (_vt(CTATrend(h1=p["h1"], h2=p["h2"], h3=p["h3"]), p.get("vt", 0.4)), 0.5),
        (_vt(BollingerRevert(window=p["bw"], entry_z=p["bz"], side="both",
                             regime_window=p["rw"]), p.get("vt", 0.4)), 0.5)])
    return strat, kw


def evaluate(family: str, p: dict, bars: dict) -> tuple[float, bool]:
    """Full-window Sharpe + the both-halves-positive gate."""
    use = bars
    if family == "universe" and "drop" in p:
        keys = sorted(bars)
        use = {k: v for k, v in bars.items() if k != keys[p["drop"] % len(keys)]}
    strat, kw = build(family, p)
    try:
        s = run_portfolio(strat, use, CRYPTO_PERP, "1h", oos_fraction=0.5, **kw)
        full = float(s.loc["full", "sharpe"])
        halves = (float(s.loc["in_sample", "sharpe"]) > 0
                  and float(s.loc["out_of_sample", "sharpe"]) > 0)
        return (full if np.isfinite(full) else -9.0), halves
    except Exception:
        return -9.0, False


def run_world(task: tuple[str, str, int]) -> dict:
    scenario, policy, seed = task
    rng = np.random.default_rng(seed)
    bars = _shuffled_world(seed)
    results = []  # (sr, halves, family)

    def spend(family, p):
        sr, ok = evaluate(family, p, bars)
        results.append((sr, ok, family))
        return sr

    if scenario == "B1":
        for fam, n in FAMILY_BUDGET.items():
            for _ in range(n):
                spend(fam, draw_params(fam, rng))
    else:
        budget = 115 if scenario == "B2" else 345
        abandon_after, refine_p = ADAPT[policy]
        fams = list(FAMILY_BUDGET) + ["composite", "composite", "composite"]
        rng.shuffle(fams)
        fam_i, best_sr, best = 0, -9.0, None
        bad_streak = 0
        while len(results) < budget:
            fam = fams[fam_i % len(fams)]
            batch_best = -9.0
            for _ in range(min(BATCH, budget - len(results))):
                near = best[1] if (best and best[0] == fam
                                   and rng.random() < refine_p) else None
                p = draw_params(fam, rng, near=near)
                sr = spend(fam, p)
                batch_best = max(batch_best, sr)
                if sr > best_sr:
                    best_sr, best = sr, (fam, p)
            if batch_best < best_sr:  # the run's incumbent survived this batch
                bad_streak += 1
            else:
                bad_streak = 0
            if bad_streak >= abandon_after:
                fam_i, bad_streak = fam_i + 1, 0
            elif best and best[0] != fam:
                fam_i += 1  # drift toward wherever the incumbent lives
                if rng.random() < refine_p:
                    fams[fam_i % len(fams)] = best[0]

    passed = [(sr, f) for sr, ok, f in results if ok]
    pool = passed if passed else [(sr, f) for sr, ok, f in results]
    winner_sr, winner_fam = max(pool, key=lambda x: x[0])
    return {"scenario": scenario, "policy": policy, "seed": seed,
            "winner_sr": round(winner_sr, 4), "winner_family": winner_fam,
            "n_evals": len(results), "n_pass_halves": len(passed)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=5)
    args = ap.parse_args()

    tasks = ([("B1", "fixed", 3000 + i) for i in range(100)]
             + [("B2", pol, 4000 + j * 100 + i)
                for j, pol in enumerate(("low", "med", "high")) for i in range(60)]
             + [("B3", "high", 5000 + i) for i in range(60)])
    done = set()
    if OUT.exists():
        prev = pd.read_csv(OUT)
        done = set(zip(prev["scenario"], prev["policy"], prev["seed"]))
        print(f"resuming: {len(done)} worlds on disk")
    todo = [t for t in tasks if t not in done]
    print(f"{len(todo)} worlds to run on {args.workers} workers -> {OUT}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    header = not OUT.exists()
    n_done = 0
    with ProcessPoolExecutor(max_workers=args.workers) as pool, open(OUT, "a") as f:
        if header:
            f.write("scenario,policy,seed,winner_sr,winner_family,n_evals,n_pass_halves\n")
        for fut in as_completed([pool.submit(run_world, t) for t in todo]):
            r = fut.result()
            f.write(",".join(str(r[k]) for k in
                             ("scenario", "policy", "seed", "winner_sr",
                              "winner_family", "n_evals", "n_pass_halves")) + "\n")
            f.flush()
            n_done += 1
            if n_done % 5 == 0:
                print(f"  {n_done}/{len(todo)} worlds", flush=True)

    df = pd.read_csv(OUT)
    print(f"\n=== E69-B winners vs SR_real={SR_REAL} ===")
    for (sc, pol), g in df.groupby(["scenario", "policy"]):
        w = g["winner_sr"]
        pct = float((w < SR_REAL).mean()) * 100
        print(f"{sc}/{pol}: worlds={len(w)}  p50={w.median():+.2f} "
              f"p95={w.quantile(.95):+.2f} max={w.max():+.2f}  "
              f"SR_real percentile={pct:.1f}%")


if __name__ == "__main__":
    main()
