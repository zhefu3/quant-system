"""Pre-trade risk gate: bounds, dd halt, stale-data skip."""

from __future__ import annotations

import pandas as pd
import pytest

from qtrade.live.risk import RiskGate, RiskLimits


@pytest.fixture
def gate(tmp_path):
    return RiskGate(RiskLimits(max_weight=0.25, max_gross=1.0, dd_halt=0.20,
                               max_data_age_bars=6), tmp_path)


def test_normal_targets_pass_untouched(gate):
    targets = {"A": 0.10, "B": -0.15}
    out, notes = gate.apply(targets, live_dd=-0.05)
    assert out == targets and notes == []


def test_weight_clamp(gate):
    out, notes = gate.apply({"A": 0.60, "B": -0.40}, live_dd=0.0)
    assert out["A"] == 0.25 and out["B"] == -0.25
    assert len(notes) == 2


def test_gross_scaling(gate):
    out, notes = gate.apply({s: 0.25 for s in "ABCDEF"}, live_dd=0.0)
    assert sum(abs(w) for w in out.values()) == pytest.approx(1.0)
    assert any("scaled" in n for n in notes)


def test_dd_halt_flattens_and_persists(gate):
    out, notes = gate.apply({"A": 0.10}, live_dd=-0.25)
    assert out == {"A": 0.0}
    assert gate.is_halted() and gate.halt_file.exists()
    # marker persists: next tick stays flat even with healthy dd
    out2, notes2 = gate.apply({"A": 0.10}, live_dd=-0.01)
    assert out2 == {"A": 0.0}
    # human removes the marker -> trading resumes
    gate.halt_file.unlink()
    out3, _ = gate.apply({"A": 0.10}, live_dd=-0.01)
    assert out3 == {"A": 0.10}


def test_stale_symbols(gate):
    now = pd.Timestamp("2026-07-12 12:00", tz="UTC")
    fresh = pd.DataFrame({"close": [1.0]},
                         index=[now - pd.Timedelta(hours=2)])
    stale = pd.DataFrame({"close": [1.0]},
                         index=[now - pd.Timedelta(hours=10)])
    assert gate.stale_symbols({"F": fresh, "S": stale}, now, "1h") == ["S"]
    assert gate.stale_symbols({"E": fresh.iloc[:0]}, now, "1h") == ["E"]


def test_paper_tick_skips_on_stale_data(tmp_path, monkeypatch):
    from qtrade.live import paper as paper_mod
    from qtrade.presets import PRESETS

    p = PRESETS["cn_futures"]
    old = pd.Timestamp.now("UTC") - pd.Timedelta(days=30)
    bars = pd.DataFrame({"open": [1.0], "high": [1.0], "low": [1.0],
                         "close": [1.0], "volume": [1.0]}, index=[old])
    monkeypatch.setattr(paper_mod, "fetch_live_bars",
                        lambda preset, adapter: {s: bars for s in p.symbols})
    trader = paper_mod.PaperTrader(p, state_dir=tmp_path)
    summary = trader.tick()
    assert set(summary["skipped_stale"]) == set(p.symbols)
    assert summary["fills"] == []
    assert not (tmp_path / "trades.csv").exists()  # no state was written
