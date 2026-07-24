"""The repo's timestamp conventions, in one place with teeth.

Timezone/date-boundary mistakes are this codebase's single most repeated
defect class — every one of these rules was paid for by an incident:

  - Bar-store stamps are tz-aware UTC everywhere.
  - CN daily bars are stamped CN-midnight-in-UTC: calling naked ``.date()``
    on one yields the PREVIOUS calendar day. The cross-source reconciler's
    first live firing was exactly that false positive (2026-07-22).
  - Crypto daily candles roll at UTC midnight → their calendar day IS the
    UTC date; use :func:`utc_date` to say so explicitly.
  - IBKR daily bars are stamped 00:00 America/New_York and complete at
    17:00 ET (see adapters/futures_ib.py).
  - Freshness and executability are judged against the POOL's own max bar
    date (`pool_ref_day`), never the wall clock and never a bench index —
    publication lag and weekend decisions both break those (07-21, thrice).

tests/test_time_conventions.py enforces usage: inside qtrade/, ``.date()``
on a line without ``tz_convert``/``timeconv``/an explicit ``# tz-ok:`` tag
fails the suite. New code either goes through these helpers or documents,
on the line, why naked is safe.
"""

from __future__ import annotations

import datetime as _dt

import pandas as pd

CN_TZ = "Asia/Shanghai"
NY_TZ = "America/New_York"


def utc_now() -> pd.Timestamp:
    return pd.Timestamp.now("UTC")


def as_utc(ts) -> pd.Timestamp:
    """tz-aware UTC view of *ts*; naive input is declared to BE UTC.

    (The book-heartbeat pattern: CSV round-trips drop tzinfo.)"""
    t = pd.Timestamp(ts)
    return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")


def cn_date(ts) -> _dt.date:
    """Calendar date in Asia/Shanghai; naive input is declared to already
    be CN wall time (akshare date strings, _last_session_date outputs)."""
    t = pd.Timestamp(ts)
    return t.date() if t.tzinfo is None else t.tz_convert(CN_TZ).date()


def utc_date(ts) -> _dt.date:
    """Calendar date in UTC — the explicit choice for UTC-rolling series
    (crypto daily candles) and day-granularity state counters."""
    return as_utc(ts).date()


def cn_dates(index: pd.DatetimeIndex):
    """Per-element Asia/Shanghai dates for a tz-aware index (vectorized)."""
    if index.tz is None:
        raise ValueError("cn_dates requires a tz-aware index — store stamps "
                         "are UTC; a naive index here hides the exact bug "
                         "this module exists to prevent")
    return index.tz_convert(CN_TZ).date
