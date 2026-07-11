"""E47: ML index enhancement — LightGBM over ~18 frozen features (see log).

Protocol (pre-registered, frozen):
  - target: next-month stock return minus benchmark return
  - expanding-window training, quarterly refits, >=36 months of samples
  - fixed hyperparameters, single model family, no search
  - selection: top-50 by predicted score, monthly, same cost model & gates

This is the honest version of what mid-frequency ML 指增 does. If THIS
can't find alpha in daily+fundamental data, the affordable A-share story
truly ends (next stop would be paid intraday data).
"""

from __future__ import annotations

import sys
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qtrade.backtest.relative import backtest_topk  # noqa: E402
from qtrade.data.store import BarStore  # noqa: E402

PIT = Path(__file__).resolve().parents[1] / "data_store" / "pit_ts"

LGB_PARAMS = dict(objective="regression", max_depth=4, num_leaves=15,
                  learning_rate=0.05, n_estimators=500, subsample=0.8,
                  colsample_bytree=0.8, min_child_samples=50, verbosity=-1)


def load_panels():
    store = BarStore()
    weights = pd.read_parquet(PIT / "hs300_weights.parquet")
    union = sorted(weights["con_code"].unique())

    closes, vols = {}, {}
    ep, bp, mv, torate = {}, {}, {}, {}
    roe_ev, growth_ev = {}, {}
    for code in union:
        sym = code.replace(".", "_")
        try:
            bars = store.load("ashare_ts", code, "1d")
        except FileNotFoundError:
            continue
        closes[code] = bars["close"]
        vols[code] = bars["volume"]
        f_db = PIT / "daily_basic" / f"{sym}.parquet"
        if f_db.exists():
            db = pd.read_parquet(f_db)
            idx = pd.to_datetime(db["trade_date"]).dt.tz_localize("Asia/Shanghai").dt.tz_convert("UTC")
            for col, target in [("pe_ttm", ep), ("pb", bp), ("total_mv", mv),
                                ("turnover_rate", torate)]:
                target[code] = pd.Series(pd.to_numeric(db[col], errors="coerce").values,
                                         index=idx).sort_index()
        f_fi = PIT / "fina" / f"{sym}.parquet"
        if f_fi.exists():
            fi = pd.read_parquet(f_fi).dropna(subset=["ann_date"])
            ann = pd.to_datetime(fi["ann_date"]).dt.tz_localize("Asia/Shanghai").dt.tz_convert("UTC")
            roe_ev[code] = pd.Series(pd.to_numeric(fi["roe"], errors="coerce").values,
                                     index=ann).sort_index()
            growth_ev[code] = pd.Series(pd.to_numeric(fi["netprofit_yoy"], errors="coerce").values,
                                        index=ann).sort_index()

    closes = pd.DataFrame(closes)
    vols = pd.DataFrame(vols).reindex(closes.index)

    def daily_panel(d: dict) -> pd.DataFrame:
        return pd.DataFrame({k: v[~v.index.duplicated(keep="last")] for k, v in d.items()}
                            ).reindex(closes.index).ffill()

    def event_panel(d: dict) -> pd.DataFrame:
        out = {}
        for code, s in d.items():
            s = s[~s.index.duplicated(keep="last")]
            out[code] = s.reindex(closes.index.union(s.index)).ffill().reindex(closes.index)
        return pd.DataFrame(out)

    membership = weights.rename(columns={"trade_date": "snap", "con_code": "code"})
    membership["snap"] = pd.to_datetime(membership["snap"]).dt.strftime("%Y-%m-%d")
    return closes, vols, {
        "EP": 1.0 / daily_panel(ep), "BP": 1.0 / daily_panel(bp),
        "LOGMV": np.log(daily_panel(mv)), "TO": daily_panel(torate),
        "ROE": event_panel(roe_ev), "GROWTH": event_panel(growth_ev),
    }, membership


def build_features(closes: pd.DataFrame, vols: pd.DataFrame, panels: dict) -> dict[str, pd.DataFrame]:
    r = closes.pct_change()
    feats = {
        "mom_1m": closes.pct_change(21), "mom_3m": closes.pct_change(63),
        "mom_6m": closes.pct_change(126), "mom_12m": closes.pct_change(252),
        "vol_1m": r.rolling(21).std(), "vol_3m": r.rolling(63).std(),
        "max_1m": r.rolling(21).max(),
        "to_1m": panels["TO"].rolling(21).mean(),
        "to_chg": panels["TO"].rolling(21).mean() / panels["TO"].rolling(126).mean(),
        "EP": panels["EP"], "BP": panels["BP"], "LOGMV": panels["LOGMV"],
        "ROE": panels["ROE"], "GROWTH": panels["GROWTH"],
        "dist_high_6m": closes / closes.rolling(126).max() - 1.0,
        "amihud": np.log10((r.abs() / (vols * closes).replace(0, np.nan)).rolling(21).mean()),
        "corr_pv": r.rolling(21).corr(vols.pct_change()),
    }
    return feats


def main():
    store = BarStore()
    closes, vols, panels, membership = load_panels()
    feats = build_features(closes, vols, panels)
    bench = store.load("ashare_index", "SH_000300", "1d")["close"]
    bench_m = bench.resample("ME").last().pct_change()

    month_ends = closes.resample("ME").last().index
    member_map = {s: set(g["code"]) for s, g in membership.groupby("snap")}
    snap_dates = sorted(member_map)

    # ---- assemble monthly samples --------------------------------------
    rows = []
    m_close = closes.resample("ME").last()
    fwd_ret = m_close.pct_change().shift(-1)  # next-month stock return
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
    print(f"samples: {len(df)} rows x {len(feat_cols)} features, "
          f"{df['date'].nunique()} months")

    # ---- expanding-window predictions ----------------------------------
    dates = sorted(df["date"].unique())
    preds = {}
    model = None
    for i, d in enumerate(dates):
        train = df[df["date"] < d]
        if train["date"].nunique() < 36:
            continue
        if model is None or i % 3 == 0:  # quarterly refit
            cut = int(len(train) * 0.9)
            model = lgb.LGBMRegressor(**LGB_PARAMS)
            model.fit(train[feat_cols].iloc[:cut], train["target"].iloc[:cut],
                      eval_set=[(train[feat_cols].iloc[cut:], train["target"].iloc[cut:])],
                      callbacks=[lgb.early_stopping(50, verbose=False)])
        cur = df[df["date"] == d]
        preds[d] = pd.Series(model.predict(cur[feat_cols]), index=cur["code"].values)
    print(f"predicted months: {len(preds)}")

    # ---- backtest via precomputed scores --------------------------------
    score_lookup = {d.strftime("%Y-%m"): s for d, s in preds.items()}

    def score_fn(hist: pd.DataFrame) -> pd.Series:
        key = hist.index[-1].strftime("%Y-%m")
        s = score_lookup.get(key)
        if s is None:
            return pd.Series(dtype=float)
        # membership codes in backtest are digits.EX; predictions are ts_code
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
    print("\n=== E47 LightGBM 指增 ===")
    print({k: v for k, v in r.items()})
    print(f"前半段超额 {float(curve.iloc[half]/curve.iloc[0]-1)*100:+.1f}% / "
          f"后半段超额 {float(curve.iloc[-1]/curve.iloc[half]-1)*100:+.1f}%")
    yearly = curve.resample('YE').last().pct_change().dropna() * 100
    print("逐年超额:", {ts.year: round(v, 1) for ts, v in yearly.items()})


if __name__ == "__main__":
    main()
