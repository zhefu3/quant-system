"""The cross-source monitor must not be able to fail silently forever
(2026-07-24: the crypto leg had never completed one comparison since deploy
and said only INFO). _apply_xsource_darkness turns persistent
failure-to-compare into a WARN after XSOURCE_DARK_DAYS."""

import json

import pandas as pd
import pytest

from qtrade.live import healthcheck as hc


@pytest.fixture
def xstate(tmp_path, monkeypatch):
    p = tmp_path / "health_xsource.json"
    monkeypatch.setattr(hc, "XSOURCE_STATE", p)
    return p


def _days_ago(n: int) -> str:
    return str((pd.Timestamp.now("UTC") - pd.Timedelta(days=n)).date())


def test_fresh_success_is_quiet_and_recorded(xstate):
    out = hc._apply_xsource_darkness({"BTC/USDT": True})
    assert out == []
    state = json.loads(xstate.read_text())
    assert state["BTC/USDT"]["last_ok"] == str(pd.Timestamp.now("UTC").date())


def test_failure_below_threshold_is_quiet(xstate):
    xstate.write_text(json.dumps(
        {"BTC/USDT": {"first_try": _days_ago(10), "last_ok": _days_ago(2)}}))
    assert hc._apply_xsource_darkness({"BTC/USDT": False}) == []


def test_dark_past_threshold_warns(xstate):
    xstate.write_text(json.dumps(
        {"BTC/USDT": {"first_try": _days_ago(10), "last_ok": _days_ago(4)}}))
    out = hc._apply_xsource_darkness({"BTC/USDT": False})
    assert len(out) == 1 and "dark" in out[0] and "4d" in out[0]


def test_never_succeeded_counts_from_first_try(xstate):
    # the exact 07-24 failure mode: born dark, no last_ok ever
    xstate.write_text(json.dumps(
        {"ETH/USDT": {"first_try": _days_ago(5), "last_ok": None}}))
    out = hc._apply_xsource_darkness({"ETH/USDT": False})
    assert len(out) == 1 and "5d" in out[0]


def test_recovery_clears_the_warn(xstate):
    xstate.write_text(json.dumps(
        {"BTC/USDT": {"first_try": _days_ago(10), "last_ok": _days_ago(7)}}))
    assert hc._apply_xsource_darkness({"BTC/USDT": True}) == []
    state = json.loads(xstate.read_text())
    assert state["BTC/USDT"]["last_ok"] == str(pd.Timestamp.now("UTC").date())


def test_corrupt_state_file_never_crashes_health(xstate):
    xstate.write_text("{not json")
    assert hc._apply_xsource_darkness({"BTC/USDT": True}) == []
    assert json.loads(xstate.read_text())["BTC/USDT"]["last_ok"] is not None
