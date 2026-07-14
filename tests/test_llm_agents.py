"""llm_agents (E60): pure logic + daily decision caching, no API calls."""

import json

import numpy as np
import pandas as pd
import pytest

from qtrade.live import llm_agents
from qtrade.presets import PRESETS


@pytest.fixture
def bars():
    rng = np.random.RandomState(0)
    idx = pd.date_range("2026-01-01", periods=120, freq="D", tz="UTC")
    out = {}
    for s in PRESETS["llm_agents"].symbols:
        c = pd.Series(100 * np.exp(np.cumsum(rng.normal(0, 0.03, 120))), index=idx)
        out[s] = pd.DataFrame({"open": c, "high": c, "low": c, "close": c,
                               "volume": 1.0}).rename_axis("ts")
    return out


def test_market_brief_covers_universe(bars):
    brief = llm_agents.market_brief(bars)
    for sym in PRESETS["llm_agents"].symbols:
        assert sym.split("/")[0] in brief
    assert "1m" in brief and "vol30d" in brief


def test_parse_decision_clamps_and_caps_gross():
    syms = PRESETS["llm_agents"].symbols
    payload = {"weights": {s.split("/")[0]: 0.5 for s in syms}}  # absurd: 0.5 each
    w = llm_agents.parse_decision(payload, syms)
    assert all(abs(v) <= llm_agents.MAX_W + 1e-9 for v in w.values())
    assert sum(abs(v) for v in w.values()) <= 1.0 + 1e-9


def test_parse_decision_missing_coins_default_flat():
    syms = PRESETS["llm_agents"].symbols
    w = llm_agents.parse_decision({"weights": {"BTC": 0.05}}, syms)
    assert w["BTC/USDT"] == 0.05
    assert all(v == 0.0 for s, v in w.items() if s != "BTC/USDT")


def test_targets_fn_uses_cached_decision_without_llm(bars, tmp_path, monkeypatch):
    # a cached decision for today must short-circuit the whole LLM chain
    monkeypatch.setattr(llm_agents, "DECISIONS", tmp_path)
    from datetime import datetime, timezone

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    (tmp_path / f"{today}.json").write_text(json.dumps(
        {"date": today, "weights": {"BTC/USDT": 0.08}}))

    def boom(*a, **k):  # any LLM call is a test failure
        raise AssertionError("LLM chain invoked despite cached decision")

    monkeypatch.setattr(llm_agents, "run_committee", boom)
    targets, closes = llm_agents.make_targets_fn(PRESETS["llm_agents"])(bars)
    assert targets["BTC/USDT"] == 0.08
    assert targets["ETH/USDT"] == 0.0
    assert set(closes) == set(PRESETS["llm_agents"].symbols)


def test_observation_book_not_in_allocation_sleeves():
    from qtrade.live.allocate import SLEEVES

    assert "llm_agents" not in SLEEVES


def test_book_outcome_from_equity_record(tmp_path):
    eq = tmp_path / "equity.csv"
    eq.write_text("ts,equity\n"
                  "2026-07-01 00:05:00+00:00,10000\n"
                  "2026-07-08 00:05:00+00:00,10200\n"
                  "2026-07-15 00:05:00+00:00,9900\n")
    r = llm_agents.book_outcome("2026-07-01", equity_file=eq)
    assert r == pytest.approx(0.02)
    assert llm_agents.book_outcome("2026-07-10", equity_file=eq) is None  # not matured


def test_reflection_written_back_and_shown_in_memory(bars, tmp_path, monkeypatch):
    monkeypatch.setattr(llm_agents, "DECISIONS", tmp_path)
    old = tmp_path / "2026-07-01.json"
    old.write_text(json.dumps({"date": "2026-07-01", "rationale": "short everything",
                               "weights": {"BTC/USDT": -0.05}}))
    monkeypatch.setattr(llm_agents, "book_outcome", lambda date, **k: 0.031)

    class FakeMsg:
        content = [type("B", (), {"type": "text",
                                  "text": "Call was right. Thesis held. Lesson: X."})]

    class FakeClient:
        class messages:  # noqa: N801
            @staticmethod
            def create(**kw):
                return FakeMsg()

    n = llm_agents.reflect_matured(FakeClient(), bars)
    assert n == 1
    d = json.loads(old.read_text())
    assert d["reflection"].startswith("Call was right")
    assert d["outcome"]["book_ret"] == 0.031
    mem = llm_agents.recent_memory()
    assert "lesson: Call was right" in mem
