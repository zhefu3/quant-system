"""Push alerting: a WARN must interrupt a human, not wait for the next manual
health run (the 07-16 hang sat unnoticed for 10h behind a green launchd label).

macOS-native via osascript — zero dependencies, works offline. De-duplication
lives in a state file: notify when the finding set changes (new problem or
all-clear), and re-remind every REMIND_HOURS while findings persist, so a
standing WARN neither spams hourly nor silently ages out.
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
STATE_FILE = REPO / "outputs" / "alerts_state.json"
REMIND_HOURS = 24


def _notify(title: str, message: str) -> None:
    script = (f"display notification {json.dumps(message)} "
              f"with title {json.dumps(title)} sound name \"Basso\"")
    try:
        subprocess.run(["osascript", "-e", script], timeout=10, check=False,
                       capture_output=True)
    except Exception:  # noqa: BLE001 — alerting must never break the caller
        pass


def push_health_alerts(findings: list[str]) -> str:
    """Reconcile current findings against alert state; push if warranted.

    Returns what happened: "alerted" / "reminded" / "all-clear" / "quiet".
    """
    cur = sorted(set(findings))
    prev: dict = {"findings": [], "last_push": 0.0}
    if STATE_FILE.exists():
        try:
            prev = json.loads(STATE_FILE.read_text())
        except Exception:  # noqa: BLE001 — corrupt state = start fresh
            pass

    outcome = "quiet"
    now = time.time()
    if cur:
        changed = cur != prev.get("findings", [])
        overdue = now - float(prev.get("last_push", 0)) > REMIND_HOURS * 3600
        if changed or overdue:
            head = cur[0] if len(cur[0]) < 120 else cur[0][:117] + "..."
            more = f" (+{len(cur) - 1} more)" if len(cur) > 1 else ""
            _notify("qtrade health", f"{len(cur)} WARN: {head}{more}")
            outcome = "alerted" if changed else "reminded"
            prev["last_push"] = now
    elif prev.get("findings"):
        _notify("qtrade health", "all clear — previous warnings resolved")
        outcome = "all-clear"
        prev["last_push"] = now

    prev["findings"] = cur
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(prev, indent=2))
    return outcome
