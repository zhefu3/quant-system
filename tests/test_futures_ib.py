"""Pure-logic tests for the IBKR futures adapter (no gateway needed)."""

import pandas as pd

from qtrade.data.adapters import make_adapter
from qtrade.data.adapters.futures_ib import EXCHANGE, IbkrFuturesAdapter
from qtrade.presets import PRESETS


def _bars(dates):
    idx = pd.DatetimeIndex(pd.to_datetime(dates)).tz_localize("America/New_York")
    return pd.DataFrame(
        {"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 0.0},
        index=idx,
    ).rename_axis("ts")


def test_dispatch_and_universe_covered():
    adapter = make_adapter("futures_ibkr")
    assert isinstance(adapter, IbkrFuturesAdapter)
    assert set(PRESETS["futures_ibkr"].symbols) <= set(EXCHANGE)


def test_drop_in_progress_waits_for_session_close():
    # bars stamped 00:00 ET; trade date D is complete at 17:00 ET on D
    bars = _bars(["2026-07-09", "2026-07-10"])
    tf = pd.Timedelta("1d")
    during = pd.Timestamp("2026-07-10 15:00", tz="America/New_York")
    after = pd.Timestamp("2026-07-10 17:00", tz="America/New_York")

    kept = IbkrFuturesAdapter.drop_in_progress(bars, during.tz_convert("UTC"), tf)
    assert list(kept.index.date) == [pd.Timestamp("2026-07-09").date()]

    kept = IbkrFuturesAdapter.drop_in_progress(bars, after.tz_convert("UTC"), tf)
    assert len(kept) == 2


def test_observation_book_not_in_allocation_sleeves():
    # E40b verdict: futures stays researched-not-deployed; the paper book is
    # observation-only and must never flow into the portfolio layer.
    from qtrade.live.allocate import SLEEVES

    assert "futures_ibkr" not in SLEEVES
