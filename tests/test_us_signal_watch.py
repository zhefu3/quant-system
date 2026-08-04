"""Flip detection for the US signal watcher (reports a system, advises nothing)."""

import json

import pandas as pd
import pytest

from qtrade.live import us_signal_watch as w


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    sig = tmp_path / "signals.csv"
    monkeypatch.setattr(w, "SIGNALS", sig)
    monkeypatch.setattr(w, "STATE", tmp_path / "state.json")
    monkeypatch.setattr(w, "DIGEST", tmp_path / "digest.md")
    monkeypatch.setattr(w, "_notify", lambda *a, **k: None)
    return sig


def _write(sig, ts, rows):
    pd.DataFrame([{"ts": ts, "symbol": s, "target_w": tw, "held_w": tw}
                  for s, tw in rows]).to_csv(sig, index=False)


def test_first_run_seeds_baseline_without_flipping(sandbox):
    _write(sandbox, "2026-08-04 22:00:00+00:00", [("SPY", 0.10), ("QQQ", 0.0)])
    assert w.run_watch() == []          # nothing to compare against yet
    assert json.loads(w.STATE.read_text())["stances"]["SPY"] == "LONG"


def test_flip_notifies_and_writes_digest(sandbox):
    _write(sandbox, "2026-08-04 22:00:00+00:00", [("SPY", 0.10), ("QQQ", 0.0)])
    w.run_watch()
    _write(sandbox, "2026-08-05 22:00:00+00:00", [("SPY", 0.0), ("QQQ", 0.08)])
    flips = w.run_watch()
    assert len(flips) == 2 and "SPY" in flips[1] or "SPY" in flips[0]
    text = w.DIGEST.read_text()
    assert "SPY 转现金" in text and "QQQ 转持有" in text
    assert "非投资建议" in text          # the framing ships with every entry


def test_residual_weight_is_not_conviction(sandbox):
    _write(sandbox, "2026-08-04 22:00:00+00:00", [("GLD", 0.10)])
    w.run_watch()
    _write(sandbox, "2026-08-05 22:00:00+00:00", [("GLD", 0.004)])  # < LONG_EPS
    assert w.run_watch() == [f"GLD 转现金 (LONG→FLAT)"]
