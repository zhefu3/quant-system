"""Tests for the live executor's pure sizing logic — the code that stands
between a signal and real money."""

import pytest

from qtrade.live.broker import MAX_SINGLE_W, plan_orders

MARKETS = {
    "BTC/USDT:USDT": {"contractSize": 0.01},
    "DOGE/USDT:USDT": {"contractSize": 1000},
}
CLOSES = {"BTC/USDT": 60_000.0, "DOGE/USDT": 0.08}


def test_contract_granularity_and_sides():
    targets = {"BTC/USDT": 0.10, "DOGE/USDT": -0.10}
    plans = plan_orders(targets, CLOSES, {}, capital=10_000.0,
                        markets=MARKETS, eps=0.02)
    by_sym = {p["symbol"]: p for p in plans}
    # BTC: 1000 USDT target / 600 USDT per contract -> 1 contract long
    assert by_sym["BTC/USDT"]["contracts"] == 1
    assert by_sym["BTC/USDT"]["side"] == "buy"
    # DOGE: 1000 USDT / 80 USDT per contract -> 12 contracts short
    assert by_sym["DOGE/USDT"]["contracts"] == 12
    assert by_sym["DOGE/USDT"]["side"] == "sell"


def test_sub_contract_delta_is_skipped_not_traded():
    targets = {"BTC/USDT": 0.05}  # 50 USDT on 1000 capital < 600/contract
    plans = plan_orders(targets, CLOSES, {}, capital=1_000.0,
                        markets=MARKETS, eps=0.02)
    assert plans[0]["contracts"] == 0
    assert "skip" in plans[0]["note"]


def test_single_weight_hard_cap():
    targets = {"BTC/USDT": 0.60}  # strategy asks 60%, rail caps at 15%
    plans = plan_orders(targets, CLOSES, {}, capital=100_000.0,
                        markets=MARKETS, eps=0.02)
    assert abs(plans[0]["notional"]) <= MAX_SINGLE_W * 100_000.0 + 1e-6


def test_small_rebalance_suppressed_but_full_exit_allowed():
    current = {"BTC/USDT": 1_000.0}
    # tiny drift: suppressed
    assert plan_orders({"BTC/USDT": 0.101}, CLOSES, current, 10_000.0,
                       MARKETS, eps=0.05) == []
    # full exit: always allowed through the eps filter
    plans = plan_orders({"BTC/USDT": 0.0}, CLOSES, current, 10_000.0,
                        MARKETS, eps=0.05)
    assert plans and plans[0]["side"] == "sell"


def test_insane_order_flow_refused():
    markets = {"BTC/USDT:USDT": {"contractSize": 0.01}}
    with pytest.raises(RuntimeError, match="sanity"):
        # current position wildly off from target -> order gross > 1.5x capital
        plan_orders({"BTC/USDT": 0.10}, CLOSES, {"BTC/USDT": -200_000.0},
                    capital=10_000.0, markets=markets, eps=0.02)
