from __future__ import annotations

import sys
from pathlib import Path

import uvicorn

from gui import backend


def test_subprocess_python_prefers_explicit_environment(monkeypatch, tmp_path: Path) -> None:
    """显式 PYTHON 应覆盖 GUI 解释器，支持 Workbench 传入选定环境。"""

    selected = tmp_path / "selected-python.exe"
    selected.touch()
    monkeypatch.setenv("PYTHON", str(selected))
    monkeypatch.setattr(sys, "executable", str(tmp_path / "gui-python.exe"))

    assert backend._find_python_exe() == str(selected)


def test_subprocess_python_inherits_gui_interpreter(monkeypatch, tmp_path: Path) -> None:
    """未显式覆盖时 Server/Agent 必须继承启动 GUI 的同一解释器。"""

    current = tmp_path / "szlab-unilab" / "python.exe"
    current.parent.mkdir()
    current.touch()
    monkeypatch.delenv("PYTHON", raising=False)
    monkeypatch.setattr(sys, "executable", str(current))

    assert backend._find_python_exe() == str(current)


def test_gui_main_starts_without_console_streams(monkeypatch) -> None:
    started_configs: list[uvicorn.Config] = []

    def record_start(server: uvicorn.Server, sockets=None) -> None:
        started_configs.append(server.config)
        server.started = True

    monkeypatch.setattr(uvicorn.Server, "run", record_start)

    with monkeypatch.context() as no_console:
        no_console.setattr(sys, "argv", ["opcua-sim gui", "--no-open"])
        no_console.setattr(sys, "stdout", None)
        no_console.setattr(sys, "stderr", None)
        result = backend.main()

    assert result == 0
    assert len(started_configs) == 1
    assert started_configs[0].use_colors is False
