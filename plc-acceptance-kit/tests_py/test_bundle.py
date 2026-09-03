from __future__ import annotations

from plc_acceptance.catalog import catalog_fingerprint, load_catalog
from plc_acceptance.config import load_bundle
from plc_acceptance.resources import default_kit_root
from plc_acceptance.runner import run_acceptance
from plc_acceptance.validator import validate_bundle

KIT_ROOT = default_kit_root()


def test_szlab_bundle_resolves_the_authoritative_point_table() -> None:
    """验收包必须发现 PLC-Sim 自带的 0810 点表及全部 1,591 个标量节点。

    参数：无。
    返回：无；断言权威点表的数量、类型、NodeId 和指纹。
    """

    bundle = load_bundle(KIT_ROOT)
    catalog = load_catalog(bundle.csv_path, node_id_prefix=bundle.node_id_prefix)

    assert bundle.project_id == "szlab-poly-studio-plc"
    assert bundle.namespace_uri == "urn:xuse:sim"
    assert len(catalog) == bundle.expected_scalar_nodes == 1591
    assert catalog["Robot_任务完成"].data_type == "INT32"
    assert catalog["Robot_任务完成"].node_id == "ns=4;s=上位机通讯|Robot_任务完成"
    assert len(catalog_fingerprint(catalog.values())) == 64


def test_l0_bundle_validation_passes_without_a_plc_connection() -> None:
    """L0 检查应验证点表、清单和唯一写入方，且不需要连接 OPC UA。

    参数：无。
    返回：无；断言 L0 没有发现问题。
    """

    bundle = load_bundle(KIT_ROOT)

    assert validate_bundle(bundle) == []


def test_requirements_coverage_keeps_unobservable_safety_gaps_explicit() -> None:
    """当前点表不可观察的故障、心跳和初始化不得被伪造成自动通过。

    参数：无。
    返回：无；断言相关规范覆盖状态保持为阻塞。
    """

    bundle = load_bundle(KIT_ROOT)
    coverage = {item["requirement"]: item["status"] for item in bundle.coverage}

    assert coverage["R6"] == "blocked"
    assert coverage["R12"] == "blocked"
    assert coverage["HS-D-001"] == "blocked"


def test_non_simulator_run_requires_an_immutable_plc_artifact() -> None:
    """软 PLC 及更高等级的报告不得脱离待测 PLC 候选包。

    参数：无。
    返回：无；断言缺少候选包时在连接前阻塞并保留诊断。
    """

    bundle = load_bundle(KIT_ROOT, environment_name="soft-plc")

    result = run_acceptance(bundle)

    assert result.status == "BLOCKED"
    preflight = next(case for case in result.cases if case.case_id == "PREFLIGHT")
    assert preflight.status == "BLOCKED"
    assert "--plc-artifact" in preflight.message


def test_real_plc_environments_define_l3_l4_evidence_and_safe_cycle_counts() -> None:
    """真机环境必须区分 L3/L4，并把连续动作缩减为现场明确的十轮。"""

    bench = load_bundle(KIT_ROOT, environment_name="bench")
    fat_sat = load_bundle(KIT_ROOT, environment_name="fat-sat")

    assert bench.environment.kind == "bench"
    assert bench.environment.evidence_level.startswith("L3")
    assert bench.environment.required_evidence_fields == (
        "supervisor",
        "test_location",
    )
    assert bench.environment.case_repeat_overrides["FL-003"] == 10
    assert fat_sat.environment.kind == "fat_sat"
    assert fat_sat.environment.evidence_level.startswith("L4")
    assert "material_reference" in fat_sat.environment.required_evidence_fields
    assert fat_sat.environment.case_repeat_overrides["FL-003"] == 10
    assert validate_bundle(bench) == []
    assert validate_bundle(fat_sat) == []

    overridden = load_bundle(
        KIT_ROOT,
        environment_name="bench",
        namespace_uri_override="urn:szlab:real-plc",
    )
    assert overridden.namespace_uri == "urn:szlab:real-plc"


def test_real_plc_run_blocks_before_connecting_without_site_evidence(
    tmp_path,
) -> None:
    """真机运行不得在缺少安全确认和现场证据时建立 OPC UA 会话。"""

    artifact = tmp_path / "candidate.zip"
    artifact.write_bytes(b"candidate")
    bundle = load_bundle(KIT_ROOT, environment_name="bench")

    result = run_acceptance(bundle, plc_artifact=str(artifact))

    assert result.status == "BLOCKED"
    preflight = next(case for case in result.cases if case.case_id == "PREFLIGHT")
    assert "受控测试模式" in preflight.message
    assert "supervisor" in preflight.message
    assert result.timeline == []
