from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from gui import agent_routes, backend
from gui.backend import (
    SZLAB_WORKFLOW_IDS,
    AgentStartReq,
    _extend_ptlc_command,
    _extend_szlab_command,
    api_version,
)
from szlab_handshake_agent import WORKFLOW_IDS


def test_workflow_catalog_matches_agent_and_gui() -> None:
    html = (Path(__file__).parents[1] / "gui" / "static" / "index.html").read_text(
        encoding="utf-8"
    )

    assert SZLAB_WORKFLOW_IDS == WORKFLOW_IDS
    for workflow_id in WORKFLOW_IDS:
        assert f'value="{workflow_id}"' in html


def test_attachment_flow_exposes_position_and_pump_options() -> None:
    app_js = (Path(__file__).parents[1] / "gui" / "static" / "simulation.js").read_text(
        encoding="utf-8"
    )
    workflow_id = "s_z_lab_单样品原子流程_无_s07_扫码"
    s04_workflows = app_js.split("const SZLAB_S04_WORKFLOWS", maxsplit=1)[1].split(
        "]);", maxsplit=1
    )[0]
    pump_workflows = app_js.split("const SZLAB_PUMP_WORKFLOWS", maxsplit=1)[1].split(
        "]);", maxsplit=1
    )[0]

    assert workflow_id in s04_workflows
    assert workflow_id in pump_workflows


def test_dual_task_attachment_profile_is_selectable_in_gui() -> None:
    """双 Task 握手场景可由 GUI 选择，并暴露共用工站参数。"""

    html = (Path(__file__).parents[1] / "gui" / "static" / "index.html").read_text(
        encoding="utf-8"
    )
    app_js = (Path(__file__).parents[1] / "gui" / "static" / "simulation.js").read_text(
        encoding="utf-8"
    )
    workflow_id = "s_z_lab_双任务单样品原子流程_无_s07_扫码"
    s04_workflows = app_js.split("const SZLAB_S04_WORKFLOWS", maxsplit=1)[1].split(
        "]);", maxsplit=1
    )[0]
    pump_workflows = app_js.split("const SZLAB_PUMP_WORKFLOWS", maxsplit=1)[1].split(
        "]);", maxsplit=1
    )[0]

    assert f'value="{workflow_id}"' in html
    assert workflow_id in s04_workflows
    assert workflow_id in pump_workflows


def test_official_stack_workflow_exposes_only_pump_options() -> None:
    app_js = (Path(__file__).parents[1] / "gui" / "static" / "simulation.js").read_text(
        encoding="utf-8"
    )
    workflow_id = "szlab_stack_s05_s06_workflow"
    s04_workflows = app_js.split("const SZLAB_S04_WORKFLOWS", maxsplit=1)[1].split(
        "]);", maxsplit=1
    )[0]
    pump_workflows = app_js.split("const SZLAB_PUMP_WORKFLOWS", maxsplit=1)[1].split(
        "]);", maxsplit=1
    )[0]

    assert workflow_id not in s04_workflows
    assert workflow_id in pump_workflows


def test_szlab_agent_options_are_forwarded_to_cli() -> None:
    req = AgentStartReq(
        profile="szlab",
        package_config="config/custom-szlab-package.yaml",
        workflow="s04_robot_stirring_workflow",
        position=5,
        pump=3,
        delay_ms=250,
        poll_ms=40,
        s09_remaining_volume_ml=88.5,
        s07_balance_reading=1.25,
        s09_balance_reading=2.5,
        time_scale=4,
        s1_host="0.0.0.0",
        s1_port=18055,
    )
    cmd = ["python", "szlab_handshake_agent.py"]

    options = _extend_szlab_command(cmd, req)

    assert options == {
        "package_config": "config/custom-szlab-package.yaml",
        "s1_host": "0.0.0.0",
        "s1_port": 18055,
        "workflow": "s04_robot_stirring_workflow",
        "position": 5,
        "pump": 3,
        "delay_ms": 250,
        "poll_ms": 40,
        "s09_remaining_volume_ml": 88.5,
        "s07_balance_reading": 1.25,
        "s09_balance_reading": 2.5,
        "time_scale": 4.0,
    }
    assert cmd == [
        "python",
        "szlab_handshake_agent.py",
        "--package-config",
        "config/custom-szlab-package.yaml",
        "--s1-host",
        "0.0.0.0",
        "--s1-port",
        "18055",
        "--workflow",
        "s04_robot_stirring_workflow",
        "--position",
        "5",
        "--pump",
        "3",
        "--delay-ms",
        "250",
        "--poll-ms",
        "40",
        "--s09-remaining-volume-ml",
        "88.5",
        "--s07-balance-reading",
        "1.25",
        "--s09-balance-reading",
        "2.5",
        "--time-scale",
        "4.0",
    ]


def test_szlab_agent_state_api_reports_package_snapshot(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class RunningProcess:
        def poll(self) -> None:
            return None

    state_file = tmp_path / "szlab-package-state.json"
    backend.write_json_file(
        state_file,
        {"schema": "unilab.package_simulation/v1", "session_id": "session-1"},
    )
    monkeypatch.setattr(backend._STATE, "agent_proc", RunningProcess())
    monkeypatch.setattr(backend._STATE, "agent_profile", "szlab")
    monkeypatch.setattr(backend._STATE, "agent_state_file", str(state_file))

    payload = asyncio.run(backend.api_szlab_agent_state())

    assert payload == {
        "ok": True,
        "running": True,
        "state": {
            "schema": "unilab.package_simulation/v1",
            "session_id": "session-1",
        },
    }


def test_ptlc_agent_only_forwards_generic_timing_options() -> None:
    req = AgentStartReq(
        profile="ptlc",
        workflow="unknown_szlab_workflow",
        delay_ms=75,
        poll_ms=10,
        time_scale=5,
        sensor_mode="federated",
        position=4,
    )
    cmd = ["python", "ptlc_handshake_agent.py"]
    options = _extend_ptlc_command(cmd, req)
    assert cmd == [
        "python",
        "ptlc_handshake_agent.py",
        "--delay-ms",
        "75",
        "--poll-ms",
        "10",
        "--time-scale",
        "5.0",
        "--sensor-mode",
        "federated",
    ]
    assert options == {
        "delay_ms": 75,
        "poll_ms": 10,
        "time_scale": 5.0,
        "sensor_mode": "federated",
    }


def test_ptlc_profiles_are_selectable_in_gui() -> None:
    root = Path(__file__).parents[1]
    html = (root / "gui" / "static" / "index.html").read_text(encoding="utf-8")
    app_js = "\n".join(
        (root / "gui" / "static" / filename).read_text(encoding="utf-8")
        for filename in ("app.js", "simulation.js", "variables.js")
    )
    assert html.count('<option value="ptlc">') == 2
    assert "config/ptlc_nodes.yaml" in app_js
    assert "config/ptlc_handshake.yaml" in app_js
    assert "requireBackendCapability" in app_js
    assert "data-element-index" in app_js
    assert "element_value" in app_js
    assert 'id="ptlcEventKind"' in html
    assert 'id="ptlcSensorMode"' in html
    assert 'id="btnPtlcWorldEvent"' in html
    assert 'post("/api/agent/ptlc/world"' in app_js
    capabilities = asyncio.run(api_version())["capabilities"]
    assert capabilities == {
        "szlab_package_runtime": True,
        "ptlc_server_profile": True,
        "ptlc_handshake_agent": True,
        "ptlc_write_ownership": True,
        "ptlc_behavior_contract": True,
        "project_version_history": True,
        "safe_online_deploy": False,
    }


def test_agent_start_reports_an_immediate_process_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """验证代理进程立即退出时启动 API 关闭失败。

    参数：``monkeypatch`` 隔离进程边界，``tmp_path`` 承载运行期状态文件。
    返回：无；断言 API 不会把已经退出的代理误报为启动成功。
    """

    class FailedProcess:
        """表示创建成功但立即以状态码 2 退出的外部代理进程。"""

        pid = 4321
        stdout = None
        stderr = None

        def poll(self) -> int:
            """返回进程退出码 2；无参数，返回整数退出码。"""

            return 2

    monkeypatch.setattr(
        agent_routes.subprocess,
        "Popen",
        lambda *args, **kwargs: FailedProcess(),
    )
    monkeypatch.setattr(agent_routes, "pipe_to_logger", lambda *args, **kwargs: None)
    monkeypatch.setattr(agent_routes, "runtime_data_dir", lambda: tmp_path)
    backend._STATE.agent_proc = None
    backend._STATE.attached = False

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(backend.api_agent_start(AgentStartReq(profile="ptlc")))

    assert exc_info.value.status_code == 500
    assert "启动后立即退出" in str(exc_info.value.detail)
    assert backend._STATE.agent_proc is None


def test_szlab_agent_rejects_unknown_workflow() -> None:
    req = AgentStartReq(profile="szlab", workflow="unknown_workflow")

    with pytest.raises(HTTPException) as exc_info:
        _extend_szlab_command([], req)

    assert exc_info.value.status_code == 400
    assert "未知 SZLab 工作流" in str(exc_info.value.detail)


def test_ptlc_world_route_only_persists_plc_input_facts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """验证 GUI 为独立设备模拟器提供原子 PLC 输入世界 seam。"""

    monkeypatch.setattr(agent_routes, "runtime_data_dir", lambda: tmp_path)
    backend._STATE.ptlc_world_file = None
    result = asyncio.run(
        agent_routes.api_ptlc_agent_world(
            agent_routes.PtlcWorldReq(
                feed_count=7,
                waste_count=3,
                sensors={"bottle_present": True},
            )
        )
    )
    assert result["world"] == {
        "feed_count": 7,
        "waste_count": 3,
        "sensors": {"bottle_present": True},
    }
    assert (tmp_path / "runtime" / "ptlc-world.json").is_file()


def test_ptlc_world_route_accepts_validated_material_events(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(agent_routes, "runtime_data_dir", lambda: tmp_path)
    backend._STATE.ptlc_world_file = None

    result = asyncio.run(
        agent_routes.api_ptlc_agent_world(
            agent_routes.PtlcWorldReq(
                events=[
                    agent_routes.PtlcWorldEventReq(
                        event_id="robot-1",
                        kind="material_transfer",
                        source="staging_a",
                        target="collect_bottle",
                    )
                ]
            )
        )
    )

    assert result["world"]["events"] == [
        {
            "event_id": "robot-1",
            "kind": "material_transfer",
            "source": "staging_a",
            "target": "collect_bottle",
        }
    ]


def test_ptlc_world_route_rejects_robot_commands(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(agent_routes, "runtime_data_dir", lambda: tmp_path)
    backend._STATE.ptlc_world_file = None

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            agent_routes.api_ptlc_agent_world(
                agent_routes.PtlcWorldReq(
                    events=[
                        agent_routes.PtlcWorldEventReq(
                            event_id="robot-2",
                            kind="robot_move",
                            source="external",
                            target="staging_a",
                        )
                    ]
                )
            )
        )

    assert exc_info.value.status_code == 400


@pytest.mark.parametrize(
    ("field_name", "value"),
    (("position", 7), ("pump", 0), ("poll_ms", 4), ("delay_ms", -1)),
)
def test_szlab_agent_rejects_out_of_range_parameters(
    field_name: str,
    value: int,
) -> None:
    with pytest.raises(ValidationError):
        AgentStartReq(profile="szlab", **{field_name: value})
