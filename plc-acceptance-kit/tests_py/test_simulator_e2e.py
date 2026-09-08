from __future__ import annotations

from pathlib import Path

from plc_acceptance.resources import default_kit_root
from plc_acceptance.simulator import run_simulator_acceptance

KIT_ROOT = default_kit_root()

FULL_DEVICE_CASE_IDS = {
    "DEV-PLC-001",
    "DEV-S1-001",
    "DEV-S04-001",
    "DEV-S05-001",
    "DEV-S06-001",
    "DEV-S07-001",
    "DEV-S08-001",
    "DEV-S09-001",
    "HS-A-001",
}


def test_szlab_acceptance_runs_through_real_cross_process_opcua(tmp_path: Path) -> None:
    """真实启动 PLC-Sim 双进程并经 OPC UA 验证代表性机器人和参数闭环。

    参数：``tmp_path`` 是跨进程报告和日志的隔离目录。
    返回：无；断言门禁、证据级别、代表用例、时间线和 JUnit 报告。
    """

    result, report_dir = run_simulator_acceptance(
        KIT_ROOT,
        output_root=tmp_path,
        selected_case_ids={"HS-A-001", "HS-C-001"},
    )

    assert result.status == "BLOCKED"
    assert result.evidence_level == "L1 协议仿真证据"
    assert {case.case_id for case in result.cases} >= {
        "CT-001",
        "CT-002",
        "HS-A-001",
        "HS-C-001",
    }
    selected_results = [
        case for case in result.cases if case.case_id in {"HS-A-001", "HS-C-001"}
    ]
    assert {case.status for case in selected_results} == {"PASSED"}
    assert next(case for case in result.cases if case.case_id == "MANIFEST").status == (
        "BLOCKED"
    )
    assert result.timeline
    assert (report_dir / "junit.xml").is_file()


def test_full_szlab_device_matrix_runs_through_public_process_endpoints(
    tmp_path: Path,
) -> None:
    """九个设备必须经真实跨进程 OPC UA/HTTP 接缝形成可追溯 L1 证据。

    参数：``tmp_path`` 是本次完整设备矩阵报告的隔离目录。
    返回：无；断言全部设备用例通过且同时留下 OPC UA 与 HTTP 时间线。
    """

    result, report_dir = run_simulator_acceptance(
        KIT_ROOT,
        output_root=tmp_path,
    )

    device_results = {
        case.case_id: case
        for case in result.cases
        if case.case_id in FULL_DEVICE_CASE_IDS
    }
    assert result.status == "PASSED"
    assert set(device_results) == FULL_DEVICE_CASE_IDS
    assert {case.status for case in device_results.values()} == {"PASSED"}
    assert {event.operation for event in result.timeline} >= {
        "connect",
        "read",
        "write",
        "http_request",
    }
    assert (report_dir / "timeline.jsonl").is_file()
