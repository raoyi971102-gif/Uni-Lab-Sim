from __future__ import annotations

from pathlib import Path

from plc_acceptance.config import load_bundle
from plc_acceptance.models import CaseResult, RunResult
from plc_acceptance.reporting import config_fingerprints, sha256_tree, write_reports
from plc_acceptance.resources import default_kit_root

KIT_ROOT = default_kit_root()


def test_report_writer_emits_all_standard_evidence(tmp_path: Path) -> None:
    """报告器必须同时输出结构化结果、JUnit、HTML 和变量时间线。

    参数：``tmp_path`` 是本用例隔离的报告根目录。
    返回：无；断言四种标准证据文件存在。
    """

    result = RunResult(
        run_id="plc-test-run",
        project_id="szlab-poly-studio-plc",
        protocol_version="0.1.0",
        environment_id="unit-test",
        evidence_level="unit evidence",
        status="PASSED",
        started_at="2026-09-01T00:00:00+00:00",
        ended_at="2026-09-01T00:00:01+00:00",
        cases=[
            CaseResult(
                case_id="CT-001",
                name="点表检查",
                safety_level="P0",
                status="PASSED",
                started_at="2026-09-01T00:00:00+00:00",
                ended_at="2026-09-01T00:00:01+00:00",
                duration_ms=1.0,
            )
        ],
    )

    report_dir = write_reports(result, tmp_path)

    assert {path.name for path in report_dir.iterdir()} == {
        "run.json",
        "timeline.jsonl",
        "junit.xml",
        "report.html",
    }
    assert "PASSED" in (report_dir / "report.html").read_text(encoding="utf-8")


def test_tree_fingerprint_changes_with_case_content(tmp_path: Path) -> None:
    """测试包指纹必须同时绑定相对路径和用例内容。

    参数：``tmp_path`` 是用于构造两版用例的临时目录。
    返回：无；断言内容变化会改变测试包指纹。
    """

    case_path = tmp_path / "tests" / "common" / "case.yaml"
    case_path.parent.mkdir(parents=True)
    case_path.write_text("id: first\n", encoding="utf-8")
    first = sha256_tree(tmp_path, ("tests/**/*.yaml",))

    case_path.write_text("id: second\n", encoding="utf-8")
    second = sha256_tree(tmp_path, ("tests/**/*.yaml",))

    assert first != second


def test_fingerprints_bind_installed_acceptance_and_plc_sim_versions() -> None:
    """安装包报告必须记录验收运行器和 PLC-Sim 分发版本。

    参数：无。
    返回：无；断言版本身份与两个已安装分发包一致。
    """

    fingerprints = config_fingerprints(load_bundle(KIT_ROOT))

    assert fingerprints["acceptance_version"] == "0.2.0"
    assert fingerprints["plc_sim_version"] == "0.2.6"
