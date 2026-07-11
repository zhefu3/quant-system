"""Tests for the benchmark-relative backtester (PIT membership + costs)."""

import numpy as np
import pandas as pd

from qtrade.backtest.relative import backtest_topk


def make_panel():
    idx = pd.date_range("2020-01-01", periods=400, freq="D", tz="UTC")
    rng = np.random.RandomState(0)
    # A grinds up, B grinds down, C flat noise, D only later a member
    closes = pd.DataFrame({
        "600001.SH": 100 * np.exp(np.cumsum(rng.normal(0.001, 0.01, 400))),
        "600002.SH": 100 * np.exp(np.cumsum(rng.normal(-0.001, 0.01, 400))),
        "600003.SH": 100 * np.exp(np.cumsum(rng.normal(0.0, 0.01, 400))),
        "600004.SH": 100 * np.exp(np.cumsum(rng.normal(0.002, 0.01, 400))),
    }, index=idx)
    bench = closes.mean(axis=1)
    return closes, bench


def membership(rows):
    return pd.DataFrame(rows, columns=["snap", "code"])


def test_pit_membership_gates_selection():
    closes, bench = make_panel()
    # 600004 joins the index only from 2020-10; before that it may NOT be picked
    rows = []
    for snap in pd.date_range("2020-01-31", "2021-02-28", freq="ME"):
        members = ["sh.600001", "sh.600002", "sh.600003"]
        if snap >= pd.Timestamp("2020-10-31"):
            members.append("sh.600004")
        rows += [{"snap": str(snap.date()), "code": c} for c in members]

    picked_early = []

    def score(hist):
        s = hist.pct_change().tail(21).mean()
        picked_early.append(set(hist.columns))
        return s

    # k=1 so the 3-member early universe clears the k*2 minimum and gets scored
    backtest_topk(closes, membership(rows), score, bench, k=1, min_history=60)
    assert len(picked_early) >= 6
    # every scoring universe before October must exclude 600004
    for cols in picked_early[:3]:
        assert "600004.SH" not in cols
    # and it must appear once membership includes it
    assert any("600004.SH" in cols for cols in picked_early)


def test_costs_reduce_excess():
    closes, bench = make_panel()
    rows = [{"snap": str(s.date()), "code": c}
            for s in pd.date_range("2020-01-31", "2021-02-28", freq="ME")
            for c in ["sh.600001", "sh.600002", "sh.600003", "sh.600004"]]

    def score(hist):
        return hist.pct_change().tail(21).mean()

    r = backtest_topk(closes, membership(rows), score, bench, k=2, min_history=60)
    assert r["n_rebalances"] > 5
    assert 0 < r["avg_monthly_turnover"] <= 2.0
    assert r["tracking_error_pct"] > 0
