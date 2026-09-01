"""执行静态门禁和配置驱动的 OPC UA 验收用例。"""

from __future__ import annotations

import time
import uuid
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .catalog import catalog_fingerprint, load_catalog
from .models import AcceptanceBundle, CaseResult, Finding, RunResult
from .opcua_session import OpcUaSession
from .reporting import config_fingerprints
from .validator import validate_bundle


def _utc_now() -> str:
    """返回 UTC ISO-8601 时间戳。"""

    return datetime.now(timezone.utc).isoformat()


def _execute_step(session: OpcUaSession, step: dict[str, Any]) -> None:
    """执行一条声明式 OPC UA 测试步骤。

    参数：``session`` 是已连接会话，``step`` 是包含 action 的配置映射。
    返回：无；非法动作或断言失败直接抛出异常。
    """

    action = str(step["action"])
    if action == "write":
        session.write(str(step["node"]), step.get("value"))
        return
    if action == "assert":
        session.assert_equal(str(step["node"]), step.get("equals"))
        return
    if action == "wait":
        session.wait_equal(
            str(step["node"]),
            step.get("equals"),
            int(step.get("timeout_ms", 5000)),
        )
        return
    if action == "sleep":
        time.sleep(int(step["duration_ms"]) / 1000)
        return
    raise ValueError(f"不支持的测试步骤 action={action}")


def _execute_steps(session: OpcUaSession, steps: Iterable[dict[str, Any]]) -> None:
    """依次执行一组测试步骤。

    参数：``session`` 是 OPC UA 会话，``steps`` 是步骤序列。
    返回：无。
    """

    for step in steps:
        _execute_step(session, step)


def _static_results(
    bundle: AcceptanceBundle, findings: list[Finding]
) -> list[CaseResult]:
    """把 L0 发现转换为 CT-001/CT-002 门禁结果。

    参数：``bundle`` 提供清单名称，``findings`` 是静态检查输出。
    返回：两条标准 ``CaseResult``。
    """

    results: list[CaseResult] = []
    names = {
        "CT-001": "节点、类型与项目点表一致性",
        "CT-002": "变量写入所有权与测试脚本边界",
    }
    for case_id in ("CT-001", "CT-002"):
        case_findings = [
            item
            for item in findings
            if item.case_id == case_id and item.severity == "error"
        ]
        now = _utc_now()
        results.append(
            CaseResult(
                case_id=case_id,
                name=names[case_id],
                safety_level="P0",
                status="FAILED" if case_findings else "PASSED",
                started_at=now,
                ended_at=now,
                duration_ms=0.0,
                message="; ".join(item.message for item in case_findings),
            )
        )
    return results


def run_acceptance(
    bundle: AcceptanceBundle,
    *,
    confirm_safe_test_mode: bool = False,
    selected_case_ids: set[str] | None = None,
    plc_artifact: str | None = None,
) -> RunResult:
    """执行一次完整的 L0/L1-L4 验收运行。

    参数：``bundle`` 是版本化配置；``confirm_safe_test_mode`` 是真实运动人工确认；
    ``selected_case_ids`` 可缩小诊断范围；``plc_artifact`` 是候选包路径。
    返回：包含门禁状态、用例结果、时间线与指纹的 ``RunResult``。
    """

    started_at = _utc_now()
    run_id = f"plc-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"
    findings = validate_bundle(bundle)
    results = _static_results(bundle, findings)
    catalog = load_catalog(bundle.csv_path, node_id_prefix=bundle.node_id_prefix)
    artifact_error = ""
    artifact_path: Path | None = None
    if plc_artifact:
        artifact_path = Path(plc_artifact).resolve()
        if not artifact_path.is_file():
            artifact_error = f"PLC 候选包不存在或不是文件: {artifact_path}"
    elif bundle.environment.kind != "simulator":
        artifact_error = "非仿真验收必须通过 --plc-artifact 绑定不可变 PLC 候选包"
    fingerprints = config_fingerprints(
        bundle,
        plc_artifact=str(artifact_path)
        if artifact_path and not artifact_error
        else None,
    )
    fingerprints["node_catalog"] = catalog_fingerprint(catalog.values())
    static_failed = any(result.status == "FAILED" for result in results)
    session: OpcUaSession | None = None

    if artifact_error:
        now = _utc_now()
        findings.append(Finding("PREFLIGHT", "error", artifact_error))
        results.append(
            CaseResult(
                case_id="PREFLIGHT",
                name="PLC 候选版本绑定",
                safety_level="P0",
                status="BLOCKED",
                started_at=now,
                ended_at=now,
                duration_ms=0.0,
                message=artifact_error,
            )
        )

    if not static_failed and not artifact_error:
        session = OpcUaSession(
            bundle.environment.endpoint,
            bundle.nodes,
            catalog,
            namespace_uri=bundle.namespace_uri,
            timeout_seconds=bundle.environment.connect_timeout_ms / 1000,
            poll_interval_seconds=bundle.environment.poll_interval_ms / 1000,
        )
        try:
            session.connect()
            access_errors: list[str] = []
            for logical_id in bundle.nodes:
                access_errors.extend(
                    session.check_access(
                        logical_id,
                        enforce_write_owner=bundle.environment.enforce_access_level,
                    )
                )
            if access_errors:
                findings.extend(
                    Finding("CT-001", "error", message) for message in access_errors
                )
                results[0].status = "FAILED"
                results[0].message = "; ".join(access_errors)
            else:
                manifest_ids = [
                    entry.case_id
                    for entry in bundle.manifest
                    if entry.case_id not in {"CT-001", "CT-002"}
                ]
                for case_id in manifest_ids:
                    if (
                        selected_case_ids is not None
                        and case_id not in selected_case_ids
                    ):
                        continue
                    case = bundle.cases[case_id]
                    if bundle.environment.kind not in case.environments:
                        now = _utc_now()
                        results.append(
                            CaseResult(
                                case_id=case.case_id,
                                name=case.name,
                                safety_level=case.safety_level,
                                status="BLOCKED",
                                started_at=now,
                                ended_at=now,
                                duration_ms=0.0,
                                message=f"环境 {bundle.environment.kind} 不在用例允许范围",
                            )
                        )
                        continue
                    safety_confirmed = (
                        bundle.environment.allow_physical_actions
                        or confirm_safe_test_mode
                    )
                    if case.physical_effect and not safety_confirmed:
                        now = _utc_now()
                        results.append(
                            CaseResult(
                                case_id=case.case_id,
                                name=case.name,
                                safety_level=case.safety_level,
                                status="BLOCKED",
                                started_at=now,
                                ended_at=now,
                                duration_ms=0.0,
                                message="未确认受控测试模式，禁止派发可能产生物理效果的动作",
                            )
                        )
                        continue
                    for iteration in range(1, case.repeat + 1):
                        case_started = _utc_now()
                        monotonic_started = time.monotonic()
                        status = "PASSED"
                        message = ""
                        try:
                            _execute_steps(session, case.given)
                            _execute_steps(session, case.steps)
                        except Exception as exc:  # noqa: BLE001 - 用例异常必须成为标准失败结果
                            status = "FAILED"
                            message = f"{type(exc).__name__}: {exc}"
                        finally:
                            try:
                                _execute_steps(session, case.cleanup)
                            except Exception as cleanup_exc:  # noqa: BLE001 - 清理失败提升为门禁失败
                                status = "FAILED"
                                cleanup_message = f"清理失败 {type(cleanup_exc).__name__}: {cleanup_exc}"
                                message = (
                                    f"{message}; {cleanup_message}"
                                    if message
                                    else cleanup_message
                                )
                        results.append(
                            CaseResult(
                                case_id=case.case_id,
                                name=case.name,
                                safety_level=case.safety_level,
                                status=status,  # type: ignore[arg-type]
                                started_at=case_started,
                                ended_at=_utc_now(),
                                duration_ms=(time.monotonic() - monotonic_started)
                                * 1000,
                                message=message,
                                iteration=iteration,
                            )
                        )
                        if status != "PASSED":
                            break
        except Exception as exc:  # noqa: BLE001 - 连接或预检失败属于 BLOCKED 证据
            now = _utc_now()
            results.append(
                CaseResult(
                    case_id="PREFLIGHT",
                    name="OPC UA 连接与运行前检查",
                    safety_level="P0",
                    status="BLOCKED",
                    started_at=now,
                    ended_at=now,
                    duration_ms=0.0,
                    message=f"{type(exc).__name__}: {exc}",
                )
            )
        finally:
            if session is not None:
                try:
                    session.disconnect()
                except Exception as exc:  # noqa: BLE001 - 断开失败写入发现但保留主要结果
                    findings.append(
                        Finding("PREFLIGHT", "warning", f"断开 OPC UA 失败: {exc}")
                    )

    required_ids = {entry.case_id for entry in bundle.manifest if entry.required}
    executed_ids = {result.case_id for result in results}
    missing_required_ids = required_ids - executed_ids
    if missing_required_ids:
        now = _utc_now()
        results.append(
            CaseResult(
                case_id="MANIFEST",
                name="必跑用例清单完整性",
                safety_level="P0",
                status="BLOCKED",
                started_at=now,
                ended_at=now,
                duration_ms=0.0,
                message="诊断筛选未执行必跑用例: "
                + ", ".join(sorted(missing_required_ids)),
            )
        )
    gate_results = [
        result
        for result in results
        if result.case_id in required_ids or result.case_id in {"PREFLIGHT", "MANIFEST"}
    ]
    if any(result.status == "FAILED" for result in gate_results):
        overall_status = "FAILED"
    elif any(result.status in {"BLOCKED", "ABORTED"} for result in gate_results):
        overall_status = "BLOCKED"
    else:
        overall_status = "PASSED"

    return RunResult(
        run_id=run_id,
        project_id=bundle.project_id,
        protocol_version=bundle.protocol_version,
        environment_id=bundle.environment.environment_id,
        evidence_level=f"{bundle.environment.kind} evidence",
        status=overall_status,  # type: ignore[arg-type]
        started_at=started_at,
        ended_at=_utc_now(),
        cases=results,
        findings=findings,
        timeline=list(session.timeline if session is not None else []),
        fingerprints=fingerprints,
        metadata={
            "endpoint": bundle.environment.endpoint,
            "required_case_ids": sorted(required_ids),
            "selected_case_ids": sorted(selected_case_ids)
            if selected_case_ids
            else None,
            "requirements_coverage": list(bundle.coverage),
        },
    )
