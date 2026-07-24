"""Structural prevention for the repo's dominant defect class.

Three incidents in ten days were timezone/date-boundary bugs (hfq archive
convention 07-15, wall-clock freshness 07-21, UTC-midnight .date() shift
07-22). This test makes the convention mechanical: inside qtrade/, a
``.date()`` call or ``.index.date`` access must sit on a line that either
already converted (``tz_convert``), goes through ``timeconv``, or carries
an explicit ``# tz-ok:`` tag stating why naked is safe. No tag, no merge.
"""

import re
from pathlib import Path

import pandas as pd
import pytest

from qtrade import timeconv

PKG = Path(__file__).resolve().parents[1] / "qtrade"
DATE_CALL = re.compile(r"\.date\(\)|\.index\.date\b|\.dt\.date\b")
SANCTIONED = ("tz_convert", "timeconv", "tz-ok:", "cn_date", "utc_date")


def test_no_naked_date_conversions_in_package():
    offenders = []
    for path in sorted(PKG.rglob("*.py")):
        if path.name == "timeconv.py":  # the helpers themselves
            continue
        for lineno, line in enumerate(path.read_text().splitlines(), 1):
            if line.lstrip().startswith("#"):  # prose about .date() is not a call
                continue
            if DATE_CALL.search(line) and not any(s in line for s in SANCTIONED):
                offenders.append(f"{path.relative_to(PKG.parent)}:{lineno}: {line.strip()}")
    assert not offenders, (
        "naked .date() on a possibly-tz-aware stamp — convert explicitly via "
        "qtrade.timeconv (cn_date/utc_date) or tag the line '# tz-ok: <why>':\n"
        + "\n".join(offenders))


# --- helper semantics: the exact incident shapes, as regressions ---

def test_cn_date_on_cn_midnight_utc_stamp():
    # CN 2026-07-23 00:00 stored as UTC = 07-22T16:00Z; naked .date() says 22
    ts = pd.Timestamp("2026-07-22 16:00", tz="UTC")
    assert ts.date() == pd.Timestamp("2026-07-22").date()  # the bug # tz-ok: demonstrating it
    assert timeconv.cn_date(ts) == pd.Timestamp("2026-07-23").date()


def test_cn_date_naive_means_cn_wall_time():
    assert timeconv.cn_date("2026-07-23") == pd.Timestamp("2026-07-23").date()


def test_utc_date_declares_utc_roll():
    assert timeconv.utc_date(pd.Timestamp("2026-07-22 16:00", tz="UTC")) \
        == pd.Timestamp("2026-07-22").date()


def test_as_utc_localizes_naive_and_converts_aware():
    assert str(timeconv.as_utc("2026-07-23 08:00").tz) == "UTC"
    sh = pd.Timestamp("2026-07-23 00:00", tz="Asia/Shanghai")
    assert timeconv.as_utc(sh) == pd.Timestamp("2026-07-22 16:00", tz="UTC")


def test_cn_dates_rejects_naive_index():
    idx = pd.DatetimeIndex([pd.Timestamp("2026-07-23")])
    with pytest.raises(ValueError, match="tz-aware"):
        timeconv.cn_dates(idx)


def test_cn_dates_vectorized_shift():
    idx = pd.DatetimeIndex([pd.Timestamp("2026-07-22 16:00", tz="UTC")])
    assert list(timeconv.cn_dates(idx)) == [pd.Timestamp("2026-07-23").date()]
