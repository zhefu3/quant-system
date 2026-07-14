"""Order-level checks + reconciliation (nautilus_trader-inspired hardening)."""

import json

import pytest

from qtrade.live.broker import MAX_ORDER_NOTIONAL, check_reconciliation, plan_orders


def _markets(syms):
    return {f"{s}:USDT": {"contractSize": 1.0} for s in syms}


def test_plan_orders_refuses_absurd_close():
    # venue reports a position >3x capital: even a "close" is corrupt data
    with pytest.raises(RuntimeError, match="3x capital"):
        plan_orders(targets={"BTC/USDT": 0.0}, closes={"BTC/USDT": 60000.0},
                    current_notional={"BTC/USDT": -10_000.0}, capital=3000.0,
                    markets=_markets(["BTC/USDT"]), eps=0.02)


def test_plan_orders_allows_oversized_flatten():
    # risk-REDUCING order above the increase cap must pass (kill-switch path)
    plans = plan_orders(targets={"ETH/USDT": 0.0}, closes={"ETH/USDT": 1750.0},
                        current_notional={"ETH/USDT": 3500.0}, capital=3000.0,
                        markets=_markets(["ETH/USDT"]), eps=0.02)
    assert any(p["contracts"] for p in plans)


def test_plan_orders_normal_rebalance_passes():
    plans = plan_orders(targets={"BTC/USDT": 0.10}, closes={"BTC/USDT": 60000.0},
                        current_notional={"BTC/USDT": 0.0}, capital=3000.0,
                        markets=_markets(["BTC/USDT"]), eps=0.02)
    assert isinstance(plans, list)
    # legit order is far below the cap
    assert all(abs(p["notional"]) <= MAX_ORDER_NOTIONAL * 3000 for p in plans)


def test_reconciliation_partial_fill_is_ok():
    expected = {"pre": {"ETH/USDT": 0.0}, "target": {"ETH/USDT": 300.0}}
    # post-only order half-filled: venue sits between pre and target
    assert check_reconciliation({"ETH/USDT": 150.0}, expected, 3000.0) == []
    # unfilled and fully filled are both fine too
    assert check_reconciliation({"ETH/USDT": 0.0}, expected, 3000.0) == []
    assert check_reconciliation({"ETH/USDT": 300.0}, expected, 3000.0) == []


def test_reconciliation_flags_manual_interference():
    expected = {"pre": {"ETH/USDT": 0.0}, "target": {"ETH/USDT": 300.0}}
    notes = check_reconciliation({"ETH/USDT": 900.0}, expected, 3000.0)
    assert len(notes) == 1 and "ETH/USDT" in notes[0]


def test_reconciliation_flags_unexpected_position():
    expected = {"pre": {}, "target": {"ETH/USDT": 300.0}}
    notes = check_reconciliation({"ETH/USDT": 300.0, "BTC/USDT": 500.0},
                                 expected, 3000.0)
    assert any("BTC/USDT" in n and "unexpected" in n for n in notes)


def test_recon_flag_blocks_sending(tmp_path, monkeypatch):
    from qtrade.live.broker import OKXExecutor
    from qtrade.presets import CRYPTO_CORE
    from tests.test_broker_flow import FakeExchange

    monkeypatch.setenv("QTRADE_OKX_ACCOUNT_UID", "424242")
    ex = OKXExecutor(CRYPTO_CORE, capital=3000.0, state_dir=tmp_path)
    ex._ex = FakeExchange()
    # last run expected a flat book; FakeExchange reports a 2-contract ETH long
    ex.expected_file.write_text(json.dumps(
        {"pre": {"ETH/USDT": 0.0}, "target": {"ETH/USDT": 0.0}}))
    ex.run(send=True, flatten=True)
    assert ex.recon_flag.exists()          # violation recorded
    assert ex._ex.orders == []             # and nothing was sent — fail closed
