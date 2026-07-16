"""Sentinel: the tick wall-clock deadline must not be swallowable.

2026-07-16 incident: the 900s SIGALRM raised TimeoutError (an Exception),
which a book's per-item `except Exception` swallowed — disarming the one-shot
alarm and leaving the process to hang 10h on an SSL read. The deadline
exception is therefore BaseException-derived: broad per-item handlers must
let it propagate.
"""

import signal
import time

import pytest

from qtrade.cli import TickDeadline


def test_deadline_is_not_an_exception():
    assert issubclass(TickDeadline, BaseException)
    assert not issubclass(TickDeadline, Exception)


def test_deadline_survives_broad_except_loops():
    """An `except Exception: pass` loop (the per-bond/per-symbol pattern in
    book ticks) must NOT absorb the deadline."""

    def _deadline(signum, frame):
        raise TickDeadline("test deadline")

    old = signal.signal(signal.SIGALRM, _deadline)
    signal.setitimer(signal.ITIMER_REAL, 0.2)
    try:
        with pytest.raises(TickDeadline):
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:  # hang guard for the test itself
                try:
                    time.sleep(0.01)  # stand-in for a stalled network call
                except Exception:  # noqa: BLE001 — the incident's exact pattern
                    pass
            pytest.fail("deadline never fired or was swallowed")
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old)
