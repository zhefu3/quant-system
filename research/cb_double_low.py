"""E63: convertible-bond double-low rotation (prereg 2026-07-15).

Frozen spec: score = close + premium(%); month-end pick lowest-20 equal
weight; exclusions: listed <3 months, issue size <3亿 (data limitation:
remaining size unavailable -> issue size proxy, recorded), rating below AA-
(current rating from master list — not PIT, recorded), close >130 (redemption
-risk proxy; announcement dates unavailable, recorded). Costs 0.01% + 0.05%
per side. THE gate is the post-2021 segment: net ann >6%, sharpe >0.8,
maxDD <15%. Pre-2021 glory (before the strategy went mainstream) is context,
not evidence.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ROOT = Path(__file__).resolve().parents[1] / "data_store" / "cn_cb"
K = 20
FEE, SLIP = 0.0001, 0.0005
MIN_LIST_MONTHS = 3
MIN_SIZE_YI = 3.0
RATINGS_OK = {"AAA", "AA+", "AA", "AA-"}
PX_REDEEM = 130.0
SEG = "2021-01-01"


def load_panels():
    master = pd.read_parquet(ROOT / "bonds.parquet")
    master["code"] = master["债券代码"].astype(str)
    master = master.set_index("code")
    px, prem = {}, {}
    for f in (ROOT / "value").glob("*.parquet"):
        code = f.stem
        v = pd.read_parquet(f)
        idx = pd.to_datetime(v["日期"])
        px[code] = pd.Series(pd.to_numeric(v["收盘价"], errors="coerce").values, index=idx)
        prem[code] = pd.Series(pd.to_numeric(v["转股溢价率"], errors="coerce").values, index=idx)
    px = pd.DataFrame(px).sort_index()
    prem = pd.DataFrame(prem).sort_index().reindex(px.index)
    return master, px, prem


def eligible(master: pd.DataFrame, code: str, d: pd.Timestamp,
             price: float) -> bool:
    if code not in master.index:
        return False
    row = master.loc[code]
    listed = pd.to_datetime(row.get("上市时间"), errors="coerce")
    if pd.isna(listed) or d < listed + pd.DateOffset(months=MIN_LIST_MONTHS):
        return False
    size = pd.to_numeric(row.get("发行规模"), errors="coerce")
    if pd.isna(size) or size < MIN_SIZE_YI:
        return False
    rating = str(row.get("信用评级", ""))
    if rating not in RATINGS_OK:
        return False
    return price <= PX_REDEEM


def run():
    master, px, prem = load_panels()
    m_px = px.resample("ME").last()
    m_prem = prem.resample("ME").last()
    print(f"panel: {px.shape[1]} bonds, {m_px.index[0].date()} -> {m_px.index[-1].date()}")

    rets, holdings_n = [], []
    prev_hold: set[str] = set()
    for i in range(len(m_px.index) - 1):
        d, d1 = m_px.index[i], m_px.index[i + 1]
        row_px, row_prem = m_px.iloc[i], m_prem.iloc[i]
        cand = []
        for code in m_px.columns:
            p, pr = row_px.get(code), row_prem.get(code)
            if pd.isna(p) or pd.isna(pr) or not eligible(master, code, d, p):
                continue
            cand.append((code, p + pr))  # premium already in percent units
        if len(cand) < K:
            rets.append((d1, 0.0, 0.0))
            prev_hold = set()
            continue
        picks = [c for c, _ in sorted(cand, key=lambda kv: kv[1])[:K]]
        # next-month return per bond; exit at last available price on delisting
        prets = []
        for c in picks:
            p0 = row_px[c]
            p1 = m_px.iloc[i + 1].get(c)
            if pd.isna(p1):
                s = px[c].dropna()
                p1 = s[s.index <= d1].iloc[-1] if len(s[s.index <= d1]) else p0
            prets.append(p1 / p0 - 1)
        gross = float(np.mean(prets))
        turnover = len(set(picks) - prev_hold) / K * 2  # round-trip fraction
        net = gross - turnover * (FEE + SLIP)
        rets.append((d1, gross, net))
        holdings_n.append(len(cand))
        prev_hold = set(picks)

    df = pd.DataFrame(rets, columns=["date", "gross", "net"]).set_index("date")
    print(f"avg eligible pool: {np.mean(holdings_n):.0f}")

    def seg_stats(r: pd.Series, label: str):
        r = r.dropna()
        if not len(r):
            return
        ann = (1 + r).prod() ** (12 / len(r)) - 1
        sharpe = r.mean() / r.std() * np.sqrt(12) if r.std() > 0 else 0
        eq = (1 + r).cumprod()
        dd = float((eq / eq.cummax() - 1).min())
        print(f"{label}: 净年化 {ann:+.1%} | Sharpe {sharpe:.2f} | maxDD {dd:.1%} "
              f"| {len(r)} 个月")
        return ann, sharpe, dd

    print("\n=== E63 双低轮动（净成本）===")
    seg_stats(df["net"], "全期        ")
    post = df["net"][df.index >= SEG]
    res = seg_stats(post, f"后段({SEG[:4]}起)")
    yearly = df["net"].groupby(df.index.year).apply(lambda x: ((1 + x).prod() - 1) * 100)
    print("逐年净收益%:", {int(y): round(v, 1) for y, v in yearly.items()})

    if res:
        ann, sharpe, dd = res
        passed = ann > 0.06 and sharpe > 0.8 and dd > -0.15
        print(f"\n门槛(后段): 净年化>6% 且 Sharpe>0.8 且 maxDD<15%")
        print(f"判决: {'✅ 过 -> 观察账本(第九本)' if passed else '❌ 不过'}")


if __name__ == "__main__":
    run()
