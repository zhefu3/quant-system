"""Soft per-call timeouts for flaky free endpoints.

Many free-data libraries (akshare among them) issue requests with no timeout,
and requests overrides socket.setdefaulttimeout — a stalled TLS read otherwise
blocks until a tick's 900s wall-clock kills everything (2026-07-16 incident).
Running the call in a daemon thread with a join timeout makes the failure cost
one item, not the process. A timed-out worker is abandoned; the short-lived
per-book process exits soon after and the OS reaps it.
"""

from __future__ import annotations

import threading


def call_with_timeout(fn, timeout: float, /, *args, **kwargs):
    out: dict = {}

    def _run():
        try:
            out["v"] = fn(*args, **kwargs)
        except Exception as e:  # noqa: BLE001 — surfaced to the caller below
            out["e"] = e

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        raise TimeoutError(f"{getattr(fn, '__name__', fn)} timed out after {timeout}s")
    if "e" in out:
        raise out["e"]
    return out.get("v")
