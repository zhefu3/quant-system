"""explain: the full decision chain behind every position, right now.

For each symbol: what the trend leg's horizons vote, where the meanrev
z-score and regime stand, how volatility scaling shrank the position, and
what the throttle will do — so "why are we long ETH" always has an answer.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..presets import PRESETS
from .signals import compute_targets, fetch_live_bars


def _leg_line(leg: dict) -> str:
    base = leg.get("base", leg)
    name = base.get("name", "?")
    mix = leg.get("mix", 1.0)
    vol = leg.get("realized_vol")
    if name == "cta_trend":
        votes = base["votes"]
        arrows = " ".join(f"{h}{'↑' if v > 0 else ('↓' if v < 0 else '→')}"
                          for h, v in votes.items())
        detail = f"趋势腿: {arrows} → 净票 {base['target']:+.2f}"
    elif name == "boll_revert":
        z, ez = base["z"], base["entry_z"]
        st = base["target"]
        pos_txt = "多" if st > 0 else ("空" if st < 0 else "无仓")
        regime = base.get("regime")
        regime_txt = {"long_ok": "MA上方(只准做多)", "short_ok": "MA下方(只准做空)"}.get(regime, "")
        detail = f"回归腿: z={z:+.2f}(阈±{ez}) {pos_txt} {regime_txt}"
    else:
        detail = f"{name}: target {base.get('target')}"
    if vol is not None:
        detail += f" | 波动率 {vol:.0%}→缩放 {leg['scale']:.2f}"
    return f"    {detail} | 贡献 {mix * leg['target']:+.4f}"


def render_symbol(sym: str, info: dict, alloc_n: int, price: float,
                  held_w: float, eps: float) -> str:
    tgt = info["target"] / alloc_n
    delta = tgt - held_w
    if abs(delta) < eps and not (tgt == 0.0 and held_w != 0.0):
        action = f"节流({eps:.0%})拦截 → 维持 {held_w:+.1%}"
    elif abs(delta) < 1e-9:
        action = "已在目标位"
    else:
        action = f"调仓 {held_w:+.1%} → {tgt:+.1%}"
    lines = [f"  {sym:11s} 目标 {tgt:+7.2%}  现价 {price:,.4g}  【{action}】"]
    for leg in info.get("legs", []):
        lines.append(_leg_line(leg))
    return "\n".join(lines)


def run_explain(preset_name: str, state_dir: str | None = None) -> None:
    from .paper import DEFAULT_ROOT

    p = PRESETS[preset_name]
    bars = fetch_live_bars(p)
    targets, closes = compute_targets(p, bars_by_symbol=bars)

    held = {}
    d = Path(state_dir) if state_dir else DEFAULT_ROOT / preset_name
    state_file = d / "state.json"
    if state_file.exists():
        state = json.loads(state_file.read_text())
        equity = state["cash"] + sum(q * closes.get(s, 0.0)
                                     for s, q in state["positions"].items())
        held = {s: q * closes[s] / equity for s, q in state["positions"].items()}

    strategy = p.strategy()
    n = len(p.symbols)
    print(f"=== {p.name} 决策解释 · {len(bars[next(iter(bars))])} bars 上下文 ===")
    active = flat = 0
    for sym in p.symbols:
        info = strategy.explain(bars[sym])
        if abs(targets[sym]) > 1e-9 or abs(held.get(sym, 0.0)) > 1e-9:
            active += 1
            print(render_symbol(sym, info, n, closes[sym],
                                held.get(sym, 0.0), p.rebalance_eps))
        else:
            flat += 1
    print(f"\n  空仓品种 {flat} 个（信号未触发或多空票互相抵消）")
    gross = sum(abs(v) for v in targets.values())
    print(f"  目标总敞口 {gross:.1%} · 记住: 空仓也是决策——"
          f"没有优势时不持仓就是这个系统赚钱的方式之一")
