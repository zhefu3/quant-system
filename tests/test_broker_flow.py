"""Executor flow tests against a fake exchange — the paths that touch money.

plan_orders math is covered in test_broker; these cover account_state
parsing, the dry-run/send gate, kill-switch flattening, and HALTED locking.
"""

import json

import pytest

from qtrade.live.broker import OKXExecutor
from qtrade.presets import CRYPTO_CORE


class FakeExchange:
    def __init__(self):
        self.orders = []
        self._markets = {f"{s}:USDT": {"contractSize": 1.0} for s in CRYPTO_CORE.symbols}

    def market(self, sym):
        return self._markets[sym]

    def fetch_balance(self):
        return {"USDT": {"total": 5000.0}}

    def fetch_positions(self, symbols):
        return [{"symbol": "ETH/USDT:USDT", "contracts": 2.0, "side": "long",
                 "markPrice": 1750.0, "entryPrice": 1700.0}]

    def fetch_ticker(self, sym):
        return {"last": 100.0}

    def create_order(self, symbol, typ, side, amount, params=None):
        self.orders.append({"symbol": symbol, "side": side, "amount": amount})
        return {"id": f"fake-{len(self.orders)}", "status": "closed"}


@pytest.fixture
def executor(tmp_path):
    ex = OKXExecutor(CRYPTO_CORE, capital=3000.0, state_dir=tmp_path)
    ex._ex = FakeExchange()
    return ex


def test_account_state_parses_positions(executor):
    usdt, notionals = executor.account_state()
    assert usdt == 5000.0
    assert notionals == {"ETH/USDT": pytest.approx(2.0 * 1750.0)}


def test_flatten_dry_run_sends_nothing(executor):
    executor.run(send=False, flatten=True)
    assert executor._ex.orders == []


def test_flatten_send_closes_the_long(executor):
    executor.run(send=True, flatten=True)
    assert len(executor._ex.orders) == 1
    o = executor._ex.orders[0]
    assert o["symbol"] == "ETH/USDT:USDT" and o["side"] == "sell"


def test_halted_flag_blocks_run(executor, capsys):
    executor.halt_flag.write_text("test halt\n")
    executor.run(send=True)
    assert "HALTED" in capsys.readouterr().out
    assert executor._ex.orders == []


def test_kill_switch_flattens_and_halts(executor):
    # High-water mark far above current managed equity -> breach on next run
    executor.hwm_file.write_text(json.dumps({"hwm": 50_000.0, "ts": "x"}))
    executor.run(send=True)  # managed = min(3000, 5000) < 80% of hwm
    assert executor.halt_flag.exists()
    assert any(o["side"] == "sell" for o in executor._ex.orders)  # flattened
