"""QMT executor prebuild: every decision path must be provable on macOS
before the Windows VPS exists — activation day should only have to trust
the thin XtBroker translation, never the logic."""

import json

import pandas as pd
import pytest

from qtrade.live import qmt_exec as qx


def _mk_targets(tmp_path, positions=None, equity=10_000.0, **over):
    # 120 paper shares @ 10k basis -> 12% of managed after 10x scaling: inside
    # every cap, so executor tests exercise the loop, not the refusal rails
    state = {"cash": 100.0, "positions": positions or {"600000.SH": 120.0}}
    p = qx.write_targets("ashare_ml", state, tmp_path / "targets.json",
                         equity=equity, as_of="2026-07-24")
    if over:
        payload = json.loads(p.read_text())
        payload.update(over)
        p.write_text(json.dumps(payload))
    return p


# --- message integrity -------------------------------------------------------

def test_roundtrip(tmp_path):
    t = qx.load_targets(_mk_targets(tmp_path))
    assert t["book"] == "ashare_ml" and t["positions"] == {"600000.SH": 120.0}


def test_tampered_checksum_refused(tmp_path):
    p = _mk_targets(tmp_path)
    payload = json.loads(p.read_text())
    payload["positions"]["600000.SH"] = 99999.0  # a truncated/corrupt pull
    p.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="checksum"):
        qx.load_targets(p)


def test_stale_targets_refused(tmp_path):
    p = _mk_targets(tmp_path)
    late = pd.Timestamp.now("UTC") + pd.Timedelta(days=qx.TARGETS_MAX_AGE_DAYS + 1)
    with pytest.raises(ValueError, match="stale|old"):
        qx.load_targets(p, now=late)


def test_short_position_refused(tmp_path):
    state = {"cash": 0.0, "positions": {"600000.SH": -100.0}}
    p = qx.write_targets("x", state, tmp_path / "t.json", 10_000.0, "2026-07-24")
    with pytest.raises(ValueError, match="long-only"):
        qx.load_targets(p)


def test_wrong_schema_refused(tmp_path):
    p = _mk_targets(tmp_path)
    payload = json.loads(p.read_text())
    payload["schema"] = 99
    p.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="schema"):
        qx.load_targets(p)


# --- pure order planning -----------------------------------------------------

def _plan(targets_pos, managed, positions=None, bought=None, prices=None,
          equity_basis=10_000.0, untradable=None):
    targets = {"equity_basis": equity_basis, "positions": targets_pos}
    return qx.plan_cn_orders(targets, managed, positions or {}, bought or {},
                             prices or {}, untradable)


def test_scaling_and_lot_rounding():
    # paper 55 sh at 10k basis, managed 100k -> want 550 -> floor to 500
    plans = _plan({"600000.SH": 55.0}, 100_000.0, prices={"600000.SH": 10.0})
    (p,) = [p for p in plans if p["shares"]]
    assert p["shares"] == 500 and p["side"] == "buy"


def test_cb_lot_is_ten():
    # want 55 units: stock lots (100) would floor to 0; CB lots (10) keep 50
    plans = _plan({"110073": 5.5}, 100_000.0, prices={"110073": 100.0})
    (p,) = [p for p in plans if p["shares"]]
    assert p["shares"] == 50


def test_t_plus_one_blocks_todays_buys():
    plans = _plan({}, 10_000.0, positions={"600000.SH": 500.0},
                  bought={"600000.SH": 500.0}, prices={"600000.SH": 10.0})
    (p,) = plans
    assert p["shares"] == 0 and "T+1" in p["note"]


def test_full_clear_may_sell_odd_lots():
    plans = _plan({}, 10_000.0, positions={"600000.SH": 550.0},
                  prices={"600000.SH": 10.0})
    (p,) = plans
    assert p["side"] == "sell" and p["shares"] == 550


def test_partial_trim_stays_on_lots():
    # want 300, cur 550 -> delta -250 -> lot-rounded sell 200
    plans = _plan({"600000.SH": 30.0}, 100_000.0,
                  positions={"600000.SH": 550.0}, prices={"600000.SH": 10.0})
    (p,) = [p for p in plans if p["shares"]]
    assert p["side"] == "sell" and p["shares"] == 200


def test_untradable_is_skipped_with_note():
    plans = _plan({"600000.SH": 55.0}, 100_000.0, prices={"600000.SH": 10.0},
                  untradable={"600000.SH": "limit_locked"})
    (p,) = plans
    assert p["shares"] == 0 and "limit_locked" in p["note"]


def test_single_name_cap_refuses_run():
    with pytest.raises(RuntimeError, match="exceeds"):
        _plan({"600000.SH": 200.0}, 10_000.0, prices={"600000.SH": 100.0})


def test_gross_cap_refuses_run():
    # 8 names x 14% each = 112% gross — every single name inside its own cap,
    # only the book-level clamp can catch it
    pos = {f"60000{i}.SH": 280.0 for i in range(8)}
    with pytest.raises(RuntimeError, match="gross"):
        _plan(pos, 10_000.0, prices={c: 7.0 for c in pos})


def test_non_finite_price_refuses_run():
    with pytest.raises(RuntimeError, match="finite"):
        _plan({"600000.SH": 55.0}, 10_000.0, prices={"600000.SH": float("nan")})


# --- executor loop with a fake broker ---------------------------------------

class FakeBroker:
    def __init__(self, cash=100_000.0, positions=None, account="SIM123"):
        self._cash, self._pos, self._acct = cash, dict(positions or {}), account
        self.placed = []

    def account_id(self):
        return self._acct

    def cash(self):
        return self._cash

    def positions(self):
        return dict(self._pos)

    def bought_today(self):
        return {}

    def last_prices(self, codes):
        return {c: 10.0 for c in codes}

    def place(self, code, side, shares):
        self.placed.append((code, side, shares))
        return {"order_id": len(self.placed)}


def _executor(tmp_path, broker, pinned="SIM123", capital=100_000.0):
    return qx.QmtExecutor(broker, tmp_path / "state", capital, pinned)


def test_dry_run_plans_but_never_places(tmp_path):
    b = FakeBroker()
    plans = _executor(tmp_path, b).run(_mk_targets(tmp_path), send=False)
    assert any(p["shares"] for p in plans) and b.placed == []


def test_send_places_and_snapshots_expected(tmp_path):
    b = FakeBroker()
    ex = _executor(tmp_path, b)
    ex.run(_mk_targets(tmp_path), send=True)
    assert b.placed and ex.expected_file.exists()
    exp = json.loads(ex.expected_file.read_text())
    assert "pre" in exp and "target" in exp


def test_account_pin_mismatch_refuses_send(tmp_path):
    ex = _executor(tmp_path, FakeBroker(account="OTHER"), pinned="SIM123")
    with pytest.raises(RuntimeError, match="pin"):
        ex.run(_mk_targets(tmp_path), send=True)


def test_pin_not_checked_on_dry_run(tmp_path):
    ex = _executor(tmp_path, FakeBroker(account="OTHER"), pinned="SIM123")
    assert ex.run(_mk_targets(tmp_path), send=False)  # inspection never blocked


def test_kill_switch_halts_without_flattening(tmp_path):
    b = FakeBroker(cash=100_000.0)
    ex = _executor(tmp_path, b)
    ex.hwm_file.parent.mkdir(parents=True, exist_ok=True)
    ex.hwm_file.write_text(json.dumps({"hwm": 200_000.0}))  # -50% from HWM
    plans = ex.run(_mk_targets(tmp_path), send=True)
    assert plans == [] and ex.halt_flag.exists() and b.placed == []


def test_halted_flag_refuses_everything(tmp_path):
    ex = _executor(tmp_path, FakeBroker())
    ex.halt_flag.parent.mkdir(parents=True, exist_ok=True)
    ex.halt_flag.write_text("x")
    assert ex.run(_mk_targets(tmp_path), send=True) == []


def test_reconcile_violation_blocks_send(tmp_path):
    b = FakeBroker(positions={"999999.SZ": 5_000.0})  # position we never traded
    ex = _executor(tmp_path, b)
    ex.expected_file.parent.mkdir(parents=True, exist_ok=True)
    ex.expected_file.write_text(json.dumps(
        {"pre": {"600000.SH": 0.0}, "target": {"600000.SH": 5000.0}}))
    ex.run(_mk_targets(tmp_path), send=True)
    assert ex.recon_flag.exists() and b.placed == []
