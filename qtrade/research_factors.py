"""Factor library + pooled-IC evaluation harness.

A factor is a function (panel data) -> DataFrame[ts x symbol] of scores.
Evaluation: pooled Spearman rank IC between factor_t and forward returns,
daily-sampled to tame autocorrelation, broken down by year — a factor earns
promotion to strategy prototyping only if the IC sign is stable across years
and the magnitude is plausible after costs.

Anchors: `mom_multi` and `boll_z` correspond to the validated trend/meanrev
legs; if the harness can't see THEIR signal, the harness is broken.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# --------------------------------------------------------------- factor defs


def f_mom_multi(closes: pd.DataFrame, **_) -> pd.DataFrame:
    """Anchor: multi-horizon momentum vote (expect positive IC at long horizons)."""
    votes = sum(np.sign(closes.pct_change(h)) for h in (96, 288, 720))
    return votes / 3.0


def f_boll_z(closes: pd.DataFrame, **_) -> pd.DataFrame:
    """Anchor: bollinger z (expect NEGATIVE IC — reversal)."""
    mean = closes.rolling(96).mean()
    std = closes.rolling(96).std()
    return (closes - mean) / std


def f_dist_high(closes: pd.DataFrame, **_) -> pd.DataFrame:
    """Proximity to 10d high: 0 = at high (documented momentum flavor)."""
    return closes / closes.rolling(240).max() - 1.0


def f_vol_term(closes: pd.DataFrame, **_) -> pd.DataFrame:
    """Vol term structure: 1d vol / 10d vol. >1 = vol spiking now."""
    r = closes.pct_change()
    return r.rolling(24).std() / r.rolling(240).std()


def f_squeeze(closes: pd.DataFrame, highs=None, lows=None, **_) -> pd.DataFrame:
    """Range compression: current 24h range vs its 10d distribution (0=tightest)."""
    rng = (highs - lows).rolling(24).mean() / closes
    return rng.rolling(240).rank(pct=True)


def f_volume_trend(closes: pd.DataFrame, volumes=None, **_) -> pd.DataFrame:
    """Volume expansion: 1d dollar-volume vs its 10d mean."""
    dv = (volumes * closes)
    return dv.rolling(24).mean() / dv.rolling(240).mean()


def f_amihud(closes: pd.DataFrame, volumes=None, **_) -> pd.DataFrame:
    """Illiquidity: |ret| per dollar volume, 7d mean (log-scaled)."""
    dv = (volumes * closes).replace(0, np.nan)
    return np.log10((closes.pct_change().abs() / dv).rolling(168).mean())


def f_btc_beta(closes: pd.DataFrame, **_) -> pd.DataFrame:
    """Rolling 10d beta to BTC (low-beta anomaly probe)."""
    r = closes.pct_change()
    btc = r["BTC/USDT"]
    cov = r.rolling(240).cov(btc)
    return cov.div(btc.rolling(240).var(), axis=0)


def f_basis_level(closes: pd.DataFrame, basis=None, **_) -> pd.DataFrame:
    """Perp-spot basis, 24h smoothed (positioning/crowding, slow version)."""
    return basis.rolling(24).mean() if basis is not None else None


FACTORS = {
    "mom_multi(锚+)": f_mom_multi,
    "boll_z(锚-)": f_boll_z,
    "dist_high_10d": f_dist_high,
    "vol_term_1d/10d": f_vol_term,
    "squeeze_range": f_squeeze,
    "volume_trend": f_volume_trend,
    "amihud_illiq": f_amihud,
    "btc_beta_10d": f_btc_beta,
    "basis_level_1d": f_basis_level,
}

# ------------------------------------------------------------- IC evaluation


def pooled_ic(factor: pd.DataFrame, fwd: pd.DataFrame, sample_every: int = 24):
    """Pooled Spearman IC, daily-sampled; returns (overall, {year: ic}, n)."""
    f = factor.iloc[::sample_every]
    r = fwd.iloc[::sample_every]
    stacked = pd.DataFrame({"f": f.stack(), "r": r.stack()}).dropna()
    if len(stacked) < 100:
        return np.nan, {}, len(stacked)
    overall = stacked["f"].corr(stacked["r"], method="spearman")
    by_year = {}
    years = stacked.index.get_level_values(0).year
    for y in sorted(set(years)):
        sub = stacked[years == y]
        if len(sub) >= 50:
            by_year[y] = sub["f"].corr(sub["r"], method="spearman")
    return overall, by_year, len(stacked)


def evaluate(panel: dict, horizons=(24, 72, 168)) -> pd.DataFrame:
    closes = panel["closes"]
    rows = []
    for name, fn in FACTORS.items():
        fac = fn(closes, highs=panel.get("highs"), lows=panel.get("lows"),
                 volumes=panel.get("volumes"), basis=panel.get("basis"))
        if fac is None:
            continue
        for h in horizons:
            fwd = closes.pct_change(h).shift(-h)
            ic, by_year, n = pooled_ic(fac, fwd)
            yrs = list(by_year.values())
            same_sign = (sum(1 for v in yrs if np.sign(v) == np.sign(ic))
                         if yrs and not np.isnan(ic) else 0)
            rows.append({
                "factor": name, "fwd_h": h, "IC": round(ic, 4), "n": n,
                "yrs_same_sign": f"{same_sign}/{len(yrs)}",
                "ic_by_year": {k: round(v, 3) for k, v in by_year.items()},
            })
    return pd.DataFrame(rows)
