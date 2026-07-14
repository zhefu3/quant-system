"""Paper-record statistics + decay state machine (frozen thresholds)."""

import numpy as np
import pandas as pd
import pytest

from qtrade.live.decay import WINDOW, classify
from qtrade.live.stats import ab_test, dd_pvalue, sharpe_ci


def _rets(mu, n=120, seed=0, sigma=0.01):
    rng = np.random.RandomState(seed)
    idx = pd.date_range("2026-01-01", periods=n, freq="D")
    return pd.Series(rng.normal(mu, sigma, n), index=idx)


def test_sharpe_ci_detects_real_edge_and_refuses_short_records():
    good = sharpe_ci(_rets(0.003))  # sharpe ~5.7: CI must exclude 0
    assert good["ci_lo"] > 0 and good["p_positive"] > 0.99
    assert sharpe_ci(_rets(0.003, n=20)) is None  # <30 marks: refuse


def test_sharpe_ci_noise_is_not_significant():
    noise = sharpe_ci(_rets(0.0, seed=3))
    assert noise["ci_lo"] < 0 < noise["ci_hi"]


def test_dd_pvalue_iid_is_unremarkable():
    res = dd_pvalue(_rets(0.0005, seed=1))
    assert 0.02 < res["p_ordering"]  # iid ordering shouldn't look extreme


def test_ab_test_detects_better_book():
    a, b = _rets(0.0, seed=5), _rets(0.002, seed=5)  # same noise, b has edge
    res = ab_test(a, b)
    assert res["p_b_better"] > 0.95
    assert res["mean_daily_diff_bps"] == pytest.approx(20, abs=3)


REF = {"ann_return": 0.14, "ann_vol": 0.12, "max_dd": 0.152}  # crypto_core ref


def test_classify_immature_below_window():
    state, _ = classify(_rets(0.001, n=WINDOW - 1).tolist(), -0.02, REF)
    assert state == "immature"


def test_classify_healthy_when_tracking_reference():
    # ref sharpe ~1.17; daily mu for ~1.5 sharpe keeps ratio above 0.5
    state, _ = classify(_rets(0.0008, n=90, seed=2).tolist(), -0.05, REF)
    assert state == "healthy"


def test_classify_decayed_on_negative_rolling_sharpe():
    state, reasons = classify(_rets(-0.002, n=90).tolist(), -0.10, REF)
    assert state == "decayed" and "sharpe" in reasons[0]


def test_classify_decayed_on_dd_breach():
    state, reasons = classify(_rets(0.001, n=90).tolist(), -0.20, REF)  # 1.32x ref dd
    assert state == "decayed" and "DD" in reasons[0]


def test_classify_warning_band():
    state, _ = classify(_rets(0.0001, n=90, seed=7).tolist(), -0.16, REF)  # dd 1.05x
    assert state == "warning"


def test_daily_returns_from_equity_csv(tmp_path):
    # coverage for the record-parsing path (a groupby-alignment bug hid here)
    f = tmp_path / "equity.csv"
    f.write_text("ts,equity\n"
                 "2026-07-01 08:05:00+00:00,10000\n"
                 "2026-07-01 20:05:00+00:00,10100\n"  # same day: keep last
                 "2026-07-02 08:05:00+00:00,10201\n"
                 "2026-07-03 08:05:00+00:00,10099.99\n")
    from qtrade.live.stats import daily_returns

    r = daily_returns(f)
    assert len(r) == 2
    assert r.iloc[0] == pytest.approx(0.01)   # 10100 -> 10201
    assert r.iloc[1] == pytest.approx(-0.0099, abs=1e-4)
