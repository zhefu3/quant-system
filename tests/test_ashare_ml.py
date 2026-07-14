"""ashare_ml (E61): pure accounting/selection logic, no network, no model."""

import pandas as pd
import pytest

from qtrade.live.ashare_ml import FEE, SLIP, mark, rebalance, top_k_weights


def test_top_k_respects_membership_and_equal_weight():
    scores = pd.Series({"A": 3.0, "B": 2.0, "C": 1.0, "D": 0.5})
    w = top_k_weights(scores, members={"B", "C", "D"}, k=2)
    assert set(w) == {"B", "C"}  # A excluded: not a member
    assert all(v == pytest.approx(0.5) for v in w.values())


def test_rebalance_charges_costs_and_gross_shadow_does_not():
    state = {"cash": 10_000.0, "positions": {},
             "gross_cash": 10_000.0, "gross_positions": {}}
    closes = {"X": 100.0, "Y": 50.0}
    state, fills = rebalance(state, {"X": 0.5, "Y": 0.5}, closes)
    assert len(fills) == 2
    net, gross = mark(state, closes)
    # net paid fee+slip on 10k notional; gross shadow paid nothing
    assert gross == pytest.approx(10_000.0)
    assert net == pytest.approx(10_000.0 * (1 - (FEE + SLIP)), rel=1e-3)


def test_rebalance_exits_dropped_names():
    state = {"cash": 0.0, "positions": {"X": 100.0},
             "gross_cash": 0.0, "gross_positions": {"X": 100.0}}
    closes = {"X": 100.0, "Y": 100.0}
    state, fills = rebalance(state, {"Y": 1.0}, closes)
    assert "X" not in state["positions"]
    assert "Y" in state["positions"]
    sides = {f["symbol"]: f["qty"] for f in fills}
    assert sides["X"] < 0 and sides["Y"] > 0


def test_mark_handles_missing_close_gracefully():
    state = {"cash": 5_000.0, "positions": {"X": 10.0, "GONE": 5.0},
             "gross_cash": 5_000.0, "gross_positions": {"X": 10.0}}
    net, gross = mark(state, {"X": 100.0})  # GONE has no close -> counts 0
    assert net == pytest.approx(6_000.0)
    assert gross == pytest.approx(6_000.0)


def test_observation_book_not_in_allocation_sleeves():
    from qtrade.live.allocate import SLEEVES

    assert "ashare_ml" not in SLEEVES
