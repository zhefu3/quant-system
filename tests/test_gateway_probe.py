"""Gateway probe sentinels: port closed must read 'down' (not zombie, not
ok), and a port that accepts but speaks no IB protocol must read 'zombie' —
the 07-21 starvation mode the TCP-only probe was blind to."""

import socket
import threading

import pytest

from qtrade.live.healthcheck import _ib_gateway_probe

# the suite's no-network guard stays on; these tests only need loopback
pytestmark = pytest.mark.allow_hosts(["127.0.0.1"])


def test_closed_port_is_down():
    # find a port that is definitely closed
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()  # released -> nothing listens there
    assert _ib_gateway_probe(port=port) == "down"


def test_mute_listener_is_zombie():
    """A socket that accepts connections but never completes the IB
    handshake — the zombie signature."""
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    stop = threading.Event()

    def _accept_and_hold():
        srv.settimeout(30)
        try:
            conn, _ = srv.accept()
            stop.wait(30)  # hold the connection open, say nothing
            conn.close()
        except OSError:
            pass

    t = threading.Thread(target=_accept_and_hold, daemon=True)
    t.start()
    try:
        assert _ib_gateway_probe(port=port) == "zombie"
    finally:
        stop.set()
        srv.close()
