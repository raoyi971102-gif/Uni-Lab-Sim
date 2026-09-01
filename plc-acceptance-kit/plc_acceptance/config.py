"""加载自动化验收包中的协议、映射、环境和测试清单。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .models import (
    AcceptanceBundle,
    CaseSpec,
    EnvironmentSpec,
    ManifestEntry,
    NodeContract,
)


def _read_yaml(path: Path) -> dict[str, Any]:
    """读取一个 YAML 映射。

    参数：``path`` 是配置文件路径。
    返回：解析后的顶层映射。
    异常：文件不是映射时抛出 ``ValueError``。
    """

    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        raise TypeError(f"YAML 顶层必须是映射: {path}")
    return payload


def _resolve_from(config_path: Path, value: str) -> Path:
    """把配置内相对路径解析为绝对路径。

    参数：``config_path`` 是声明路径的 YAML，``value`` 是相对或绝对路径。
    返回：规范化后的绝对路径。
    """

    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = config_path.parent / candidate
    return candidate.resolve()


def _load_cases(root: Path, patterns: list[str]) -> dict[str, CaseSpec]:
    """按清单路径加载可执行用例。

    参数：``root`` 是验收包根目录，``patterns`` 是相对 glob 列表。
    返回：以稳定用例 ID 为键的用例映射。
    异常：重复 ID 或非法重复次数会抛出 ``ValueError``。
    """

    cases: dict[str, CaseSpec] = {}
    for pattern in patterns:
        for case_path in sorted(root.glob(pattern)):
            payload = _read_yaml(case_path)
            for raw_case in payload.get("cases", []):
                case_id = str(raw_case["id"])
                if case_id in cases:
                    raise ValueError(f"重复用例 ID: {case_id}")
                repeat = int(raw_case.get("repeat", 1))
                if repeat < 1:
                    raise ValueError(f"用例 {case_id} 的 repeat 必须大于 0")
                cases[case_id] = CaseSpec(
                    case_id=case_id,
                    name=str(raw_case["name"]),
                    level=str(raw_case["level"]),
                    safety_level=str(raw_case["safety_level"]),
                    environments=tuple(
                        str(item) for item in raw_case.get("environments", [])
                    ),
                    physical_effect=bool(raw_case.get("physical_effect", False)),
                    repeat=repeat,
                    given=tuple(dict(item) for item in raw_case.get("given", [])),
                    steps=tuple(dict(item) for item in raw_case.get("when", [])),
                    cleanup=tuple(dict(item) for item in raw_case.get("cleanup", [])),
                )
    return cases


def load_bundle(
    root: str | Path,
    *,
    environment_name: str = "szlab-simulator",
    endpoint_override: str | None = None,
) -> AcceptanceBundle:
    """加载一个完整的 SZLab PLC 验收配置包。

    参数：``root`` 是验收包目录；``environment_name`` 是环境配置名；
    ``endpoint_override`` 可为本次运行覆盖 OPC UA Endpoint。
    返回：已解析且路径固定的 ``AcceptanceBundle``。
    """

    bundle_root = Path(root).resolve()
    protocol_path = bundle_root / "protocol" / "plc-interface.yaml"
    mapping_path = bundle_root / "mappings" / "szlab.yaml"
    manifest_path = bundle_root / "protocol" / "test-manifest.yaml"
    coverage_path = bundle_root / "protocol" / "requirements-coverage.yaml"
    environment_path = bundle_root / "environments" / f"{environment_name}.yaml"

    protocol = _read_yaml(protocol_path)
    mapping = _read_yaml(mapping_path)
    manifest_payload = _read_yaml(manifest_path)
    coverage_payload = _read_yaml(coverage_path)
    environment_payload = _read_yaml(environment_path)

    node_contracts: dict[str, NodeContract] = {}
    for raw_node in protocol.get("nodes", []):
        logical_id = str(raw_node["id"])
        if logical_id in node_contracts:
            raise ValueError(f"重复逻辑变量 ID: {logical_id}")
        node_contracts[logical_id] = NodeContract(
            logical_id=logical_id,
            name=str(raw_node["name"]),
            data_type=str(raw_node["data_type"]).upper(),
            owner=str(raw_node["owner"]),  # type: ignore[arg-type]
            required=bool(raw_node.get("required", True)),
            description=str(raw_node.get("description", "")),
        )

    manifest_entries = tuple(
        ManifestEntry(
            case_id=str(item["id"]),
            required=bool(item.get("required", True)),
            safety_level=str(item["safety_level"]),
        )
        for item in manifest_payload.get("cases", [])
    )
    case_patterns = [str(item) for item in manifest_payload.get("case_files", [])]
    cases = _load_cases(bundle_root, case_patterns)

    endpoint = endpoint_override or str(environment_payload["endpoint"])
    environment = EnvironmentSpec(
        environment_id=str(environment_payload["id"]),
        kind=str(environment_payload["kind"]),
        endpoint=endpoint,
        connect_timeout_ms=int(environment_payload.get("connect_timeout_ms", 5000)),
        poll_interval_ms=int(environment_payload.get("poll_interval_ms", 20)),
        enforce_access_level=bool(
            environment_payload.get("enforce_access_level", True)
        ),
        allow_physical_actions=bool(
            environment_payload.get("allow_physical_actions", False)
        ),
    )

    return AcceptanceBundle(
        root=bundle_root,
        protocol_path=protocol_path,
        mapping_path=mapping_path,
        manifest_path=manifest_path,
        coverage_path=coverage_path,
        environment_path=environment_path,
        csv_path=_resolve_from(mapping_path, str(mapping["csv_path"])),
        namespace_uri=str(mapping["namespace_uri"]),
        node_id_prefix=str(mapping["node_id_prefix"]),
        protocol_version=str(protocol["protocol_version"]),
        project_id=str(protocol["project_id"]),
        expected_scalar_nodes=int(mapping["expected_scalar_nodes"]),
        nodes=node_contracts,
        cases=cases,
        manifest=manifest_entries,
        coverage=tuple(dict(item) for item in coverage_payload.get("requirements", [])),
        environment=environment,
    )
