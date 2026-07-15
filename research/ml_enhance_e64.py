"""E64: E47 with the universe widened to point-in-time HS300 ∪ CSI500.

Everything except membership is ml_enhance.py (E47) verbatim — same features,
frozen LGB hyperparameters, expanding window, quarterly refits, monthly
top-50, same cost model and gates. The single change: at each month-end the
eligible cross-section is the union of both indexes' PIT members (~800 names
instead of ~300). Breadth is the one structural edge of institutional CTA/ML
shops that is replicable at retail scale (see cn_quant_public_notes.md).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from qtrade.backtest.relative import backtest_topk  # noqa: E402
from qtrade.data.store import BarStore  # noqa: E402

PIT = REPO / "data_store" / "pit_ts"

spec = importlib.util.spec_from_file_location("ml_enhance", REPO / "research" / "ml_enhance.py")
M = importlib.util.module_from_spec(spec)
sys.modules.setdefault("ml_enhance", M)
spec.loader.exec_module(M)


def load_panels_union():
    """ml_enhance.load_panels with membership = HS300 ∪ CSI500 PIT."""
    store = BarStore()
    w300 = pd.read_parquet(PIT / "hs300_weights.parquet")
    w500 = pd.read_parquet(PIT / "csi500_weights.parquet")
    weights = pd.concat([w300, w500], ignore_index=True)
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

    def daily_panel(d):
        return pd.DataFrame({k: v[~v.index.duplicated(keep="last")] for k, v in d.items()}
                            ).reindex(closes.index).ffill()

    def event_panel(d):
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


def main():
    store = BarStore()
    closes, vols, panels, membership = load_panels_union()
    print(f"universe: {closes.shape[1]} codes x {len(closes)} days")
    feats = M.build_features(closes, vols, panels)
    bench = store.load("ashare_index", "SH_000300", "1d")["close"]
    bench_m = bench.resample("ME").last().pct_change()

    month_ends = closes.resample("ME").last().index
    member_map = {}
    for s, g in membership.groupby("snap"):
        member_map.setdefault(s, set()).update(g["code"])
    snap_dates = sorted(member_map)

    rows = []
    m_close = closes.resample("ME").last()
    fwd_ret = m_close.pct_change().shift(-1)
    for d in month_ends:
        snap = max((s for s in snap_dates if pd.Timestamp(s, tz="UTC") <= d), default=None)
        if snap is None:
            continue
        upto = closes.loc[:d]
        if len(upto) < 260:
            continue
        di = upto.index[-1]
        fvals = {name: f.loc[di] for name, f in feats.items()}
        tgt = fwd_ret.loc[d] - (bench_m.shift(-1).loc[d] if d in bench_m.index else np.nan)
        for c in [c for c in member_map[snap] if c in closes.columns]:
            row = {name: fvals[name].get(c, np.nan) for name in feats}
            row.update({"date": d, "code": c, "target": tgt.get(c, np.nan)})
            rows.append(row)
    df = pd.DataFrame(rows).replace([np.inf, -np.inf], np.nan)
    feat_cols = list(feats)
    df = df.dropna(subset=["target"])
    print(f"samples: {len(df)} rows x {len(feat_cols)} features, "
          f"{df['date'].nunique()} months")

    dates = sorted(df["date"].unique())
    preds = {}
    model = None
    for i, d in enumerate(dates):
        train = df[df["date"] < d]
        if train["date"].nunique() < 36:
            continue
        if model is None or i % 3 == 0:
            cut = int(len(train) * 0.9)
            model = lgb.LGBMRegressor(**M.LGB_PARAMS)
            model.fit(train[feat_cols].iloc[:cut], train["target"].iloc[:cut],
                      eval_set=[(train[feat_cols].iloc[cut:], train["target"].iloc[cut:])],
                      callbacks=[lgb.early_stopping(50, verbose=False)])
        cur = df[df["date"] == d]
        preds[d] = pd.Series(model.predict(cur[feat_cols]), index=cur["code"].values)
    print(f"predicted months: {len(preds)}")

    score_lookup = {d.strftime("%Y-%m"): s for d, s in preds.items()}

    def score_fn(hist: pd.DataFrame) -> pd.Series:
        s = score_lookup.get(hist.index[-1].strftime("%Y-%m"))
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
    print("\n=== E64 LightGBM 指增（HS300∪CSI500, top-50）===")
    print({k: v for k, v in r.items()})
    print(f"前半段超额 {float(curve.iloc[half]/curve.iloc[0]-1)*100:+.1f}% / "
          f"后半段超额 {float(curve.iloc[-1]/curve.iloc[half]-1)*100:+.1f}%")
    yearly = curve.resample('YE').last().pct_change().dropna() * 100
    print("逐年超额:", {ts.year: round(v, 1) for ts, v in yearly.items()})
    print("\nE47 基线: 净+2.54%/年 IR 0.22 毛+6.3%/年 | 门槛: 净>3% 且 IR>0.5 且 两半正")


if __name__ == "__main__":
    main()
