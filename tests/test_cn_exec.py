"""Execution-realism sentinels (2026-07-21 upgrade, pre-first-rebalance):

1. a suspended holding must NOT be phantom-sold at its stale price
2. a one-way limit board must refuse the adverse side and allow the other
3. the gross shadow still trades frozen names (frictionless twin unchanged)
4. patch-mode retries touch only the retried code
"""

import pandas as pd
import pytest

from qtrade.live import cn_exec
from qtrade.live.ashare_ml import mark, rebalance


def _bar(o, h, l, c):
    return pd.Series({"open": o, "high": h, "low": l, "close": c})


# ---- fill_verdict ----------------------------------------------------------

def test_suspended_when_no_bar():
    assert cn_exec.fill_verdict("buy", "600000.SH", None, 10.0) == "suspended"


def test_one_way_limit_up_refuses_buy_allows_sell():
    prev = 10.0
    locked = _bar(11.0, 11.0, 11.0, 11.0)  # 一字涨停 +10%
    assert cn_exec.fill_verdict("buy", "600000.SH", locked, prev) == "limit_locked"
    assert cn_exec.fill_verdict("sell", "600000.SH", locked, prev) == "fill"


def test_one_way_limit_down_refuses_sell():
    prev = 10.0
    locked = _bar(9.0, 9.0, 9.0, 9.0)  # 一字跌停 -10%
    assert cn_exec.fill_verdict("sell", "600000.SH", locked, prev) == "limit_locked"
    assert cn_exec.fill_verdict("buy", "600000.SH", locked, prev) == "fill"


def test_traded_range_at_limit_close_still_fills():
    # touched the limit but traded through a range — not a one-way board
    bar = _bar(10.2, 11.0, 10.1, 11.0)
    assert cn_exec.fill_verdict("buy", "600000.SH", bar, 10.0) == "fill"


def test_20pct_board_detection():
    prev = 10.0
    locked = _bar(12.0, 12.0, 12.0, 12.0)  # +20% 一字
    assert cn_exec.fill_verdict("buy", "300750.SZ", locked, prev) == "limit_locked"
    # same move on a 10% board is just a (impossible) big bar, not a lock test:
    # high==low at +20% cannot happen on the main board; verdict stays locked
    # only when the pct matches the board's own limit
    assert cn_exec.limit_pct("300750.SZ") == 0.20
    assert cn_exec.limit_pct("600000.SH") == 0.10


# ---- frozen / gross-shadow semantics ----------------------------------------

def _state(cash=10_000.0):
    return {"cash": cash, "positions": {}, "gross_cash": cash,
            "gross_positions": {}, "last_mark": None}


def test_frozen_holding_not_phantom_sold():
    state = _state()
    state, _ = rebalance(state, {"A": 0.5, "B": 0.5}, {"A": 10.0, "B": 20.0})
    qty_a = state["positions"]["A"]
    # exit both, but A is suspended (frozen); only B may sell
    state2, fills = rebalance(state, {}, {"A": 10.0, "B": 20.0}, frozen={"A"})
    assert state2["positions"]["A"] == qty_a          # net leg untouched
    assert "B" not in state2["positions"]
    assert all(f["symbol"] != "A" for f in fills)
    # gross shadow DID exit A (frictionless twin measures pure signal)
    assert "A" not in state2["gross_positions"]


def test_patch_mode_touches_only_target():
    state = _state()
    state, _ = rebalance(state, {"A": 0.5, "B": 0.5}, {"A": 10.0, "B": 20.0})
    before_b = state["positions"]["B"]
    gross_b = state["gross_positions"]["B"]
    state2, fills = rebalance(state, {"A": 0.4}, {"A": 10.0, "B": 20.0},
                              only={"A"})
    assert [f["symbol"] for f in fills] == ["A"]
    assert state2["positions"]["B"] == before_b
    assert state2["gross_positions"]["B"] == gross_b  # patch mode: both legs


def test_split_executable_partitions():
    frozen, pending = cn_exec.split_executable(
        {"A": 0.5, "B": 0.5}, {"C": 3.0},
        {"A": "limit_locked", "B": "fill", "C": "suspended"})
    assert frozen == {"A", "C"}
    assert pending == {"A": 0.5, "C": 0.0}


def test_equity_conserved_under_freeze():
    """Freezing must not create or destroy money on either leg."""
    closes = {"A": 10.0, "B": 20.0}
    state = _state()
    state, _ = rebalance(state, {"A": 0.5, "B": 0.5}, closes)
    net0, gross0 = mark(state, closes)
    state2, _ = rebalance(state, {}, closes, frozen={"A"})
    net1, gross1 = mark(state2, closes)
    # the only equity change is B's exit cost: 5000 notional x (fee+slip)
    exit_cost = 5000 * (0.0008 + 0.001)
    assert net0 - net1 == pytest.approx(exit_cost, abs=1e-6)
    assert gross1 == pytest.approx(gross0, abs=1e-9)
