"""Broker-reply interpreter (frozen 2026-07-22, BEFORE the reply arrived).

The interpretation rules for whatever numbers the broker sends back were
committed in advance — prereg discipline applied to a negotiation:

A-share ML line (archived E47, gate net>3%/yr AND IR>0.5 AND both halves+):
  ANALYTIC PRE-VERDICT — full revival is unreachable at ANY commission.
  IR>0.5 needs net >= 0.5 x TE(11.5%) = 5.75%/yr, i.e. total drag <=
  0.55%/yr; stamp duty alone (sell-side 万5 x ~12.7x annual sell volume)
  is ~0.64%/yr. The tax floor, not the commission, is binding. Fee cuts
  only polish the archived-marginal record; the real unlock stays the
  minute-data signal upgrade (E48 path, ¥2000).

CB low-premium line (E67, gate = post-2021 net>6%/Sharpe>0.8/DD<15% at
BOTH 5bp and 10bp slippage):
  BOUNDARY — passes at ANY commission (万0.3~万1) when slippage is 5bp;
  fails at any commission when 10bp. The reopen therefore hinges on the
  QMT-sim TCA measuring real slippage <=5bp, exactly as the E67 verdict
  pre-committed. Commission moves net by ~0.1pp; measured slippage moves
  the verdict.

Also check on reply: cb_double_low's live cost model assumes 万1 — if the
negotiated CB commission lands ABOVE that, the paper book's costs are
optimistic and an ops note is due; at 万0.5 they are conservative.
And 免五 is non-negotiable for the stock book: at ¥2000/order (10万 x
top-50), a ¥5 minimum is 25bp/side — worse than today's 万2.45.

Usage: python research/fee_verdicts.py --stock-fee 1.0 --cb-fee 0.5
(units: 万分之N per side; prints the committed interpretation.)
"""

from __future__ import annotations

import argparse

STAMP_BP = 2.5        # sell-side 万5 stamp duty, symmetrized per side
GROSS, TE, DRAG = 6.3, 11.5, 3.8   # archived E47 aggregates (log.md E47 判决)
FEE0_BP, SLIP0_BP = 8.0, 10.0      # E47 frozen cost model per side

# E67 post-2021 stats by total per-side cost, from the frozen weekly loop
# (research/cb_low_premium.py rerun 2026-07-22; recompute if panel extends)
E67_TABLE = {5.3: (0.103, 0.92, -0.141), 5.5: (0.102, 0.91, -0.142),
             6.0: (0.101, 0.90, -0.144), 10.3: (0.087, 0.79, -0.164),
             10.5: (0.087, 0.79, -0.165), 11.0: (0.085, 0.78, -0.168)}


def ashare_ml_readout(comm_bp: float, min5_waived: bool) -> None:
    fee_bp = comm_bp + STAMP_BP
    fee_part = DRAG * FEE0_BP / (FEE0_BP + SLIP0_BP)
    slip_part = DRAG - fee_part
    net = GROSS - slip_part - fee_part * fee_bp / FEE0_BP
    print(f"[A股ML] 佣金万{comm_bp:g}{'免五' if min5_waived else '不免五'}: "
          f"档案口径净超额 ~{net:+.2f}%/年, IR ~{net / TE:.2f}")
    if not min5_waived:
        print("        ⚠ 不免五: ¥2000 级订单最低5元=25bp/边, 劣于现价——股票线不可用")
    print("        解析预判: IR>0.5 在印花税地板下不可达 → 全面复活不判, "
          "真正解锁=分钟线信号升级")


def cb_readout(comm_bp: float) -> None:
    lo, hi = comm_bp + 5.0, comm_bp + 10.0
    def nearest(x):
        k = min(E67_TABLE, key=lambda t: abs(t - x))
        return E67_TABLE[k]
    a5, s5, d5 = nearest(lo)
    a10, s10, d10 = nearest(hi)
    ok5 = a5 > 0.06 and s5 > 0.8 and d5 > -0.15
    ok10 = a10 > 0.06 and s10 > 0.8 and d10 > -0.15
    print(f"[转债E67] 佣金万{comm_bp:g}: 滑点5bp档 {a5:+.1%}/{s5:.2f} "
          f"{'✅' if ok5 else '❌'} | 10bp档 {a10:+.1%}/{s10:.2f} {'✅' if ok10 else '❌'}")
    print("        判定权在 QMT 模拟盘 TCA: 实测滑点≤5bp 即复活, 佣金档位只挪 ~0.1pp")
    if comm_bp > 1.0:
        print("        ⚠ 高于万1: cb_double_low 纸面账成本模型偏乐观, 需补运维注记")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stock-fee", type=float, required=True, help="股票佣金, 万分之N")
    ap.add_argument("--no-min5-waiver", action="store_true", help="未争取到免五")
    ap.add_argument("--cb-fee", type=float, required=True, help="转债佣金, 万分之N")
    a = ap.parse_args()
    print("══════ 券商回复解读(规则冻结于 2026-07-22, 回复到达前) ══════")
    ashare_ml_readout(a.stock_fee, not a.no_min5_waiver)
    print()
    cb_readout(a.cb_fee)


if __name__ == "__main__":
    main()
