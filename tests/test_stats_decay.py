"""Paper-record statistics + decay state machine (frozen thresholds)."""

import numpy as np
import pandas as pd
import pytest

from qtrade.live.decay import WINDOW, classify
from qtrade.live.stats import (ab_test, dd_pvalue, exposure_series,
                               sharpe_ci)


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


def test_ab_test_flags_a_variant_that_never_diverged():
    """A challenger whose distinguishing condition never fires is untested.

    Its returns match the champion on all but a couple of days, so the paired
    test sees no effect — that must read as INCONCLUSIVE_NO_EXPOSURE, never as
    equivalence (2026-07-27 amendment; the live core-vs-v2 pair sat here for
    its first 18 days).
    """
    a = _rets(0.001, seed=7)
    b = a.copy()
    b.iloc[5] += 0.0004  # the regime filter fired exactly twice
    b.iloc[40] -= 0.0003
    res = ab_test(a, b)
    assert res["n_diff_days"] == 2
    assert res["exposure"] == "INCONCLUSIVE_NO_EXPOSURE"


def test_ab_test_reports_power_and_grades_a_well_exposed_pair():
    a, b = _rets(0.001, seed=11), _rets(0.001, seed=12)  # genuinely different paths
    res = ab_test(a, b)
    assert res["exposure"] in ("DECIDABLE", "INCONCLUSIVE_LOW_PRECISION")
    assert res["trigger_frequency"] == 1.0  # continuous divergence, not rare doses
    # a noisy pair resolves only large effects; power must rise with effect size
    assert res["mde_sharpe_80"] > 0.1
    assert res["power"]["dSR=0.5"] >= res["power"]["dSR=0.1"]


def test_ten_days_inside_one_regime_are_one_episode_not_ten():
    """The exposure amendment's whole point: consecutive days are one dose.

    A challenger that diverged for ten straight days saw a single regime, and
    must not clear an exposure floor meant to require repeated independent
    triggers (2026-07-27 amendment).
    """
    a = _rets(0.001, seed=21)
    b = a.copy()
    b.iloc[30:40] += 0.0005  # one uninterrupted bear-filter episode
    res = ab_test(a, b)
    assert res["n_diff_days"] == 10  # would have passed the old day-count floor
    assert res["n_diff_episodes"] == 1
    assert res["exposure"] == "INCONCLUSIVE_NO_EXPOSURE"


def test_exposure_series_sees_position_divergence_that_returns_hide():
    """Offsetting weight changes can net to zero P&L; positions still moved."""
    import pandas as pd

    def _write(path, weights):
        rows = [{"ts": f"2026-0{1 + d // 28}-{1 + d % 28:02d} 20:00:00+00:00",
                 "symbol": sym, "target_w": w, "held_w": w}
                for d, day in enumerate(weights) for sym, w in day.items()]
        pd.DataFrame(rows).to_csv(path, index=False)

    import tempfile
    with tempfile.TemporaryDirectory() as td:
        fa, fb = f"{td}/a.csv", f"{td}/b.csv"
        _write(fa, [{"BTC/USDT": 0.2, "ETH/USDT": 0.2}] * 4)
        _write(fb, [{"BTC/USDT": 0.4, "ETH/USDT": 0.0}] * 4)  # same gross, moved
        ex = exposure_series(fa, fb)
        assert len(ex) == 4
        assert ex.iloc[0] == pytest.approx(0.4)  # |0.2| + |0.2| of L1 distance


def test_ab_test_identical_books_have_no_resolving_power():
    a = _rets(0.001, seed=9)
    res = ab_test(a, a.copy())
    assert res["mde_sharpe_80"] is None  # cannot detect anything, at any size
    assert res["exposure"] == "INCONCLUSIVE_NO_EXPOSURE"


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
