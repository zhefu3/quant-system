"""E69-A: the false-positive noise floor under the strategy's own harness.

A gate that has only ever passed good strategies proves nothing until it is
shown killing junk. Three placebo families, 1000 draws, every one pushed
through the SAME backtester and cost model as the flagship (frozen execution
spec: log 2026-07-28, preregistered 2026-07-27):

  S  random persistent signals on real prices — position noise, vol-targeted
  B  the frozen strategy on block-shuffled worlds — time structure destroyed,
     cross-correlation and fat tails kept (one joint permutation, 168h blocks)
  R  random parameters from the strategy's own family on real prices — is the
     frozen config a lucky corner of its own neighborhood?

This arm calibrates final-strategy luck only; the research-process null is
E69-B's job and no claim here extends to it.

Draws checkpoint to CSV as they finish, so a crash resumes instead of
restarting. Usage:
    .venv/bin/python research/e69_placebo.py [--n 1000] [--workers 5]
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
from qtrade.data.store import BarStore  # noqa: E402
from qtrade.markets.rules import CRYPTO_PERP  # noqa: E402
from qtrade.presets import CRYPTO_CORE  # noqa: E402
from qtrade.strategies.base import Strategy  # noqa: E402
from qtrade.strategies.composite import Composite  # noqa: E402
from qtrade.strategies.cta import CTATrend  # noqa: E402
from qtrade.strategies.meanrev import BollingerRevert  # noqa: E402
from qtrade.strategies.overlays import VolTarget  # noqa: E402

PANEL = ["BTC/USDT", "ETH/USDT", "XRP/USDT", "ADA/USDT", "LTC/USDT", "LINK/USDT"]
START = pd.Timestamp("2019-07-01", tz="UTC")
OUT = Path(__file__).resolve().parents[1] / "outputs" / "e69a_draws.csv"

# frozen spec constants (log 2026-07-28)
FLIP_P = 1 / 72          # S: mean holding ~3 days, matched to mid-frequency
BLOCK = 168              # B: one-week blocks, joint across symbols
R_RANGES = {"h1": (24, 192), "h2": (96, 720), "h3": (240, 2160),
             "bw": (24, 240), "bz": (1.0, 3.0), "rw": (240, 1440)}

_BARS: dict[str, pd.DataFrame] | None = None  # per-worker cache


def _panel() -> dict[str, pd.DataFrame]:
    global _BARS
    if _BARS is None:
        store = BarStore()
        _BARS = {}
        for s in PANEL:
            b = store.load("crypto", s, "1h")
            _BARS[s] = b[b.index >= START]
    return _BARS


def _book(strategy: Strategy, bars: dict[str, pd.DataFrame]) -> float:
    r = run_portfolio(strategy, bars, CRYPTO_PERP, "1h", allocation="equal",
                      rebalance_eps=CRYPTO_CORE.rebalance_eps,
                      oos_fraction=0.0001).loc["full"]
    return float(r["sharpe"]) if pd.notna(r["sharpe"]) else float("nan")


class RandomPersistent(Strategy):
    """Coin-flip positions with realistic persistence — pure signal noise."""

    def __init__(self, seed: int):
        self.seed = seed

    def target_position(self, bars: pd.DataFrame) -> pd.Series:
        rng = np.random.default_rng(self.seed)
        n = len(bars)
        flips = rng.random(n) < FLIP_P
        sign = np.empty(n)
        cur = rng.choice([-1.0, 1.0])
        for i in range(n):
            if flips[i]:
                cur = -cur
            sign[i] = cur
        return pd.Series(sign, index=bars.index)


def _shuffled_world(seed: int) -> dict[str, pd.DataFrame]:
    """Joint circular block permutation of returns; prices rebuilt from 100."""
    bars = _panel()
    common = None
    for df in bars.values():
        common = df.index if common is None else common.intersection(df.index)
    rets = {s: df.loc[common, "close"].pct_change().fillna(0.0).to_numpy()
            for s, df in bars.items()}
    n = len(common)
    rng = np.random.default_rng(seed)
    n_blocks = int(np.ceil(n / BLOCK))
    starts = rng.integers(0, n, size=n_blocks)
    idx = np.concatenate([(s + np.arange(BLOCK)) % n for s in starts])[:n]
    out = {}
    for s, r in rets.items():
        px = 100.0 * np.cumprod(1 + r[idx])
        out[s] = pd.DataFrame({"open": px, "high": px, "low": px,
                               "close": px, "volume": 1e6}, index=common)
    return out


def _random_params_strategy(seed: int) -> Strategy:
    rng = np.random.default_rng(seed)

    def logu(lo, hi):
        return int(round(np.exp(rng.uniform(np.log(lo), np.log(hi)))))

    h = sorted([logu(*R_RANGES["h1"]), logu(*R_RANGES["h2"]), logu(*R_RANGES["h3"])])
    bw, rw = logu(*R_RANGES["bw"]), logu(*R_RANGES["rw"])
    bz = float(rng.uniform(*R_RANGES["bz"]))

    def vt(s):
        return VolTarget(s, target_vol=0.4, vol_window=168, bars_per_year=8760)

    trend = vt(CTATrend(h1=h[0], h2=h[1], h3=h[2]))
    meanrev = vt(BollingerRevert(window=bw, entry_z=bz, side="both",
                                 regime_window=rw))
    return Composite([(trend, 0.5), (meanrev, 0.5)])


def one_draw(task: tuple[str, int]) -> dict:
    family, seed = task
    try:
        if family == "S":
            sharpe = _book(RandomPersistent(seed), _panel())
        elif family == "B":
            sharpe = _book(CRYPTO_CORE.strategy(), _shuffled_world(seed))
        elif family == "R":
            sharpe = _book(_random_params_strategy(seed), _panel())
        else:
            raise ValueError(family)
        return {"family": family, "seed": seed, "sharpe": sharpe}
    except Exception as e:  # a failed draw is data, not a crash
        return {"family": family, "seed": seed, "sharpe": float("nan"),
                "error": str(e)[:120]}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=1000)
    ap.add_argument("--workers", type=int, default=5)
    args = ap.parse_args()

    n_s = args.n // 3
    n_b = args.n // 3
    n_r = args.n - n_s - n_b
    tasks = ([("S", i) for i in range(n_s)]
             + [("B", 1000 + i) for i in range(n_b)]
             + [("R", 2000 + i) for i in range(n_r)])

    done: set[tuple[str, int]] = set()
    if OUT.exists():
        prev = pd.read_csv(OUT)
        done = set(zip(prev["family"], prev["seed"]))
        print(f"resuming: {len(done)} draws already on disk")
    todo = [t for t in tasks if t not in done]

    control = _book(CRYPTO_CORE.strategy(), _panel())
    print(f"control (frozen strategy, this harness): Sharpe {control:.3f}")
    print(f"running {len(todo)} draws on {args.workers} workers -> {OUT}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    write_header = not OUT.exists()
    n_done = 0
    with ProcessPoolExecutor(max_workers=args.workers) as pool, \
            open(OUT, "a") as f:
        if write_header:
            f.write("family,seed,sharpe\n")
        for fut in as_completed([pool.submit(one_draw, t) for t in todo]):
            r = fut.result()
            f.write(f"{r['family']},{r['seed']},{r['sharpe']}\n")
            f.flush()
            n_done += 1
            if n_done % 25 == 0:
                print(f"  {n_done}/{len(todo)} done", flush=True)

    df = pd.read_csv(OUT).dropna(subset=["sharpe"])
    print(f"\n=== E69-A summary (control {control:.3f}) ===")
    for fam, g in df.groupby("family"):
        s = g["sharpe"]
        line = (f"{fam}: n={len(s)}  mean={s.mean():+.3f}  p95={s.quantile(.95):+.3f} "
                f" p99={s.quantile(.99):+.3f}  max={s.max():+.3f}")
        if fam in ("S", "B"):
            line += f"  P(>=control)={float((s >= control).mean()):.4f}"
        else:
            line += (f"  frozen-config percentile="
                     f"{float((s < control).mean()) * 100:.1f}%")
        print(line)


if __name__ == "__main__":
    main()
