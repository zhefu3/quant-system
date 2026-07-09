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
