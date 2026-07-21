"""E67: convertible-bond low-premium WEEKLY rotation (prereg 2026-07-21,
frozen before this ran — research/log.md).

The jisilu-community favorite, judged by our frozen gates. Spec: E63's
eligibility verbatim (imported, not re-implemented), rank by 转股溢价率
ascending (pure low premium), top-20 equal weight, Friday-close signal,
hold one week. Costs 0.01%+0.05%/side headline with a pre-committed
0.10%/side sensitivity — the conservative one decides. Gate: post-2021
net ann >6%, Sharpe >0.8, maxDD <15% at BOTH cost levels.

Honest priors in the prereg: weekly turnover is the likely killer; low
premium = equity-like bonds, expect deeper drawdowns than double-low.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

spec = importlib.util.spec_from_file_location("cb_double_low",
                                              REPO / "research" / "cb_double_low.py")
E63 = importlib.util.module_from_spec(spec)
sys.modules.setdefault("cb_double_low", E63)
spec.loader.exec_module(E63)

K = 20
FEE, SLIP, SLIP_THIN = 0.0001, 0.0005, 0.0010
SEG = "2021-01-01"


def run():
    master, px, prem = E63.load_panels()
    w_px = px.resample("W-FRI").last()
    w_prem = prem.resample("W-FRI").last()
    print(f"panel: {px.shape[1]} bonds, weekly {w_px.index[0].date()} -> "
          f"{w_px.index[-1].date()} ({len(w_px)} weeks)")

    rows, pool_n, turns = [], [], []
    prev_hold: set[str] = set()
    for i in range(len(w_px.index) - 1):
        d, d1 = w_px.index[i], w_px.index[i + 1]
        row_px, row_prem = w_px.iloc[i], w_prem.iloc[i]
        cand = []
        for code in w_px.columns:
            p, pr = row_px.get(code), row_prem.get(code)
            if pd.isna(p) or pd.isna(pr) or not E63.eligible(master, code, d, p):
                continue
            cand.append((code, pr))  # rank: premium ascending, nothing else
        if len(cand) < K:
            rows.append((d1, 0.0, 0.0, 0.0))
            pool_n.append(len(cand))
            prev_hold = set()
            continue
        picks = [c for c, _ in sorted(cand, key=lambda kv: kv[1])[:K]]
        prets = []
        for c in picks:
            p0 = row_px[c]
            p1 = w_px.iloc[i + 1].get(c)
            if pd.isna(p1):  # delisted mid-week -> exit at last available price
                s = px[c].dropna()
                p1 = s[s.index <= d1].iloc[-1] if len(s[s.index <= d1]) else p0
            prets.append(p1 / p0 - 1)
        gross = float(np.mean(prets))
        turnover = len(set(picks) - prev_hold) / K * 2  # round-trip fraction
        turns.append(turnover / 2)
        rows.append((d1, gross,
                     gross - turnover * (FEE + SLIP),
                     gross - turnover * (FEE + SLIP_THIN)))
        pool_n.append(len(cand))
        prev_hold = set(picks)

    df = pd.DataFrame(rows, columns=["date", "gross", "net", "net_thin"]).set_index("date")
    print(f"avg eligible pool: {np.mean(pool_n):.0f} | "
          f"avg weekly one-way turnover: {np.mean(turns):.0%}")

    def seg_stats(r: pd.Series, label: str, ppy: int = 52):
        r = r.dropna()
        if not len(r):
            return None
        ann = (1 + r).prod() ** (ppy / len(r)) - 1
        sharpe = r.mean() / r.std() * np.sqrt(ppy) if r.std() > 0 else 0
        eq = (1 + r).cumprod()
        dd = float((eq / eq.cummax() - 1).min())
        print(f"{label}: 净年化 {ann:+.1%} | Sharpe {sharpe:.2f} | maxDD {dd:.1%} "
              f"| {len(r)} 周")
        return ann, sharpe, dd

    print("\n=== E67 低溢价周频轮动 ===")
    seg_stats(df["gross"], "全期 毛        ")
    seg_stats(df["net"], "全期 净(5bp)   ")
    res = seg_stats(df["net"][df.index >= SEG], "后段(2021,5bp) ")
    res_thin = seg_stats(df["net_thin"][df.index >= SEG], "后段(2021,10bp)")
    yearly = df["net"].groupby(df.index.year).apply(lambda x: ((1 + x).prod() - 1) * 100)
    print("逐年净%(5bp):", {int(y): round(v, 1) for y, v in yearly.items()})

    print("\n门槛(后段, 两种成本都须过): 净年化>6% 且 Sharpe>0.8 且 maxDD<15%")
    if res and res_thin:
        for tag, rr in [("5bp", res), ("10bp", res_thin)]:
            ann, sh, dd = rr
            ok = ann > 0.06 and sh > 0.8 and dd > -0.15
            print(f"  [{tag}] {'✅' if ok else '❌'} "
                  f"(ann {ann:+.1%}/Sharpe {sh:.2f}/DD {dd:.1%})")
    return df


if __name__ == "__main__":
    run()
