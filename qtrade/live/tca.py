"""TCA scaffold: measure real execution cost against the assumed cost model.

Institutions reconcile "the slippage we assume" with "the slippage we pay"
on every fill; our backtest gates all rest on assumed costs, so the moment
real orders flow, this is the first thing to check. The recording side is
wired into the live broker path now — dormant until --send is used — so the
evidence accumulates from the very first real fill.

Realized slippage per fill: signed (fill/arrival - 1), positive = paid more
than the decision price. Compare its distribution to the rules' assumed
slip; a sustained excess means the cost model (and every gate that used it)
is optimistic.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

LIVE_ROOT = Path(__file__).resolve().parents[2] / "outputs" / "live"


def record_fill(book_dir: Path, ts: str, symbol: str, side: str,
                contracts: float, arrival_px: float, order: dict) -> None:
    """Append one fill's TCA row. Never raises — a TCA bookkeeping failure
    must not disturb the order path."""
    try:
        fill_px = order.get("average") or order.get("price")
        row = {"ts": ts, "symbol": symbol, "side": side, "contracts": contracts,
               "arrival_px": arrival_px, "fill_px": fill_px,
               "order_id": order.get("id")}
        if fill_px and arrival_px:
            sign = 1 if side == "buy" else -1
            row["slip_bps"] = (float(fill_px) / float(arrival_px) - 1) * sign * 1e4
        f = book_dir / "tca.csv"
        pd.DataFrame([row]).to_csv(f, mode="a", header=not f.exists(), index=False)
    except Exception as e:  # noqa: BLE001 — bookkeeping must not break trading
        print(f"  TCA record failed ({type(e).__name__}) — order unaffected")


def run_tca() -> None:
    """Realized vs assumed execution cost, per live book."""
    from ..markets.rules import BY_NAME
    from ..presets import PRESETS

    print(f"══════ qtrade TCA · 实付成本 vs 假设成本 ══════")
    found = False
    for name, preset in PRESETS.items():
        f = LIVE_ROOT / name / "tca.csv"
        if not f.exists():
            continue
        df = pd.read_csv(f)
        filled = df.dropna(subset=["slip_bps"]) if "slip_bps" in df else df.iloc[0:0]
        if not len(filled):
            print(f"{name}: {len(df)} orders recorded, none with fill prices yet")
            continue
        found = True
        assumed_bps = preset.rules.slippage * 1e4
        med = float(filled["slip_bps"].median())
        p90 = float(filled["slip_bps"].quantile(0.9))
        verdict = ("⚠ 实付超假设 — 成本模型偏乐观, 所有靠它过门槛的结论要复查"
                   if med > assumed_bps else "ok — 假设覆盖实付")
        print(f"{name}: {len(filled)} fills | 实付滑点 中位 {med:+.1f}bp / "
              f"P90 {p90:+.1f}bp | 假设 {assumed_bps:.1f}bp [{verdict}]")
    if not found:
        print("尚无实盘成交记录 — 脚手架已就位, 首笔真实成交起自动积累 "
              "(outputs/live/<preset>/tca.csv)")
