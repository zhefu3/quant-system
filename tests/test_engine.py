"""Tests for the engine's honesty guarantees: no lookahead, no free lunch."""

import numpy as np
import pandas as pd
import pytest

from qtrade.backtest.engine import Engine
from qtrade.data.schema import normalize_ohlcv
from qtrade.data.store import BarStore
from qtrade.markets.rules import CRYPTO, MarketRules
from qtrade.strategies.base import Strategy


def make_bars(closes):
    idx = pd.date_range("2024-01-01", periods=len(closes), freq="1h", tz="UTC")
    c = pd.Series(closes, index=idx, dtype="float64")
    return pd.DataFrame(
        {"open": c, "high": c * 1.001, "low": c * 0.999, "close": c, "volume": 1.0}
    ).rename_axis("ts")


class OracleStrategy(Strategy):
    """A cheating strategy: goes long exactly on the bar BEFORE a known jump."""

    name = "oracle"

    def __init__(self, long_at: int):
        self.long_at = long_at

    def target_position(self, bars):
        pos = pd.Series(0.0, index=bars.index)
        pos.iloc[self.long_at] = 1.0
        return pos


def test_zero_cost_is_rejected():
    with pytest.raises(ValueError, match="free-lunch"):
        MarketRules(market="x", fee_rate=0.0, slippage=0.0)


def test_no_lookahead_next_bar_execution():
    # Price doubles between bar 10 and bar 11, then stays flat.
    closes = [100.0] * 11 + [200.0] * 20
    bars = make_bars(closes)

    # Oracle "decides" at bar 10 (right before the jump). With honest next-bar
    # execution it fills at bar 11's close = 200 and exits at 12 — the doubling
    # must NOT appear in its P&L.
    result = Engine(CRYPTO).run(OracleStrategy(long_at=10), bars, oos_fraction=0.3)
    full = result.to_frame().loc["full"]
    assert full["total_return_pct"] < 1.0, "oracle captured the jump: lookahead bug!"


def test_costs_reduce_returns():
    closes = list(np.linspace(100, 200, 50))  # steady uptrend

    class AlwaysLong(Strategy):
        name = "always_long"

        def target_position(self, bars):
            return pd.Series(1.0, index=bars.index)

    cheap = MarketRules(market="cheap", fee_rate=1e-6, slippage=1e-6)
    dear = MarketRules(market="dear", fee_rate=0.01, slippage=0.01)
    bars = make_bars(closes)
    r_cheap = Engine(cheap).run(AlwaysLong(), bars).to_frame().loc["full", "total_return_pct"]
    r_dear = Engine(dear).run(AlwaysLong(), bars).to_frame().loc["full", "total_return_pct"]
    assert r_dear < r_cheap


class AlwaysShort(Strategy):
    name = "always_short"

    def target_position(self, bars):
        return pd.Series(-1.0, index=bars.index)


def test_short_rejected_when_market_disallows():
    bars = make_bars(list(np.linspace(100, 50, 30)))
    with pytest.raises(ValueError, match="disallows shorting"):
        Engine(CRYPTO).run(AlwaysShort(), bars)


def test_short_profits_in_downtrend():
    from qtrade.markets.rules import CRYPTO_PERP

    bars = make_bars(list(np.linspace(100, 50, 60)))  # steady 50% decline
    r = Engine(CRYPTO_PERP).run(AlwaysShort(), bars).to_frame().loc["full"]
    assert r["total_return_pct"] > 20, "short in a halving market should profit"
    assert r["benchmark_return_pct"] < -40


def test_t_plus_one_blocks_same_day_exit():
    from qtrade.backtest.engine import _enforce_t_plus_one

    # Two trading days x 4 intraday bars (Shanghai time).
    idx = pd.DatetimeIndex(
        [f"2026-07-0{d} {h}:00" for d in (6, 7) for h in ("09", "10", "13", "14")],
        tz="Asia/Shanghai",
    ).tz_convert("UTC")
    # Execution-terms position: buy fills 09:00 day1, strategy wants out 13:00 day1.
    pos = pd.Series([1, 1, 0, 0, 0, 0, 0, 0], index=idx, dtype=float)
    fixed = _enforce_t_plus_one(pos, "Asia/Shanghai")
    # Same-day exit is pushed to day 2's first bar.
    assert fixed.tolist() == [1, 1, 1, 1, 0, 0, 0, 0]

    # An exit already on the next day is untouched.
    pos2 = pd.Series([1, 1, 1, 1, 0, 0, 0, 0], index=idx, dtype=float)
    assert _enforce_t_plus_one(pos2, "Asia/Shanghai").tolist() == pos2.tolist()


def test_fractional_weights_scale_exposure():
    closes = list(np.linspace(100, 200, 80))  # +100% trend

    class HalfLong(Strategy):
        name = "half_long"

        def target_position(self, bars):
            return pd.Series(0.5, index=bars.index)

    class FullLong(Strategy):
        name = "full_long"

        def target_position(self, bars):
            return pd.Series(1.0, index=bars.index)

    bars = make_bars(closes)
    r_half = Engine(CRYPTO).run(HalfLong(), bars).to_frame().loc["full", "total_return_pct"]
    r_full = Engine(CRYPTO).run(FullLong(), bars).to_frame().loc["full", "total_return_pct"]
    assert 0 < r_half < r_full
    assert r_half == pytest.approx(r_full / 2, rel=0.25)  # roughly half the P&L


def test_rebalance_throttle_cuts_churn():
    rng = np.random.RandomState(7)
    closes = list(100 + np.cumsum(rng.normal(0, 0.3, 300)))

    class Jittery(Strategy):
        """Weight wiggles ±1% every bar around 0.5 — economically meaningless."""

        name = "jittery"

        def target_position(self, bars):
            noise = 0.01 * np.sin(np.arange(len(bars)))
            return pd.Series(0.5 + noise, index=bars.index)

    bars = make_bars(closes)
    thr = Engine(CRYPTO, rebalance_eps=0.05).run(Jittery(), bars)
    raw = Engine(CRYPTO, rebalance_eps=0.0).run(Jittery(), bars)
    fees_thr = thr.to_frame().loc["full", "total_fees"]
    fees_raw = raw.to_frame().loc["full", "total_fees"]
    # The initial 0.5 entry dominates fees in both runs; the throttled run
    # must still avoid the ~300 micro-rebalances the raw run pays for.
    assert fees_thr < fees_raw / 3, "throttle should suppress sub-eps rebalances"
    assert thr.to_frame().loc["full", "trades"] < raw.to_frame().loc["full", "trades"] / 10


def test_t_plus_one_fractional_reduction_blocked():
    from qtrade.backtest.engine import _enforce_t_plus_one

    idx = pd.DatetimeIndex(
        [f"2026-07-0{d} {h}:00" for d in (6, 7) for h in ("09", "10", "13", "14")],
        tz="Asia/Shanghai",
    ).tz_convert("UTC")
    # Scale in to 0.8 on day 1, try to cut to 0.3 same day -> held at 0.8.
    pos = pd.Series([0.4, 0.8, 0.3, 0.3, 0.3, 0, 0, 0], index=idx, dtype=float)
    fixed = _enforce_t_plus_one(pos, "Asia/Shanghai")
    assert fixed.tolist() == [0.4, 0.8, 0.8, 0.8, 0.3, 0, 0, 0]


def test_store_roundtrip_and_dedup(tmp_path):
    store = BarStore(root=tmp_path)
    bars = normalize_ohlcv(make_bars([1.0, 2.0, 3.0]))
    store.save(bars, "crypto", "TEST/USDT", "1h")
    # Overlapping save: last two bars again with a revised close.
    revised = bars.iloc[1:].copy()
    revised["close"] = [20.0, 30.0]
    store.save(revised, "crypto", "TEST/USDT", "1h")
    out = store.load("crypto", "TEST/USDT", "1h")
    assert len(out) == 3
    assert out["close"].tolist() == [1.0, 20.0, 30.0]  # new data wins
