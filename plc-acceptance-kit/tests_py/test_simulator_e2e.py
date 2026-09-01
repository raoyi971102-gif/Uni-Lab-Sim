from __future__ import annotations

from pathlib import Path

from plc_acceptance.simulator import run_simulator_acceptance

KIT_ROOT = Path(__file__).resolve().parents[1]


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
    assert result.evidence_level == "simulator evidence"
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
