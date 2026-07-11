"""E46: fundamental index enhancement on Tushare PIT data (the real test).

Factors pre-registered in research/log.md BEFORE running:
  EP = 1/pe_ttm, BP = 1/pb          (daily_basic — daily, no lag needed)
  ROE, netprofit_yoy                (fina_indicator — effective from ann_date)

Universe: official HS300 membership at each month-end (Tushare weights file).
Selection: top-50 composite z. Costs: A-share retail incl stamp duty.
Gates: post-cost excess >3%/yr AND IR>0.5 AND both halves positive.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qtrade.backtest.relative import backtest_topk  # noqa: E402
from qtrade.data.store import BarStore  # noqa: E402

PIT = Path(__file__).resolve().parents[1] / "data_store" / "pit_ts"


def load_data():
    store = BarStore()
    weights = pd.read_parquet(PIT / "hs300_weights.parquet")
    union = sorted(weights["con_code"].unique())

    closes, ep, bp = {}, {}, {}
    roe_events, growth_events = {}, {}
    for code in union:
        sym = code.replace(".", "_")
        try:
            closes[code] = store.load("ashare_ts", code, "1d")["close"]
        except FileNotFoundError:
            continue
        f_db = PIT / "daily_basic" / f"{sym}.parquet"
        if f_db.exists():
            db = pd.read_parquet(f_db)
            idx = pd.to_datetime(db["trade_date"]).dt.tz_localize("Asia/Shanghai").dt.tz_convert("UTC")
            ep[code] = pd.Series((1.0 / db["pe_ttm"]).values, index=idx).sort_index()
            bp[code] = pd.Series((1.0 / db["pb"]).values, index=idx).sort_index()
        f_fi = PIT / "fina" / f"{sym}.parquet"
        if f_fi.exists():
            fi = pd.read_parquet(f_fi).dropna(subset=["ann_date"])
            ann = pd.to_datetime(fi["ann_date"]).dt.tz_localize("Asia/Shanghai").dt.tz_convert("UTC")
            roe_events[code] = pd.Series(pd.to_numeric(fi["roe"], errors="coerce").values,
                                         index=ann).sort_index()
            growth_events[code] = pd.Series(pd.to_numeric(fi["netprofit_yoy"], errors="coerce").values,
                                            index=ann).sort_index()

    closes = pd.DataFrame(closes)
    # Event series -> daily panels, effective FROM publication date (ffill).
    def eventize(events: dict) -> pd.DataFrame:
        out = {}
        for code, s in events.items():
            s = s[~s.index.duplicated(keep="last")]
            out[code] = s.reindex(closes.index.union(s.index)).ffill().reindex(closes.index)
        return pd.DataFrame(out)

    panels = {
        "EP": pd.DataFrame(ep).reindex(closes.index).ffill(),
        "BP": pd.DataFrame(bp).reindex(closes.index).ffill(),
        "ROE": eventize(roe_events),
        "GROWTH": eventize(growth_events),
    }
    membership = weights.rename(columns={"trade_date": "snap", "con_code": "code"})
    membership["snap"] = pd.to_datetime(membership["snap"]).dt.strftime("%Y-%m-%d")
    # backtest_topk expects baostock-style codes (sh.600000); convert.
    membership["code"] = membership["code"].map(
        lambda c: f"{c.split('.')[1].lower()}.{c.split('.')[0]}")
    return closes, panels, membership


def make_score_fn(panels: dict, names: list[str]):
    def zscore(s: pd.Series) -> pd.Series:
        sd = s.std()
        return (s - s.mean()) / (sd if sd and not np.isnan(sd) else 1.0)

    def score(hist: pd.DataFrame) -> pd.Series:
        d = hist.index[-1]
        total = None
        for n in names:
            panel = panels[n]
            cols = [c for c in hist.columns if c in panel.columns]
            row = panel.loc[:d].iloc[-1] if len(panel.loc[:d]) else None
            if row is None:
                continue
            z = zscore(row[cols].replace([np.inf, -np.inf], np.nan))
            total = z if total is None else total.add(z, fill_value=0.0)
        return total.dropna() if total is not None else pd.Series(dtype=float)

    return score


def main():
    store = BarStore()
    closes, panels, membership = load_data()
    print(f"panel: {closes.shape[1]} names x {len(closes)} days")
    bench = store.load("ashare_index", "SH_000300", "1d")["close"]

    # backtest_topk uses digit.EX symbols; closes here keyed by ts_code — remap.
    remap = {c: f"{c.split('.')[0]}.{c.split('.')[1]}" for c in closes.columns}
    closes = closes.rename(columns=remap)
    panels = {k: v.rename(columns=remap) for k, v in panels.items()}

    for label, names in [("四因子 EP+BP+ROE+GROWTH", ["EP", "BP", "ROE", "GROWTH"]),
                         ("双因子 质量+价值 (ROE+EP)", ["ROE", "EP"])]:
        r = backtest_topk(closes, membership, make_score_fn(panels, names), bench, k=50)
        curve = r.pop("excess_curve")
        half = len(curve) // 2
        h1 = float(curve.iloc[half] / curve.iloc[0] - 1) * 100
        h2 = float(curve.iloc[-1] / curve.iloc[half] - 1) * 100
        print(f"\n=== {label} ===")
        print({k: v for k, v in r.items()})
        print(f"前半段超额 {h1:+.1f}% / 后半段超额 {h2:+.1f}%")


if __name__ == "__main__":
    main()
