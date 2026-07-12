"""Stitching invariants for the E50b-frozen rules (qtrade.data.cn_futures)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from qtrade.data import cn_futures as cnf


def _contract(dates, close, hold, settle=None):
    n = len(dates)
    close = pd.Series(close, dtype=float)
    return pd.DataFrame({
        "open": close.values, "high": close.values, "low": close.values,
        "close": close.values, "volume": np.ones(n),
        "hold": pd.Series(hold, dtype=float).values,
        "settle": (pd.Series(settle, dtype=float).values if settle is not None
                   else close.values),
    }, index=pd.DatetimeIndex(dates, name="date"))


DATES = pd.bdate_range("2024-01-01", periods=8)


def test_expiry_key():
    assert cnf.expiry_key("RB2410") == 202410
    assert cnf.expiry_key("I1905") == 201905


def test_pick_main_uses_previous_day_oi():
    # near's OI drops below far's on day 3; the switch must show on day 4
    # (decisions read yesterday's OI — no look-ahead).
    near = _contract(DATES, close=[100] * 8, hold=[50, 50, 10, 10, 10, 10, 10, 10])
    far = _contract(DATES, close=[110] * 8, hold=[20, 20, 60, 60, 60, 60, 60, 60])
    sched = cnf.pick_main({"RB2405": near, "RB2410": far})["contract"]
    assert sched.iloc[2] == "RB2405"  # crossover day: yesterday still favored near
    assert sched.iloc[3] == "RB2410"


def test_pick_main_never_rolls_backward():
    far = _contract(DATES, close=[110] * 8, hold=[60] * 8)
    near = _contract(DATES, close=[100] * 8, hold=[10, 10, 10, 10, 90, 90, 90, 90])
    sched = cnf.pick_main({"RB2405": near, "RB2410": far})["contract"]
    assert (sched == "RB2410").all()  # near regaining OI must not win

def test_pick_main_forced_roll_when_main_stops_trading():
    near = _contract(DATES[:4], close=[100] * 4, hold=[50] * 4)
    far = _contract(DATES, close=[110] * 8, hold=[20] * 8)
    sched = cnf.pick_main({"RB2405": near, "RB2410": far})["contract"]
    assert sched.iloc[3] == "RB2405" and sched.iloc[4] == "RB2410"


def test_stitch_cross_roll_return_is_new_contract_return(monkeypatch, tmp_path):
    # old trades at 100 flat; new lists at 90 and moves to 93 across the roll.
    # The stitched series must show the NEW contract's return through the roll
    # and raw (unadjusted) prices on the latest segment.
    old = _contract(DATES[:5], close=[100] * 5, hold=[50, 50, 50, 5, 5])
    new = _contract(DATES, close=[90, 90, 90, 90, 91, 93, 94, 95],
                    hold=[10, 10, 10, 80, 80, 80, 80, 80])
    d = tmp_path / "cn_contracts"
    d.mkdir()
    old.reset_index().assign(date=lambda x: x["date"].astype(str)).to_parquet(d / "RB2405.parquet")
    new.reset_index().assign(date=lambda x: x["date"].astype(str)).to_parquet(d / "RB2410.parquet")
    monkeypatch.setattr(cnf, "CONTRACT_DIR", d)

    bars, stats = cnf.stitch("RB")
    assert stats["rolls"] == 1
    # roll happens on day 5 (index 4): prev-day OI first favors new on day 5
    r = bars["close"].pct_change()
    assert r.iloc[4] == pytest.approx(91 / 90 - 1)  # new contract's return, no splice jump
    assert bars["close"].iloc[-1] == pytest.approx(95.0)  # latest segment = raw
    # pre-roll history scaled by new/old on the anchor day (90/100)
    assert bars["close"].iloc[0] == pytest.approx(100 * 90 / 100)


def test_adapter_drop_in_progress_keeps_past_close_stamped_bars():
    from qtrade.data.adapters.cn_futures_ak import CnFuturesAdapter

    idx = pd.DatetimeIndex([pd.Timestamp("2026-07-09 07:00", tz="UTC"),
                            pd.Timestamp("2026-07-10 07:00", tz="UTC")])
    bars = pd.DataFrame({"close": [1.0, 2.0]}, index=idx)
    now = pd.Timestamp("2026-07-10 08:00", tz="UTC")  # 16:00 Shanghai, post-close
    kept = CnFuturesAdapter.drop_in_progress(bars, now, pd.Timedelta(days=1))
    assert len(kept) == 2  # crypto rule would wrongly drop today's completed bar
    early = pd.Timestamp("2026-07-10 03:00", tz="UTC")  # 11:00 Shanghai, mid-session
    assert len(CnFuturesAdapter.drop_in_progress(bars, early, pd.Timedelta(days=1))) == 1


def test_make_adapter_dispatch():
    from qtrade.data.adapters import make_adapter

    assert make_adapter("cnfutures").market == "cnfutures"


def test_last_session_date_cutoff():
    pre = pd.Timestamp("2026-07-10 06:00", tz="UTC")   # 14:00 SH, pre-close
    post = pd.Timestamp("2026-07-10 07:30", tz="UTC")  # 15:30 SH, post-close
    assert cnf._last_session_date(pre) == pd.Timestamp("2026-07-09")
    assert cnf._last_session_date(post) == pd.Timestamp("2026-07-10")
