"""E62 evidence relabel: "33 years" is calendar span, not 10-asset evidence span.

The external review forced the right question: what was the universe actually
holding in each era of that 33-year window? This reruns the E62 V2 book
verbatim (same script constants, same net_returns) and reports it BY ERA,
with the era boundaries set by the ETFs' real first-bar dates — so "crisis
year 2000 was positive" can be read for what it is: a two-ETF sample.

No parameter, gate, or verdict changes — this is labeling, not research.
Usage: .venv/bin/python research/etf_relabel.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qtrade.backtest.portfolio import run_portfolio  # noqa: E402
from qtrade.data.store import BarStore  # noqa: E402

from etf_trend_exec import RULES, UNIVERSE, book, net_returns  # noqa: E402


def main() -> None:
    store = BarStore()
    bars = {s: store.load("etf", s, "1d") for s in UNIVERSE}
    starts = pd.Series({s: b.index[0] for s, b in bars.items()}).sort_values()
    print("entry dates:")
    for s, t in starts.items():
        print(f"  {s:4s} {t.date()}")

    _, d = run_portfolio(book(), bars, RULES, "1d", allocation="equal",
                         rebalance_eps=0.02, align="ffill", return_details=True)
    W, closes = d["weights"], d["closes"]
    r = net_returns(W.clip(lower=0), closes)  # V2 long-flat, the adopted variant

    # eras open whenever membership widens (clustered entries merged by year)
    boundaries = [(starts.iloc[0], None)]
    for s, t in starts.items():
        if t > boundaries[-1][0] + pd.Timedelta(days=200):
            boundaries.append((t, s))
    rows = []
    for i, (t0, _) in enumerate(boundaries):
        t1 = boundaries[i + 1][0] if i + 1 < len(boundaries) else r.index[-1]
        seg = r[(r.index >= t0) & (r.index < t1)]
        if len(seg) < 60:
            continue
        width = int((starts <= t0).sum())
        sharpe = float(seg.mean() / seg.std() * np.sqrt(252)) if seg.std() > 0 else 0.0
        eq = (1 + seg).cumprod()
        rows.append({"era": f"{t0.date()} -> {t1.date()}",
                     "n_etf": width,
                     "years": round(len(seg) / 252, 1),
                     "sharpe": round(sharpe, 2),
                     "ann_ret_pct": round((eq.iloc[-1] ** (252 / len(seg)) - 1) * 100, 2),
                     "max_dd_pct": round(float((eq / eq.cummax() - 1).min()) * 100, 1)})
    df = pd.DataFrame(rows)
    print("\nE62 V2 (long-flat) by era:")
    print(df.to_string(index=False))

    full_sharpe = float(r.mean() / r.std() * np.sqrt(252))
    ten = r[r.index >= starts.max()]
    ten_sharpe = float(ten.mean() / ten.std() * np.sqrt(252))
    print(f"\nfull window Sharpe {full_sharpe:.2f} ({len(r) / 252:.1f}y calendar span)")
    print(f"full-breadth era only (10 ETFs, {starts.max().date()}+): "
          f"Sharpe {ten_sharpe:.2f} over {len(ten) / 252:.1f}y")


if __name__ == "__main__":
    main()
