from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from common import load_ptlc_nodes
from ptlc_agent_cli import main as ptlc_main
from ptlc_behavior import STATIONS, load_behavior_contracts

ROOT = Path(__file__).parents[1]
SNAPSHOT = ROOT / "config" / "ptlc_nodes.yaml"


def test_ptlc_snapshot_covers_v2_types_arrays_and_nested_gvl() -> None:
    nodes = load_ptlc_nodes(SNAPSHOT)
    by_name = {node.name_cn: node for node in nodes}

    assert len(nodes) >= 250
    assert {node.data_type for node in nodes} == {
        "BOOLEAN",
        "BYTE",
        "INT16",
        "INT32",
        "FLOAT",
        "DOUBLE",
        "STRING",
    }
    assert by_name["PLC_Axis_CommOperational"].array_len == 11
    assert by_name["Tank_State"].array_len == 8
    assert by_name["Rail_Pos_Target"].array_len == 6
    assert by_name["Rail_L2_ActionCode"].write_owner == "host"
    assert by_name["Rail_L2_State"].write_owner == "plc"
    assert by_name["Rail_ActPos"].write_owner == "plc"
    assert by_name["Sampling_clean_count"].write_owner == "maintenance"
    assert by_name["PLC_Ready"].browse_path == (
        "DeviceSet",
        "Inovance-ARM-Linux",
        "Resources",
        "Application",
        "GlobalVars",
        "Host_Computer",
    )
    for station in (
        "Sampling",
        "Collect",
        "Develop",
        "PhotoScrape",
        "FeedLift",
        "Pump",
        "Rail",
        "StagingA",
    ):
        for field in (
            "ActionCode",
            "RequestSeq",
            "Start",
            "Reset",
            "State",
            "ActiveCode",
            "AcceptedSeq",
            "CompletedSeq",
            "Step",
            "ErrorCode",
            "SafeState",
            "Retryable",
        ):
            assert f"{station}_L2_{field}" in by_name


def test_ptlc_behavior_snapshot_covers_all_l2_stations_and_rejects_unknowns() -> None:
    """验证行为快照同时保留派发器和动作的可执行语义。

    参数：无；从仓库内八工位快照加载。
    返回：无；断言计时常量、动作门禁与派发器注记没有在加载时丢失。
    """

    contracts = load_behavior_contracts(ROOT / "config" / "ptlc_behavior")
    assert set(contracts) == set(STATIONS)
    assert all(contract.accepts for contract in contracts.values())
    assert contracts["Rail"].unknown_code_error == 101
    assert contracts["StagingA"].unknown_code_error == 103
    assert set(contracts["FeedLift"].accepts) == {10, 11, 12, 13, 21, 22, 91}
    collect = contracts["Collect"]
    assert collect.constants["a30_drain_s"] == 20
    assert "State=10 RUNNING" in collect.dispatcher_notes
    assert collect.action(22).gate["收集平台瓶子有无传感器"] is False
    assert "不满足时冻结" in collect.action(22).notes


def test_optional_reference_contract_has_not_drifted() -> None:
    root = os.environ.get("PTLC_REFERENCE_ROOT")
    if not root:
        pytest.skip("set PTLC_REFERENCE_ROOT to enable cross-repository drift check")
    reference = Path(root) / "eit_ptlc" / "config" / "plc_nodes.yaml"
    if not reference.is_file():
        pytest.skip(f"PTLC reference node map not found: {reference}")

    expected = load_ptlc_nodes(reference)
    actual = load_ptlc_nodes(SNAPSHOT)
    normalize = lambda nodes: [
        (item.name_cn, item.data_type, item.array_len, item.browse_path)
        for item in nodes
    ]
    assert normalize(actual) == normalize(expected)
    reference_specs = Path(root) / "eit_ptlc" / "mock" / "behavior" / "specs"
    for snapshot_path in sorted((ROOT / "config" / "ptlc_behavior").glob("*.yaml")):
        assert (
            snapshot_path.read_bytes()
            == (reference_specs / snapshot_path.name).read_bytes()
        )


def test_ptlc_list_reports_complete_plc_action_coverage(capsys) -> None:
    """验证 CLI 明确公布 PLC-only 边界和完整动作覆盖率。

    参数：``capsys`` 捕获标准输出。
    返回：无；断言 55 个合法 PLC 动作全部建模且不含工作流运行时。
    """

    assert (
        ptlc_main(
            [
                "list",
                "--config",
                str(ROOT / "config" / "ptlc_handshake.yaml"),
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["scope"] == "plc-only"
    assert payload["orchestrator"] == "Uni-Lab OS Backend"
    assert payload["sensor_mode"] == "standalone"
    assert payload["modeled_actions"]["Rail"] == [10]
    assert all(not actions for actions in payload["unmodeled_actions"].values())
    assert payload["coverage"]["modeled"] == 55
    assert payload["coverage"]["accepted"] == 55
