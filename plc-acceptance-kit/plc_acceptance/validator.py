"""执行协议、点表、变量所有权和用例引用的 L0 静态检查。"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from typing import Any

from .catalog import load_catalog
from .models import AcceptanceBundle, Finding


def _iter_steps(case: Any) -> Iterable[dict[str, Any]]:
    """遍历一个用例的全部前置、执行和清理步骤。

    参数：``case`` 是 ``CaseSpec``。
    返回：按声明顺序产生步骤的迭代器。
    """

    yield from case.given
    yield from case.steps
    yield from case.cleanup


def validate_bundle(bundle: AcceptanceBundle) -> list[Finding]:
    """验证验收包内部一致性和 SZLab 点表映射。

    参数：``bundle`` 是完整配置包。
    返回：带 CT-001/CT-002 归属的发现列表；空列表表示 L0 通过。
    """

    findings: list[Finding] = []
    if not bundle.csv_path.is_file():
        return [Finding("CT-001", "error", f"点表不存在: {bundle.csv_path}")]

    try:
        catalog = load_catalog(bundle.csv_path, node_id_prefix=bundle.node_id_prefix)
    except Exception as exc:  # noqa: BLE001 - L0 必须把点表解析错误转成门禁证据
        return [
            Finding("CT-001", "error", f"点表解析失败: {type(exc).__name__}: {exc}")
        ]

    if len(catalog) != bundle.expected_scalar_nodes:
        findings.append(
            Finding(
                "CT-001",
                "error",
                f"标量节点数量不一致: 期望 {bundle.expected_scalar_nodes}，实际 {len(catalog)}",
            )
        )

    names = [contract.name for contract in bundle.nodes.values()]
    for name, count in Counter(names).items():
        if count > 1:
            findings.append(Finding("CT-001", "error", f"协议重复声明变量名: {name}"))

    for contract in bundle.nodes.values():
        actual = catalog.get(contract.name)
        if actual is None:
            severity = "error" if contract.required else "warning"
            findings.append(
                Finding("CT-001", severity, f"点表缺少变量: {contract.name}")
            )
            continue
        if actual.data_type != contract.data_type:
            findings.append(
                Finding(
                    "CT-001",
                    "error",
                    f"变量类型不一致: {contract.name} 期望 {contract.data_type}，实际 {actual.data_type}",
                )
            )

    manifest_ids = [entry.case_id for entry in bundle.manifest]
    for case_id, count in Counter(manifest_ids).items():
        if count > 1:
            findings.append(Finding("CT-001", "error", f"测试清单重复用例: {case_id}"))

    executable_ids = set(bundle.cases)
    static_ids = {"CT-001", "CT-002"}
    for entry in bundle.manifest:
        if entry.case_id not in executable_ids | static_ids:
            findings.append(
                Finding("CT-001", "error", f"测试清单引用未知用例: {entry.case_id}")
            )

    for case in bundle.cases.values():
        for step in _iter_steps(case):
            logical_id = str(step.get("node", ""))
            if not logical_id:
                continue
            contract = bundle.nodes.get(logical_id)
            if contract is None:
                findings.append(
                    Finding(
                        "CT-001",
                        "error",
                        f"用例 {case.case_id} 引用未知逻辑变量: {logical_id}",
                    )
                )
                continue
            if step.get("action") == "write" and contract.owner != "host":
                findings.append(
                    Finding(
                        "CT-002",
                        "error",
                        f"用例 {case.case_id} 试图写入 PLC 所有变量: {logical_id} ({contract.name})",
                    )
                )

    for case_id in bundle.environment.case_repeat_overrides:
        case = bundle.cases.get(case_id)
        if case is None:
            findings.append(
                Finding("CT-001", "error", f"环境重复次数引用未知用例: {case_id}")
            )
        elif bundle.environment.kind not in case.environments:
            findings.append(
                Finding(
                    "CT-001",
                    "error",
                    f"环境 {bundle.environment.kind} 为不允许的用例 {case_id} 配置重复次数",
                )
            )

    for requirement in bundle.coverage:
        if not all(key in requirement for key in ("requirement", "status", "evidence")):
            findings.append(
                Finding("CT-001", "error", f"规范覆盖记录字段不完整: {requirement}")
            )

    return findings
