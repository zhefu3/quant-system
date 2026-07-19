"""Alert de-dup: a standing WARN must alert once, re-remind daily, and send
one all-clear when resolved — never hourly spam, never silent aging-out."""

import json

import qtrade.live.alerts as alerts


def _setup(tmp_path, monkeypatch):
    sent = []
    monkeypatch.setattr(alerts, "STATE_FILE", tmp_path / "alerts_state.json")
    monkeypatch.setattr(alerts, "_notify", lambda t, m: sent.append(m))
    return sent


def test_alert_once_then_quiet(tmp_path, monkeypatch):
    sent = _setup(tmp_path, monkeypatch)
    assert alerts.push_health_alerts(["book X stale"]) == "alerted"
    assert alerts.push_health_alerts(["book X stale"]) == "quiet"
    assert len(sent) == 1


def test_new_finding_realerts(tmp_path, monkeypatch):
    sent = _setup(tmp_path, monkeypatch)
    alerts.push_health_alerts(["book X stale"])
    assert alerts.push_health_alerts(["book X stale", "book Y HALTED"]) == "alerted"
    assert len(sent) == 2


def test_reminder_after_window(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    alerts.push_health_alerts(["book X stale"])
    state = json.loads(alerts.STATE_FILE.read_text())
    state["last_push"] = 0  # pretend the last push was long ago
    alerts.STATE_FILE.write_text(json.dumps(state))
    assert alerts.push_health_alerts(["book X stale"]) == "reminded"


def test_all_clear_fires_once(tmp_path, monkeypatch):
    sent = _setup(tmp_path, monkeypatch)
    alerts.push_health_alerts(["book X stale"])
    assert alerts.push_health_alerts([]) == "all-clear"
    assert alerts.push_health_alerts([]) == "quiet"
    assert len(sent) == 2
