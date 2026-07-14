"""E56 step 1: train-window factor prescreen with same-universe random control.

Method (frozen in the E56 prereg; changing anything here after the freeze
invalidates the run):

  - window: month-ends strictly BEFORE 2018-07-01 (E47's first prediction
    month). The OOS segment never touches this tool.
  - cross-section: HS300 point-in-time members at each month-end (E43 data);
    months with <100 valid names are skipped.
  - signal: month-end factor value; outcome: next-month stock return.
  - IC: Spearman rank correlation per month.
  - random control (the Vibe-Trading bench_runner_strict idea): for each
    factor, 100 replicates that shuffle the factor's cross-sectional values
    within each month (NaN mask preserved), estimating the null mean IC
    (mechanical bias). A factor passes only if
        |IC_mean| >= 0.02
        AND IC sign stability: pos-ratio >= 0.55 (or <= 0.45 for reversed)
        AND t = |IC_mean - null_mean| / (IC_std / sqrt(n_months)) >= 3.5
    The 3.5 threshold is Harvey-Liu-Zhu (2016): with 442 candidate factors,
    a conventional 95th-percentile gate would admit ~22 pure-noise factors
    (5% x 442); t>=3.5 keeps the expected false-survivor count near zero.
  - selection (step 2, applied to survivors): rank by |IC_mean|, greedily
    keep factors whose month-end-value correlation with every already-kept
    factor AND with each of E47's 17 base features is < 0.8, stop at 30.

Run `--selftest` for a synthetic-data mechanics check (planted signal must
pass, pure noise must fail). The real run happens only after the E56
prereg is frozen.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qtrade.factors import FactorRegistry  # noqa: E402

CUTOFF = pd.Timestamp("2018-07-01", tz="UTC")  # frozen: E47 first prediction month
MIN_NAMES = 100
N_RANDOM = 100
IC_MIN = 0.02
POS_RATIO = 0.55
T_MIN = 3.5  # Harvey-Liu-Zhu (2016) multiple-testing bar
SEED = 42

OUT = Path(__file__).resolve().parents[1] / "outputs" / "e56_prescreen.csv"


def month_end_frames(panel_close: pd.DataFrame) -> tuple[pd.DatetimeIndex, pd.DataFrame]:
    """Trading month-ends before CUTOFF and next-month forward returns."""
    m_close = panel_close.resample("ME").last()
    fwd = m_close.pct_change().shift(-1)
    ends = m_close.index[(m_close.index < CUTOFF) & fwd.notna().any(axis=1).values]
    return ends, fwd


def members_at(membership: pd.DataFrame, d: pd.Timestamp) -> set[str]:
    snaps = sorted(membership["snap"].unique())
    snap = max((s for s in snaps if pd.Timestamp(s, tz="UTC") <= d), default=None)
    if snap is None:
        return set()
    return set(membership[membership["snap"] == snap]["code"])


def monthly_ic(factor: pd.DataFrame, fwd: pd.DataFrame, ends: pd.DatetimeIndex,
               membership: pd.DataFrame, rng: np.random.Generator | None = None,
               ) -> pd.Series:
    """Spearman IC per month-end; if rng given, factor values are shuffled
    within each cross-section (random-control replicate)."""
    f_m = factor.resample("ME").last()
    ics = {}
    for d in ends:
        if d not in f_m.index or d not in fwd.index:
            continue
        codes = [c for c in members_at(membership, d) if c in factor.columns]
        x = f_m.loc[d, codes]
        y = fwd.loc[d, codes]
        ok = x.notna() & y.notna()
        if ok.sum() < MIN_NAMES:
            continue
        xv, yv = x[ok], y[ok]
        if xv.nunique() < 2 or yv.nunique() < 2:
            continue
        if rng is not None:
            xv = pd.Series(rng.permutation(xv.values), index=xv.index)
        with np.errstate(invalid="ignore", divide="ignore"):
            ic = xv.rank().corr(yv.rank())
        if np.isfinite(ic):
            ics[d] = ic
    return pd.Series(ics)


def prescreen_one(factor: pd.DataFrame, fwd: pd.DataFrame, ends: pd.DatetimeIndex,
                  membership: pd.DataFrame, seed: int = SEED) -> dict:
    ic = monthly_ic(factor, fwd, ends, membership)
    if len(ic) < 24:  # need 2y of monthly cross-sections to say anything
        return {"n_months": len(ic), "passed": False, "reason": "too_few_months"}
    ic_mean = float(ic.mean())
    pos_ratio = float((ic > 0).mean())

    rng = np.random.default_rng(seed)
    null_means = np.array([
        float(monthly_ic(factor, fwd, ends, membership, rng=rng).mean())
        for _ in range(N_RANDOM)])
    null_mean = float(null_means.mean())
    se = float(ic.std(ddof=1) / np.sqrt(len(ic)))
    t = abs(ic_mean - null_mean) / se if se > 0 else 0.0

    passed = (abs(ic_mean) >= IC_MIN
              and (pos_ratio >= POS_RATIO or pos_ratio <= 1 - POS_RATIO)
              and t >= T_MIN)
    return {"n_months": len(ic), "ic_mean": round(ic_mean, 4),
            "ic_pos_ratio": round(pos_ratio, 3), "null_mean": round(null_mean, 4),
            "t": round(t, 2), "passed": bool(passed), "reason": ""}


def run(selftest: bool = False):
    if selftest:
        rng = np.random.RandomState(7)
        idx = pd.date_range("2014-01-01", "2018-06-30", freq="B", tz="UTC")
        codes = [f"S{i:03d}" for i in range(150)]
        # planted signal must survive monthly aggregation: an AR(1) monthly
        # driver (persistent across months) that feeds that month's returns
        months = pd.period_range(idx[0], idx[-1], freq="M")
        F = np.zeros((len(months), 150))
        for m in range(1, len(months)):
            F[m] = 0.7 * F[m - 1] + 0.3 * rng.normal(0, 1, 150)
        f_monthly = pd.DataFrame(F, index=months.to_timestamp(how="end").tz_localize("UTC"), columns=codes)
        driver = f_monthly.reindex(idx, method="bfill")
        rets = (0.04 / 21) * driver + rng.normal(0, 0.02, (len(idx), 150))
        close = 100 * (1 + pd.DataFrame(rets, idx, codes)).cumprod()
        membership = pd.DataFrame({"snap": "2014-01-01", "code": codes})
        ends, fwd = month_end_frames(close)
        planted = prescreen_one(driver, fwd, ends, membership)
        noise = prescreen_one(
            pd.DataFrame(rng.normal(0, 1, (len(idx), 150)), idx, codes),
            fwd, ends, membership)
        print("planted signal:", planted)
        print("pure noise:   ", noise)
        assert planted["passed"] and not noise["passed"], "selftest FAILED"
        print("selftest OK: planted passes, noise fails")
        return

    from zoo_panel import build_panel
    panel, membership = build_panel()
    # freeze the window: nothing after CUTOFF enters this tool
    panel = {c: f[f.index < CUTOFF] for c, f in panel.items()}
    ends, fwd = month_end_frames(panel["close"])
    reg = FactorRegistry()
    rows = []
    ids = reg.list(universe="equity_cn") or reg.list()
    for i, aid in enumerate(ids):
        try:
            factor = reg.compute(aid, panel)
            res = prescreen_one(factor, fwd, ends, membership)
        except Exception as e:  # noqa: BLE001 — one bad alpha must not kill the sweep
            res = {"passed": False, "reason": f"error: {str(e)[:80]}"}
        rows.append({"alpha_id": aid, **res})
        if (i + 1) % 25 == 0:
            print(f"  {i + 1}/{len(ids)} screened, "
                  f"{sum(r.get('passed') for r in rows)} passed so far")
    df = pd.DataFrame(rows)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT, index=False)
    print(f"\n{df['passed'].sum()} / {len(df)} passed -> {OUT}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    run(selftest=args.selftest)
