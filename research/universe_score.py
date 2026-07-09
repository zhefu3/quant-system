"""Universe selection, as a rule instead of a hunch.

Scores every symbol in the store on four dimensions that matter TO THIS
STRATEGY FAMILY (trend + regime meanrev, vol-targeted, retail costs):

  liquidity   median daily dollar volume (slippage reality)  — higher better
  vol_fit     annualized vol vs the 40% vol target           — 30-100% ideal
  fitness     per-symbol book Sharpe, split-half consistency  — both halves >0
  granularity min contract notional vs weight size @3k USDT   — tradeable?

⚠️ fitness is in-sample by construction (mild selection bias) — mitigated by
requiring BOTH halves positive, and by re-scoring quarterly rather than
chasing ranks. E28 taught us more symbols ≠ better; this tool exists to
answer "why these ten" with numbers, and to flag rotation candidates.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qtrade.backtest.portfolio import run_portfolio  # noqa: E402
from qtrade.data.store import BarStore  # noqa: E402
from qtrade.markets.rules import CRYPTO_PERP  # noqa: E402
from qtrade.presets import CRYPTO_CORE  # noqa: E402

CAPITAL = 3000.0
# OKX contract sizes (from public specs; granularity check only)
CONTRACT_SIZE = {"BTC/USDT": 0.01, "ETH/USDT": 0.1, "SOL/USDT": 1, "XRP/USDT": 100,
                 "DOGE/USDT": 1000, "ADA/USDT": 100, "LTC/USDT": 1, "LINK/USDT": 1,
                 "AVAX/USDT": 1, "DOT/USDT": 1, "UNI/USDT": 1, "ATOM/USDT": 1,
                 "FIL/USDT": 0.1, "NEAR/USDT": 10, "TRX/USDT": 1000, "BCH/USDT": 0.1}


def main():
    store = BarStore()
    cov = store.coverage()
    syms = sorted(cov[(cov["market"] == "crypto") & (cov["timeframe"] == "1h")]["symbol"])
    p = CRYPTO_CORE

    bars_all = {s: store.load("crypto", s, "1h") for s in syms}
    start = max(b.index[0] for b in bars_all.values())
    bars_all = {s: b[b.index >= start] for s, b in bars_all.items()}

    def book_sharpe(members: list[str]) -> float:
        r = run_portfolio(p.strategy(), {s: bars_all[s] for s in members},
                          CRYPTO_PERP, "1h", allocation="equal",
                          rebalance_eps=p.rebalance_eps, oos_fraction=0.0001).loc["full"]
        return float(r["sharpe"]) if pd.notna(r["sharpe"]) else 0.0

    base_sharpe = book_sharpe(p.symbols)
    print(f"panel from {start.date()} | 10-symbol book Sharpe {base_sharpe:.2f}\n")

    rows = []
    for sym in syms:
        b = bars_all[sym]
        recent = b[b.index >= b.index[-1] - pd.Timedelta(days=365)]
        dollar_vol = float((recent["volume"] * recent["close"]).resample("1D").sum().median())
        ann_vol = float(recent["close"].pct_change().std() * np.sqrt(8760))

        in_book = sym in p.symbols
        if in_book:
            # marginal contribution: how much does the BOOK lose without it?
            marginal = base_sharpe - book_sharpe([s for s in p.symbols if s != sym])
        else:
            # rotation candidate: does adding it help the book?
            marginal = book_sharpe(p.symbols + [sym]) - base_sharpe

        csize = CONTRACT_SIZE.get(sym)
        min_notional = csize * float(b["close"].iloc[-1]) if csize else np.nan

        why = []
        if dollar_vol < 5e6:  # OKX-only volume; our size is tiny
            why.append("流动性偏薄")
        if not (0.30 <= ann_vol <= 1.20):
            why.append(f"波动率{ann_vol:.0%}超适配区")
        if min_notional > 0.04 * CAPITAL:
            why.append(f"3k资金粒度差({min_notional:.0f}U/张)")
        if in_book:
            verdict = "保留" if marginal > -0.05 else "换出候选"
            if marginal <= -0.05:
                why.append(f"边际贡献 {marginal:+.2f}(拖累组合)")
        else:
            verdict = "换入候选" if (marginal > +0.05 and not why) else "不引入"
        rows.append({
            "symbol": sym, "in_book": "✓" if in_book else "",
            "med_$vol_M": round(dollar_vol / 1e6, 1), "ann_vol": f"{ann_vol:.0%}",
            "marginal_sharpe": round(marginal, 3),
            "min_contract_$": round(min_notional, 0) if csize else None,
            "verdict": verdict, "why": "; ".join(why) or "-",
        })
    df = pd.DataFrame(rows).sort_values("marginal_sharpe", ascending=False)
    print(df.to_string(index=False))
    print("\n规则: 池内品种边际贡献 < -0.05 → 换出候选; 池外 > +0.05 且无短板 → 换入候选。"
          "\n任何换池动作必须再过预注册双面板门槛(见 log 2026-07-10), 每季度重评, 不追排名。")


if __name__ == "__main__":
    main()
