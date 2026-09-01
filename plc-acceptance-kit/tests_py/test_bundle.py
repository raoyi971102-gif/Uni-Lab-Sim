from __future__ import annotations

from pathlib import Path

from plc_acceptance.catalog import catalog_fingerprint, load_catalog
from plc_acceptance.config import load_bundle
from plc_acceptance.runner import run_acceptance
from plc_acceptance.validator import validate_bundle

KIT_ROOT = Path(__file__).resolve().parents[1]


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
