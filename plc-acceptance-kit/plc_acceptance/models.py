"""验收配置、运行结果和 OPC UA 时间线的数据模型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

Owner = Literal["host", "plc"]
ResultStatus = Literal["PASSED", "FAILED", "BLOCKED", "ABORTED"]


@dataclass(frozen=True)
class NodeContract:
    """描述一个逻辑变量及其 PLC 通讯契约。

    字段：``logical_id`` 是测试稳定引用，``name`` 是点表中文变量名，
    ``data_type`` 是规范化类型，``owner`` 是唯一写入方。
    """

    logical_id: str
    name: str
    data_type: str
    owner: Owner
    required: bool = True
    description: str = ""


@dataclass(frozen=True)
class CatalogNode:
    """保存从项目点表发现的节点定义。"""

    name: str
    data_type: str
    node_id: str


@dataclass(frozen=True)
class CaseSpec:
    """保存一条可执行验收用例。

    ``given`` 是前置断言，``steps`` 是正式刺激与断言，``cleanup`` 是无论
    成败都执行的主机侧复位步骤；``repeat`` 表示完整用例重复次数。
    """

    case_id: str
    name: str
    level: str
    safety_level: str
    environments: tuple[str, ...]
    physical_effect: bool
    repeat: int
    given: tuple[dict[str, Any], ...]
    steps: tuple[dict[str, Any], ...]
    cleanup: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class EnvironmentSpec:
    """描述一次验收运行的环境、证据边界、安全门禁和 OPC UA 端点。"""

    environment_id: str
    kind: str
    endpoint: str
    connect_timeout_ms: int
    poll_interval_ms: int
    enforce_access_level: bool
    allow_physical_actions: bool
    evidence_level: str
    scope_statement: str
    required_evidence_fields: tuple[str, ...]
    case_repeat_overrides: dict[str, int]


@dataclass(frozen=True)
class ManifestEntry:
    """描述测试清单中的门禁用例。"""

    case_id: str
    required: bool
    safety_level: str


@dataclass(frozen=True)
class AcceptanceBundle:
    """聚合一次运行所需的版本化配置和解析后对象。"""

    root: Path
    protocol_path: Path
    mapping_path: Path
    manifest_path: Path
    coverage_path: Path
    environment_path: Path
    csv_path: Path
    namespace_uri: str
    node_id_prefix: str
    protocol_version: str
    project_id: str
    expected_scalar_nodes: int
    nodes: dict[str, NodeContract]
    cases: dict[str, CaseSpec]
    manifest: tuple[ManifestEntry, ...]
    coverage: tuple[dict[str, Any], ...]
    environment: EnvironmentSpec


@dataclass(frozen=True)
class Finding:
    """表示静态检查发现的一条可定位问题。"""

    case_id: str
    severity: Literal["error", "warning", "info"]
    message: str


@dataclass(frozen=True)
class TimelineEvent:
    """保存一次带时间戳的 OPC UA 读、写或连接证据。"""

    timestamp: str
    elapsed_ms: float
    operation: str
    logical_id: str
    node_name: str
    node_id: str
    value: Any
    detail: str = ""


@dataclass
class CaseResult:
    """保存单条验收用例的最终状态和诊断。"""

    case_id: str
    name: str
    safety_level: str
    status: ResultStatus
    started_at: str
    ended_at: str
    duration_ms: float
    message: str = ""
    iteration: int = 1


@dataclass
class RunResult:
    """保存整次验收运行的门禁结论、版本证据和时间线。"""

    run_id: str
    project_id: str
    protocol_version: str
    environment_id: str
    evidence_level: str
    status: ResultStatus
    started_at: str
    ended_at: str
    cases: list[CaseResult] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    timeline: list[TimelineEvent] = field(default_factory=list)
    fingerprints: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
