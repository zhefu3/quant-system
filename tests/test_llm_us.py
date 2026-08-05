"""llm_us (E70): pure logic + per-trading-day decision caching, no API calls."""

import json

import numpy as np
import pandas as pd
import pytest

from qtrade.live import llm_us
from qtrade.presets import PRESETS


@pytest.fixture
def bars():
    rng = np.random.RandomState(0)
    # business-day index: the last completed US session is the cache key
    idx = pd.bdate_range("2026-01-01", periods=120, tz="UTC")
    out = {}
    for s in PRESETS["llm_us"].symbols:
        c = pd.Series(100 * np.exp(np.cumsum(rng.normal(0, 0.02, 120))), index=idx)
        out[s] = pd.DataFrame({"open": c, "high": c, "low": c, "close": c,
                               "volume": 1.0}).rename_axis("ts")
    return out


def test_universe_is_the_frozen_appendix():
    """presets must match the freeze artifact byte for byte (E70 prereg)."""
    from pathlib import Path

    art = (Path(llm_us.__file__).resolve().parents[2] / "research" / "artifacts"
           / "llm_us_universe_20260804.json")
    frozen = json.loads(art.read_text())["top50"]
    assert sorted(PRESETS["llm_us"].symbols) == sorted(frozen)
    assert len(PRESETS["llm_us"].symbols) == 50


def test_session_date_is_last_completed_bar(bars):
    last = max(b.index[-1] for b in bars.values())
    assert llm_us.session_date(bars) == last.strftime("%Y-%m-%d")


def test_market_brief_covers_universe(bars):
    brief = llm_us.market_brief(bars)
    for sym in PRESETS["llm_us"].symbols:
        assert sym in brief
    assert "1m" in brief and "vol30d" in brief


def test_parse_decision_long_only_and_caps_gross():
    syms = PRESETS["llm_us"].symbols
    payload = {"weights": {s: (-0.5 if i % 2 else 0.5)
                           for i, s in enumerate(syms)}}
    w = llm_us.parse_decision(payload, syms)
    assert all(v >= 0.0 for v in w.values())          # long-only: shorts -> 0
    assert all(v <= llm_us.MAX_W + 1e-9 for v in w.values())
    assert sum(w.values()) <= 1.0 + 1e-9


def test_parse_decision_missing_names_default_flat():
    syms = PRESETS["llm_us"].symbols
    w = llm_us.parse_decision({"weights": {"NVDA": 0.04}}, syms)
    assert w["NVDA"] == 0.04
    assert all(v == 0.0 for s, v in w.items() if s != "NVDA")


def test_targets_fn_uses_cached_decision_without_llm(bars, tmp_path, monkeypatch):
    # a cached decision for the session must short-circuit the whole LLM chain
    monkeypatch.setattr(llm_us, "DECISIONS", tmp_path)
    skey = llm_us.session_date(bars)
    (tmp_path / f"{skey}.json").write_text(json.dumps(
        {"date": skey, "weights": {"NVDA": 0.05}}))

    def boom(*a, **k):  # any LLM call is a test failure
        raise AssertionError("LLM chain invoked despite cached decision")

    monkeypatch.setattr(llm_us, "run_committee", boom)
    targets, closes = llm_us.make_targets_fn(PRESETS["llm_us"])(bars)
    assert targets["NVDA"] == 0.05
    assert targets["AAPL"] == 0.0
    assert set(closes) == set(PRESETS["llm_us"].symbols)


def test_weekend_tick_reuses_fridays_key(bars, tmp_path, monkeypatch):
    """Saturday's tick sees Friday as the last completed session — same key,
    no second committee call for the same trading day."""
    monkeypatch.setattr(llm_us, "DECISIONS", tmp_path)
    skey = llm_us.session_date(bars)  # a Friday-or-earlier bdate
    (tmp_path / f"{skey}.json").write_text(json.dumps(
        {"date": skey, "weights": {"MSFT": 0.03}}))
    monkeypatch.setattr(llm_us, "run_committee",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError))
    t1, _ = llm_us.make_targets_fn(PRESETS["llm_us"])(bars)
    t2, _ = llm_us.make_targets_fn(PRESETS["llm_us"])(bars)  # "next hour"
    assert t1 == t2


def test_observation_book_not_in_allocation_sleeves():
    from qtrade.live.allocate import SLEEVES

    assert "llm_us" not in SLEEVES


def test_book_outcome_from_equity_record(tmp_path):
    eq = tmp_path / "equity.csv"
    eq.write_text("ts,equity\n"
                  "2026-07-01 00:05:00+00:00,10000\n"
                  "2026-07-08 00:05:00+00:00,10200\n"
                  "2026-07-15 00:05:00+00:00,9900\n")
    r = llm_us.book_outcome("2026-07-01", equity_file=eq)
    assert r == pytest.approx(0.02)
    assert llm_us.book_outcome("2026-07-10", equity_file=eq) is None  # not matured


def test_prompt_hash_is_pinned_and_matches():
    """The A/B's subject includes the prompt; drift must be loud."""
    assert llm_us._prompt_hash() == llm_us.PROMPT_SHA256


def test_prompt_drift_refuses_new_decisions(monkeypatch):
    monkeypatch.setattr(llm_us, "PROMPT_SHA256", "0" * 64)
    assert llm_us._prompt_hash() != llm_us.PROMPT_SHA256


def test_preset_matches_prereg_bounds():
    """|w|<=0.05, gross<=1.0, dd_halt 0.15, long-only cost rules (E70)."""
    p = PRESETS["llm_us"]
    assert p.risk.max_weight == 0.05
    assert p.risk.max_gross == 1.0
    assert p.risk.dd_halt == 0.15
    assert p.rules.allow_short is False
    assert p.rules.fee_rate == 0.0001 and p.rules.slippage == 0.0003
    assert llm_us.MAX_W == p.risk.max_weight
