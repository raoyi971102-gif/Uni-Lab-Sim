from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from gui.backend import SZLAB_WORKFLOW_IDS, AgentStartReq, _extend_szlab_command
from szlab_handshake_agent import WORKFLOW_IDS


def test_workflow_catalog_matches_agent_and_gui() -> None:
    html = (Path(__file__).parents[1] / "gui" / "static" / "index.html").read_text(
        encoding="utf-8"
    )

    assert SZLAB_WORKFLOW_IDS == WORKFLOW_IDS
    for workflow_id in WORKFLOW_IDS:
        assert f'value="{workflow_id}"' in html


def test_attachment_flow_exposes_position_and_pump_options() -> None:
    app_js = (Path(__file__).parents[1] / "gui" / "static" / "app.js").read_text(
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
    app_js = (Path(__file__).parents[1] / "gui" / "static" / "app.js").read_text(
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


def test_robot_atomic_profiles_are_selectable_with_all_station_options() -> None:
    """单、双 TASK 机器人原子动作工作流可由 GUI 选择，并显示全部工站参数。

    参数：无。
    返回：无；断言工作流（Workflow）标识进入全部相关 GUI 选项集。
    """

    html = (Path(__file__).parents[1] / "gui" / "static" / "index.html").read_text(
        encoding="utf-8"
    )
    app_js = (Path(__file__).parents[1] / "gui" / "static" / "app.js").read_text(
        encoding="utf-8"
    )
    workflow_ids = (
        "s_z_lab_单样品原子流程_机器人原子动作",
        "s_z_lab_双任务单样品原子流程_机器人原子动作",
    )
    option_sets = (
        "SZLAB_S04_WORKFLOWS",
        "SZLAB_PUMP_WORKFLOWS",
        "SZLAB_S07_WORKFLOWS",
        "SZLAB_S09_WORKFLOWS",
    )

    for workflow_id in workflow_ids:
        assert f'value="{workflow_id}"' in html
        for option_set in option_sets:
            values = app_js.split(f"const {option_set}", maxsplit=1)[1].split(
                "]);", maxsplit=1
            )[0]
            assert workflow_id in values


def test_official_stack_workflow_exposes_only_pump_options() -> None:
    app_js = (Path(__file__).parents[1] / "gui" / "static" / "app.js").read_text(
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
        workflow="s04_robot_stirring_workflow",
        position=5,
        pump=3,
        delay_ms=250,
        poll_ms=40,
        s09_remaining_volume_ml=88.5,
        s07_balance_reading=1.25,
        s09_balance_reading=2.5,
    )
    cmd = ["python", "szlab_handshake_agent.py"]

    options = _extend_szlab_command(cmd, req)

    assert options == {
        "workflow": "s04_robot_stirring_workflow",
        "position": 5,
        "pump": 3,
        "delay_ms": 250,
        "poll_ms": 40,
        "s09_remaining_volume_ml": 88.5,
        "s07_balance_reading": 1.25,
        "s09_balance_reading": 2.5,
    }
    assert cmd == [
        "python",
        "szlab_handshake_agent.py",
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
    ]


def test_szlab_agent_rejects_unknown_workflow() -> None:
    req = AgentStartReq(profile="szlab", workflow="unknown_workflow")

    with pytest.raises(HTTPException) as exc_info:
        _extend_szlab_command([], req)

    assert exc_info.value.status_code == 400
    assert "未知 SZLab 工作流" in str(exc_info.value.detail)


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
