"""Sentinels for the CB event collector's diff logic: the dataset's value is
that every event row is a real, dated transition — no phantom events from a
baseline run, no missed announcement flips."""

import importlib.util
import sys
from pathlib import Path

import pandas as pd

spec = importlib.util.spec_from_file_location(
    "collect_cb_events",
    Path(__file__).resolve().parents[1] / "research" / "collect_cb_events.py")
M = importlib.util.module_from_spec(spec)
sys.modules.setdefault("collect_cb_events", M)
spec.loader.exec_module(M)


def _redeem(rows):
    return pd.DataFrame(rows, columns=["代码", "名称", "强赎状态"])


def _master(rows):
    return pd.DataFrame(rows, columns=["债券代码", "债券简称", "转股价"])


def test_baseline_run_yields_no_events():
    cur_r = _redeem([["113001", "A转债", "已满足强赎条件"]])
    cur_m = _master([["113001", "A转债", 10.0]])
    assert M.detect_events(None, cur_r, None, cur_m, "2026-07-21") == []


def test_redeem_status_transition_is_an_announcement_date():
    prev = _redeem([["113001", "A转债", "已满足强赎条件"]])
    cur = _redeem([["113001", "A转债", "已公告强赎"]])
    ev = M.detect_events(prev, cur, None, _master([]), "2026-07-22")
    assert len(ev) == 1
    assert ev[0]["type"] == "redeem_status"
    assert ev[0]["new"] == "已公告强赎" and ev[0]["date"] == "2026-07-22"


def test_conv_price_cut_detected_as_event():
    prev = _master([["128026", "众兴转债", 11.12]])
    cur = _master([["128026", "众兴转债", 8.75]])
    ev = M.detect_events(_redeem([]), _redeem([]), prev, cur, "2026-07-22")
    assert len(ev) == 1
    assert ev[0]["type"] == "conv_price"
    assert ev[0]["old"] == 11.12 and ev[0]["new"] == 8.75


def test_identical_snapshots_yield_nothing():
    r = _redeem([["113001", "A转债", "已公告强赎"]])
    m = _master([["113001", "A转债", 10.0]])
    assert M.detect_events(r.copy(), r.copy(), m.copy(), m.copy(), "d") == []


def test_new_bond_entering_watch_table():
    prev = _redeem([["113001", "A转债", "x"]])
    cur = _redeem([["113001", "A转债", "x"], ["123002", "B转债", "已满足强赎条件"]])
    ev = M.detect_events(prev, cur, None, _master([]), "d")
    assert [e["type"] for e in ev] == ["redeem_new"]
