from __future__ import annotations

import json
import time

from gui.backend import _read_server_connection_state


def test_connection_state_filters_pid_and_staleness(tmp_path, monkeypatch):
    state_path = tmp_path / "connections.json"
    monkeypatch.setenv("PLCSIM_CONNECTION_STATE", str(state_path))
    payload = {
        "server_pid": 1234,
        "generated_at": time.time(),
        "tcp_connection_count": 1,
        "session_count": 1,
        "clients": [
            {
                "host": "192.0.2.10",
                "port": 50123,
                "connected_at": time.time() - 2,
                "session_state": "Activated",
            }
        ],
    }
    state_path.write_text(json.dumps(payload), encoding="utf-8")

    current = _read_server_connection_state(expected_pid=1234, running=True)
    assert current["available"] is True
    assert current["tcp_connection_count"] == 1
    assert current["clients"][0]["port"] == 50123

    wrong_pid = _read_server_connection_state(expected_pid=9999, running=True)
    assert wrong_pid["available"] is False
    assert wrong_pid["stale"] is True

    payload["generated_at"] = time.time() - 10
    state_path.write_text(json.dumps(payload), encoding="utf-8")
    stale = _read_server_connection_state(expected_pid=1234, running=True)
    assert stale["available"] is False
    assert stale["stale"] is True
