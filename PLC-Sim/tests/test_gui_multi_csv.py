from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi import HTTPException

from gui import server_routes
from gui.backend_state import STATE
from gui.server_routes import ServerStartReq, _resolve_server_node_paths

ROOT = Path(__file__).parents[1]
HTML = (ROOT / "gui" / "static" / "index.html").read_text(encoding="utf-8")
JS = (ROOT / "gui" / "static" / "simulation.js").read_text(encoding="utf-8")


def _write_csv(path: Path, name: str) -> None:
    path.write_text(
        "Name,EnglishName,NodeType,DataType,NodeLanguage,NodeId\n"
        f"{name},{name},VARIABLE,BOOLEAN,English,ns=4;s=uniab|{name}\n",
        encoding="utf-8",
    )


def test_multi_csv_controls_and_request_are_exposed_in_gui() -> None:
    assert 'id="simCsvFile" type="file" accept=".csv,text/csv" multiple' in HTML
    assert '<textarea id="simCsv"' in HTML
    assert "function parseNodeTablePaths" in JS
    assert 'csvs: profile === "csv" && nodePaths.length ? nodePaths : null' in JS
    assert 'requireBackendCapability("multi_csv_server"' in JS


def test_node_paths_preserve_order_and_remove_duplicates(tmp_path: Path) -> None:
    first = tmp_path / "first.csv"
    second = tmp_path / "second.csv"
    _write_csv(first, "First")
    _write_csv(second, "Second")

    paths = _resolve_server_node_paths(
        ServerStartReq(csvs=[str(first), str(second), str(first)]), "csv"
    )

    assert paths == [first.resolve(), second.resolve()]


def test_ptlc_profile_rejects_multiple_node_tables(tmp_path: Path) -> None:
    first = tmp_path / "first.yaml"
    second = tmp_path / "second.yaml"
    first.write_text("schema: test\n", encoding="utf-8")
    second.write_text("schema: test\n", encoding="utf-8")

    with pytest.raises(HTTPException, match="只接受一份"):
        _resolve_server_node_paths(
            ServerStartReq(profile="ptlc", csvs=[str(first), str(second)]), "ptlc"
        )


def test_node_path_list_rejects_blanks_and_directories(tmp_path: Path) -> None:
    with pytest.raises(HTTPException, match="空路径"):
        _resolve_server_node_paths(ServerStartReq(csvs=[""]), "csv")
    with pytest.raises(HTTPException, match="节点表不存在"):
        _resolve_server_node_paths(ServerStartReq(csv=str(tmp_path)), "csv")


def test_server_start_forwards_every_csv_and_publishes_combined_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = tmp_path / "first.csv"
    second = tmp_path / "second.csv"
    _write_csv(first, "First")
    _write_csv(second, "Second")
    captured: dict[str, list[str]] = {}

    class RunningProcess:
        pid = 43210
        stdout = None
        stderr = None

        def poll(self) -> None:
            return None

    def fake_popen(command: list[str], **_kwargs) -> RunningProcess:
        captured["command"] = command
        return RunningProcess()

    monkeypatch.setattr(
        server_routes,
        "runtime_command",
        lambda _name, script, args, **_kwargs: ["python", str(script), *args],
    )
    monkeypatch.setattr(server_routes.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(server_routes, "pipe_to_logger", lambda *_args: None)
    monkeypatch.setattr(server_routes, "_wait_for_opc_server", lambda *_args: None)
    monkeypatch.setattr(
        server_routes, "connection_state_path", lambda: tmp_path / "connections.json"
    )
    STATE.attached = False
    STATE.server_proc = None

    try:
        result = asyncio.run(
            server_routes.api_server_start(
                ServerStartReq(csvs=[str(first), str(second)], host="127.0.0.1")
            )
        )

        command = captured["command"]
        csv_positions = [index for index, value in enumerate(command) if value == "--csv"]
        assert [command[index + 1] for index in csv_positions] == [
            str(first.resolve()),
            str(second.resolve()),
        ]
        assert result["count"] == 2
        assert result["csvs"] == [str(first.resolve()), str(second.resolve())]
        assert STATE.server_csv_paths == result["csvs"]
        assert len(STATE.server_node_defs) == 2
    finally:
        STATE.server_proc = None
        STATE.server_client_url = None
        STATE.server_csv_paths = []
        STATE.server_node_defs = []
        STATE.server_csv_id = None
