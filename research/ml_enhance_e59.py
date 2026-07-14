"""E59: E47 feature expansion with prescreened zoo factors (prereg 2026-07-14).

Everything except the feature set is inherited verbatim from ml_enhance.py
(E47): universe, PIT data, monthly top-50, cost model, LGB hyperparameters,
expanding-window training with quarterly refits, backtest_topk metrics.

Feature set = E47's 17 base features
            + zoo prescreen survivors (outputs/e59_prescreen.csv, passed=True)
              after the frozen redundancy filter:
                rank survivors by |ic_mean| desc; greedily keep those whose
                train-window month-end-value correlation with every kept zoo
                feature AND every E47 base feature is < 0.8; stop at 30.

Gates (frozen, same as E45/E46/E47): net excess > 3%/yr AND IR > 0.5 AND
both halves positive.
"""

from __future__ import annotations

import sys
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ml_enhance import LGB_PARAMS, build_features, load_panels  # noqa: E402
from zoo_panel import build_panel  # noqa: E402

from qtrade.backtest.relative import backtest_topk  # noqa: E402
from qtrade.data.store import BarStore  # noqa: E402
from qtrade.factors import FactorRegistry  # noqa: E402

CUTOFF = pd.Timestamp("2018-07-01", tz="UTC")  # frozen, = prescreen train window end
MAX_ZOO_FEATS = 30
CORR_MAX = 0.8
PRESCREEN_CSV = Path(__file__).resolve().parents[1] / "outputs" / "e59_prescreen.csv"


def select_zoo_features(prescreen: pd.DataFrame, zoo_monthly: dict[str, pd.DataFrame],
                        base_monthly: dict[str, pd.DataFrame]) -> list[str]:
    """Frozen redundancy filter on TRAIN-window month-end values."""

    def train_flat(f: pd.DataFrame) -> pd.Series:
        m = f[f.index < CUTOFF]
        return m.stack(future_stack=True)

    base_flat = {n: train_flat(f) for n, f in base_monthly.items()}
    survivors = prescreen[prescreen["passed"] == True]  # noqa: E712
    survivors = survivors.reindex(
        survivors["ic_mean"].abs().sort_values(ascending=False).index)

    kept: list[str] = []
    kept_flat: dict[str, pd.Series] = {}
    for aid in survivors["alpha_id"]:
        if aid not in zoo_monthly:
            continue
        cand = train_flat(zoo_monthly[aid])
        redundant = False
        for other in list(kept_flat.values()) + list(base_flat.values()):
            pair = pd.concat([cand, other], axis=1, join="inner").dropna()
            if len(pair) >= 100 and abs(pair.iloc[:, 0].corr(pair.iloc[:, 1])) >= CORR_MAX:
                redundant = True
                break
        if not redundant:
            kept.append(aid)
            kept_flat[aid] = cand
        if len(kept) >= MAX_ZOO_FEATS:
            break
    return kept


def main():
    store = BarStore()
    prescreen = pd.read_csv(PRESCREEN_CSV)
    n_pass = int(prescreen["passed"].sum())
    print(f"prescreen: {n_pass}/{len(prescreen)} passed")
    if n_pass == 0:
        print("E59 verdict path: no zoo survivors -> experiment ends, "
              "zoo archived as '已检验无增量' (prereg branch 3)")
        return

    # --- E47 base pipeline, verbatim ------------------------------------
    closes, vols, panels, membership = load_panels()
    feats = build_features(closes, vols, panels)
    m_index = closes.resample("ME").last().index

    # --- zoo features on the full window ---------------------------------
    zoo_panel, _ = build_panel()
    reg = FactorRegistry()
    zoo_monthly: dict[str, pd.DataFrame] = {}
    for aid in prescreen[prescreen["passed"] == True]["alpha_id"]:  # noqa: E712
        try:
            f = reg.compute(aid, zoo_panel)
            zoo_monthly[aid] = f.resample("ME").last().reindex(m_index)
        except Exception as e:  # noqa: BLE001
            print(f"  skip {aid}: {str(e)[:60]}")

    base_monthly = {n: f.resample("ME").last().reindex(m_index) for n, f in feats.items()}
    selected = select_zoo_features(prescreen, zoo_monthly, base_monthly)
    print(f"selected zoo features ({len(selected)}): {selected}")

    # align zoo features to daily index so the E47 assembly loop stays verbatim
    for aid in selected:
        feats[aid] = zoo_monthly[aid].reindex(closes.index, method="ffill")

    # --- monthly samples (E47 verbatim) ----------------------------------
    bench = store.load("ashare_index", "SH_000300", "1d")["close"]
    bench_m = bench.resample("ME").last().pct_change()
    month_ends = m_index
    member_map = {s: set(g["code"]) for s, g in membership.groupby("snap")}
    snap_dates = sorted(member_map)

    rows = []
    m_close = closes.resample("ME").last()
    fwd_ret = m_close.pct_change().shift(-1)
    for d in month_ends:
        snap = max((s for s in snap_dates if pd.Timestamp(s, tz="UTC") <= d), default=None)
        if snap is None:
            continue
        members = [c for c in member_map[snap] if c in closes.columns]
        upto = closes.loc[:d]
        if len(upto) < 260:
            continue
        di = upto.index[-1]
        fvals = {name: f.loc[di] for name, f in feats.items()}
        tgt = fwd_ret.loc[d] - (bench_m.shift(-1).loc[d] if d in bench_m.index else np.nan)
        for c in members:
            row = {name: fvals[name].get(c, np.nan) for name in feats}
            row.update({"date": d, "code": c, "target": tgt.get(c, np.nan)})
            rows.append(row)
    df = pd.DataFrame(rows).replace([np.inf, -np.inf], np.nan)
    feat_cols = list(feats)
    df = df.dropna(subset=["target"])
    print(f"samples: {len(df)} rows x {len(feat_cols)} features "
          f"(17 base + {len(selected)} zoo), {df['date'].nunique()} months")

    # --- expanding-window predictions (E47 verbatim) ---------------------
    dates = sorted(df["date"].unique())
    preds = {}
    model = None
    for i, d in enumerate(dates):
        train = df[df["date"] < d]
        if train["date"].nunique() < 36:
            continue
        if model is None or i % 3 == 0:
            cut = int(len(train) * 0.9)
            model = lgb.LGBMRegressor(**LGB_PARAMS)
            model.fit(train[feat_cols].iloc[:cut], train["target"].iloc[:cut],
                      eval_set=[(train[feat_cols].iloc[cut:], train["target"].iloc[cut:])],
                      callbacks=[lgb.early_stopping(50, verbose=False)])
        cur = df[df["date"] == d]
        preds[d] = pd.Series(model.predict(cur[feat_cols]), index=cur["code"].values)
    print(f"predicted months: {len(preds)}")

    # --- backtest (E47 verbatim) ------------------------------------------
    score_lookup = {d.strftime("%Y-%m"): s for d, s in preds.items()}

    def score_fn(hist: pd.DataFrame) -> pd.Series:
        key = hist.index[-1].strftime("%Y-%m")
        s = score_lookup.get(key)
        if s is None:
            return pd.Series(dtype=float)
        remap = {c: f"{c.split('.')[0]}.{c.split('.')[1]}" for c in s.index}
        return pd.Series(s.values, index=[remap[c] for c in s.index]).dropna()

    closes_bt = closes.rename(columns={c: f"{c.split('.')[0]}.{c.split('.')[1]}"
                                       for c in closes.columns})
    membership_bt = membership.copy()
    membership_bt["code"] = membership_bt["code"].map(
        lambda c: f"{c.split('.')[1].lower()}.{c.split('.')[0]}")
    r = backtest_topk(closes_bt, membership_bt, score_fn, bench, k=50, buffer_rank=0)
    curve = r.pop("excess_curve")
    half = len(curve) // 2
    print("\n=== E59 LightGBM 指增（E47 + zoo 特征）===")
    print({k: v for k, v in r.items()})
    print(f"前半段超额 {float(curve.iloc[half]/curve.iloc[0]-1)*100:+.1f}% / "
          f"后半段超额 {float(curve.iloc[-1]/curve.iloc[half]-1)*100:+.1f}%")
    yearly = curve.resample('YE').last().pct_change().dropna() * 100
    print("逐年超额:", {ts.year: round(v, 1) for ts, v in yearly.items()})
    print("\nE47 基线（档案）: 净超额 +2.54%/年, IR 0.22, 两半段 +3.7/+11.6")
    print("门槛: 净超额>3%/年 且 IR>0.5 且 两半段皆正")


if __name__ == "__main__":
    main()
