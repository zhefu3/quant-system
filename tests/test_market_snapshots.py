"""Session-day labeling for the live-forward snapshot collector.

The endpoints answer with whatever table is latest, so the collector must
decide the DATA date itself — a pre-market pull labeled with the wall-clock
day would store yesterday's session under today (caught live on first run,
2026-08-04; the repo's #1 defect class)."""

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "research"))

import collect_market_snapshots as cms  # noqa: E402


def _fake_now(monkeypatch, ts_cn: str):
    t = pd.Timestamp(ts_cn, tz="Asia/Shanghai").tz_convert("UTC")
    monkeypatch.setattr(cms, "utc_now", lambda: t)


def test_pre_market_pull_labels_previous_session(monkeypatch):
    _fake_now(monkeypatch, "2026-08-04 04:30")   # CN pre-market
    assert cms._ashare_session_day() == "2026-08-03"


def test_post_close_pull_labels_today(monkeypatch):
    _fake_now(monkeypatch, "2026-08-04 17:10")   # after CN close
    assert cms._ashare_session_day() == "2026-08-04"


def test_intraday_pull_still_labels_previous_session(monkeypatch):
    # 14:00 CN: today's pool is still forming — an intraday snapshot of a
    # half-finished session must not masquerade as the session record
    _fake_now(monkeypatch, "2026-08-04 14:00")
    assert cms._ashare_session_day() == "2026-08-03"
