from __future__ import annotations

import sys

import uvicorn

from gui import backend


def test_gui_main_starts_without_console_streams(monkeypatch) -> None:
    started_configs: list[uvicorn.Config] = []

    def record_start(server: uvicorn.Server, sockets=None) -> None:
        started_configs.append(server.config)
        server.started = True

    monkeypatch.setattr(uvicorn.Server, "run", record_start)

    with monkeypatch.context() as no_console:
        no_console.setattr(sys, "argv", ["plc-sim gui", "--no-open"])
        no_console.setattr(sys, "stdout", None)
        no_console.setattr(sys, "stderr", None)
        result = backend.main()

    assert result == 0
    assert len(started_configs) == 1
    assert started_configs[0].use_colors is False
