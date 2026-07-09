"""E36: run the factor scoreboard on the 10-symbol crypto panel.

    .venv/bin/python research/factor_scan.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qtrade.data.store import BarStore  # noqa: E402
from qtrade.presets import CRYPTO_CORE  # noqa: E402
from qtrade.research_factors import evaluate  # noqa: E402


def main():
    store = BarStore()
    p = CRYPTO_CORE
    bars = {s: store.load("crypto", s, "1h") for s in p.symbols}
    start = max(b.index[0] for b in bars.values())
    bars = {s: b[b.index >= start] for s, b in bars.items()}

    panel = {
        "closes": pd.DataFrame({s: b["close"] for s, b in bars.items()}),
        "highs": pd.DataFrame({s: b["high"] for s, b in bars.items()}),
        "lows": pd.DataFrame({s: b["low"] for s, b in bars.items()}),
        "volumes": pd.DataFrame({s: b["volume"] for s, b in bars.items()}),
    }
    # basis where swap data exists (aligned to the same grid)
    basis = {}
    for s in p.symbols:
        try:
            swap = store.load("crypto_swap", s, "1h")["close"]
            basis[s] = (swap.reindex(panel["closes"].index) / panel["closes"][s] - 1.0)
        except FileNotFoundError:
            pass
    if basis:
        panel["basis"] = pd.DataFrame(basis)

    print(f"panel {panel['closes'].shape[1]} symbols from {start.date()}, "
          f"{len(panel['closes'])} bars\n")
    res = evaluate(panel)
    for h in sorted(res["fwd_h"].unique()):
        sub = res[res["fwd_h"] == h].sort_values("IC", key=abs, ascending=False)
        print(f"===== forward {h}h =====")
        for _, r in sub.iterrows():
            print(f"  {r['factor']:16s} IC {r['IC']:+.4f}  同号年 {r['yrs_same_sign']}  "
                  f"逐年 {r['ic_by_year']}")
        print()


if __name__ == "__main__":
    main()
