"""Cross-book view: what are the nine books, added together, actually betting?

E65's lesson institutionalized: two books held ~0% of the same names yet
correlated 0.77 monthly — holdings overlap is not return overlap. This section
measures return correlation directly from the forward paper records, weekly,
so redundancy is seen while it accumulates rather than discovered at
allocation time. Observation only — it changes no book's behavior.
"""

from __future__ import annotations

import pandas as pd

from ..presets import PRESETS
from .paper import DEFAULT_ROOT

MIN_OVERLAP_DAYS = 8   # below this a correlation is noise, not information
REDUNDANCY_FLAG = 0.6  # pairs above this get called out (E65 fired at 0.77)

ASSET_CLASS = {"crypto": "crypto", "crypto_swap": "crypto",
               "cnfutures": "cn_commodity", "futures_ibkr": "us_futures",
               "us_etf": "us_equity", "ashare": "cn_equity",
               "cn_cb": "cn_convertible"}


def _daily_returns() -> pd.DataFrame:
    out = {}
    for name in PRESETS:
        f = DEFAULT_ROOT / name / "equity.csv"
        if not f.exists():
            continue
        eq = pd.read_csv(f, parse_dates=["ts"])
        s = (eq.set_index(pd.DatetimeIndex(eq["ts"]).tz_localize(None))["equity"]
               .resample("1D").last().dropna())
        if len(s) >= 2:
            out[name] = s.pct_change().dropna()
    return pd.DataFrame(out)


def cross_book_section() -> None:
    print("--- 跨账本视角（E65 教训制度化: 相关性周周看, 不等分配时才发现）---")
    rets = _daily_returns()
    if rets.empty:
        print("(无足够记录)")
        return

    # asset-class rollup
    rows = []
    for name in rets.columns:
        f = DEFAULT_ROOT / name / "equity.csv"
        eq = pd.read_csv(f)
        cls = ASSET_CLASS.get(PRESETS[name].market, PRESETS[name].market)
        rows.append({"book": name, "class": cls,
                     "equity": float(eq["equity"].iloc[-1]),
                     "vol_ann": float(rets[name].std() * (365 ** 0.5))})
    df = pd.DataFrame(rows)
    by_class = df.groupby("class")["equity"].agg(["count", "sum"])
    by_class["share"] = by_class["sum"] / by_class["sum"].sum()
    print("资产类分布: " + " | ".join(
        f"{c}: {int(r['count'])}本 {r['share']:.0%}" for c, r in by_class.iterrows()))

    # pairwise correlation on overlapping days
    names = list(rets.columns)
    flagged = []
    printed_any = False
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            both = rets[[a, b]].dropna()
            if len(both) < MIN_OVERLAP_DAYS:
                continue
            corr = float(both[a].corr(both[b]))
            if pd.isna(corr):
                continue
            printed_any = True
            if corr > REDUNDANCY_FLAG:
                mark = "  ⚠ 收益冗余"
                flagged.append((a, b, corr))
            elif corr < -REDUNDANCY_FLAG:
                mark = "  (强负相关 — 分散贡献, 非冗余)"
            else:
                mark = ""
            print(f"  {a} × {b}: ρ={corr:+.2f} (n={len(both)}d){mark}")
    if not printed_any:
        print(f"  (所有账本对重叠记录 <{MIN_OVERLAP_DAYS} 天, 相关性待积累)")
    if flagged:
        print("  注: A/B 平行账本(crypto_core×v2/4h)高相关是设计使然; "
              "跨资产类的高相关才是分配层要警惕的")
