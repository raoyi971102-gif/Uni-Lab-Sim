from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import cli as package_cli


def test_cli_defaults_to_gui_and_restores_process_arguments(monkeypatch):
    imported: list[str] = []
    delegated_argv: list[str] = []
    original_argv = list(sys.argv)

    def fake_import(name: str):
        imported.append(name)

        def fake_main() -> int:
            delegated_argv.extend(sys.argv)
            return 7

        return SimpleNamespace(main=fake_main)

    monkeypatch.setattr(package_cli.importlib, "import_module", fake_import)

    assert package_cli.main([]) == 7
    assert imported == ["gui.backend"]
    assert delegated_argv == ["plc-sim gui"]
    assert sys.argv == original_argv


def test_cli_forwards_remaining_arguments_to_selected_command(monkeypatch):
    delegated_argv: list[str] = []

    def fake_import(name: str):
        assert name == "server"

        def fake_main() -> int:
            delegated_argv.extend(sys.argv)
            return 0

        return SimpleNamespace(main=fake_main)

    monkeypatch.setattr(package_cli.importlib, "import_module", fake_import)

    assert package_cli.main(["server", "--port", "4860"]) == 0
    assert delegated_argv == ["plc-sim server", "--port", "4860"]


def test_cli_help_lists_all_installed_commands(capsys):
    assert package_cli.main(["--help"]) == 0
    output = capsys.readouterr().out
    assert "gui" in output
    assert "server" in output
    assert "handshake" in output
    assert "szlab-handshake" in output
    assert "ptlc-handshake" in output
    assert "ino" in output


def test_cli_reports_package_version(capsys):
    """验证统一 CLI 输出当前 PLC-Sim 包版本。

    参数：``capsys`` 是 pytest 的标准输出捕获器。
    返回：无；断言版本参数成功且输出与发布元数据一致。
    """

    assert package_cli.main(["--version"]) == 0
    assert capsys.readouterr().out.strip() == "0.2.6"


def test_runtime_command_uses_python_script_in_source_mode(monkeypatch):
    monkeypatch.delattr(package_cli.sys, "frozen", raising=False)

    command = package_cli.runtime_command(
        "server",
        Path("/checkout/server.py"),
        ["--port", "4855"],
        python_executable="/venv/bin/python",
    )

    assert command == [
        "/venv/bin/python",
        str(Path("/checkout/server.py")),
        "--port",
        "4855",
    ]


def test_runtime_command_reenters_frozen_executable(monkeypatch):
    monkeypatch.setattr(package_cli.sys, "frozen", True, raising=False)
    monkeypatch.setattr(package_cli.sys, "executable", "/Applications/PLC-Sim")

    command = package_cli.runtime_command(
        "szlab-handshake",
        Path("/bundle/szlab_handshake_agent.py"),
        ["--url", "opc.tcp://127.0.0.1:4855/xuse_sim/"],
        python_executable="/ignored/python",
    )

    assert command == [
        "/Applications/PLC-Sim",
        "szlab-handshake",
        "--url",
        "opc.tcp://127.0.0.1:4855/xuse_sim/",
    ]
