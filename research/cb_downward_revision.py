"""E65: convertible-bond downward-revision game (prereg 2026-07-15, frozen
before this backtest ran — see research/log.md E65 entry).

Thesis: bonds trading deep below conversion (转股价值 low) embed a downward-
revision option (issuers cut the conversion price to dodge the put / enable
conversion). Frozen spec: month-end, among eligible bonds, rank by 转股价值
ascending, take lowest-20 equal weight, hold one month.

Eligible: listed >=3 months, issue size >=3亿, rating in {AAA,AA+,AA,AA-},
close <=115 (PIT-safe call/conversion exclusion), 转股价值 <=80 (stock <=80%
of conversion — the revision-trigger zone). Costs 0.01%+0.05% per side
(headline) with a 0.10%/side thin-liquidity sensitivity. THE gate is the
post-2021 segment: net ann >6%, sharpe >0.8, maxDD <15%.

Data reused wholesale from E63 (data_store/cn_cb/) — no new fetch. adj_logs
events are used only for ex-post attribution (attribution() below), never in
the gated signal.
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
SLIP_THIN = 0.0010  # pre-committed thin-liquidity sensitivity
MIN_LIST_MONTHS = 3
MIN_SIZE_YI = 3.0
RATINGS_OK = {"AAA", "AA+", "AA", "AA-"}
PX_CAP = 115.0        # revision candidates trade near/below par; PIT call proxy
CV_CAP = 80.0         # 转股价值 <=80 -> stock <=80% of conversion (trigger zone)
SEG = "2021-01-01"


def load_panels():
    master = pd.read_parquet(ROOT / "bonds.parquet")
    master["code"] = master["债券代码"].astype(str)
    master = master.set_index("code")
    px, cv = {}, {}
    for f in (ROOT / "value").glob("*.parquet"):
        code = f.stem
        v = pd.read_parquet(f)
        idx = pd.to_datetime(v["日期"])
        px[code] = pd.Series(pd.to_numeric(v["收盘价"], errors="coerce").values, index=idx)
        cv[code] = pd.Series(pd.to_numeric(v["转股价值"], errors="coerce").values, index=idx)
    px = pd.DataFrame(px).sort_index()
    cv = pd.DataFrame(cv).sort_index().reindex(px.index)
    return master, px, cv


def eligible(master: pd.DataFrame, code: str, d: pd.Timestamp,
             price: float, cval: float) -> bool:
    if code not in master.index:
        return False
    if pd.isna(price) or pd.isna(cval):
        return False
    if price > PX_CAP or cval > CV_CAP:
        return False
    row = master.loc[code]
    listed = pd.to_datetime(row.get("上市时间"), errors="coerce")
    if pd.isna(listed) or d < listed + pd.DateOffset(months=MIN_LIST_MONTHS):
        return False
    size = pd.to_numeric(row.get("发行规模"), errors="coerce")
    if pd.isna(size) or size < MIN_SIZE_YI:
        return False
    return str(row.get("信用评级", "")) in RATINGS_OK


def _seg_stats(r: pd.Series, label: str):
    r = r.dropna()
    if not len(r):
        print(f"{label}: (无数据)")
        return None
    ann = (1 + r).prod() ** (12 / len(r)) - 1
    sharpe = r.mean() / r.std() * np.sqrt(12) if r.std() > 0 else 0
    eq = (1 + r).cumprod()
    dd = float((eq / eq.cummax() - 1).min())
    print(f"{label}: 净年化 {ann:+.1%} | Sharpe {sharpe:.2f} | maxDD {dd:.1%} "
          f"| {len(r)} 个月")
    return ann, sharpe, dd


def run():
    master, px, cv = load_panels()
    m_px = px.resample("ME").last()
    m_cv = cv.resample("ME").last()
    print(f"panel: {px.shape[1]} bonds, {m_px.index[0].date()} -> {m_px.index[-1].date()}")

    rows, pool_n = [], []
    prev_hold: set[str] = set()
    for i in range(len(m_px.index) - 1):
        d, d1 = m_px.index[i], m_px.index[i + 1]
        row_px, row_cv = m_px.iloc[i], m_cv.iloc[i]
        cand = []
        for code in m_px.columns:
            p, c = row_px.get(code), row_cv.get(code)
            if eligible(master, code, d, p, c):
                cand.append((code, c))  # rank key = 转股价值 (ascending)
        if len(cand) < K:
            rows.append((d1, 0.0, 0.0, 0.0))
            prev_hold = set()
            pool_n.append(len(cand))
            continue
        picks = [c for c, _ in sorted(cand, key=lambda kv: kv[1])[:K]]
        prets = []
        for c in picks:
            p0 = row_px[c]
            p1 = m_px.iloc[i + 1].get(c)
            if pd.isna(p1):  # delisted mid-month -> exit at last available price
                s = px[c].dropna()
                p1 = s[s.index <= d1].iloc[-1] if len(s[s.index <= d1]) else p0
            prets.append(p1 / p0 - 1)
        gross = float(np.mean(prets))
        turnover = len(set(picks) - prev_hold) / K * 2
        net = gross - turnover * (FEE + SLIP)
        net_thin = gross - turnover * (FEE + SLIP_THIN)
        rows.append((d1, gross, net, net_thin))
        pool_n.append(len(cand))
        prev_hold = set(picks)

    df = pd.DataFrame(rows, columns=["date", "gross", "net", "net_thin"]).set_index("date")
    print(f"avg eligible pool: {np.mean(pool_n):.0f} (min {min(pool_n)}, max {max(pool_n)})")

    print("\n=== E65 下修博弈（净成本）===")
    _seg_stats(df["gross"], "全期 毛      ")
    _seg_stats(df["net"], "全期 净(5bp) ")
    post = df["net"][df.index >= SEG]
    post_thin = df["net_thin"][df.index >= SEG]
    res = _seg_stats(post, f"后段({SEG[:4]}起,5bp) ")
    res_thin = _seg_stats(post_thin, f"后段({SEG[:4]}起,10bp)")
    yearly = df["net"].groupby(df.index.year).apply(lambda x: ((1 + x).prod() - 1) * 100)
    print("逐年净收益%(5bp):", {int(y): round(v, 1) for y, v in yearly.items()})

    print("\n门槛(后段): 净年化>6% 且 Sharpe>0.8 且 maxDD<15% (以保守=10bp 判决)")
    if res and res_thin:
        for tag, rr in [("5bp", res), ("10bp", res_thin)]:
            ann, sh, dd = rr
            ok = ann > 0.06 and sh > 0.8 and dd > -0.15
            print(f"  [{tag}] {'✅ 过' if ok else '❌ 不过'} "
                  f"(ann {ann:+.1%}/Sharpe {sh:.2f}/DD {dd:.1%})")
    return df


if __name__ == "__main__":
    run()
