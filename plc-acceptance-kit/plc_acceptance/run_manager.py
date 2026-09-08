"""管理 GUI 发起的单实例验收运行和持久报告。"""

from __future__ import annotations

import json
import threading
import time
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import load_bundle
from .reporting import write_reports
from .resources import default_kit_root, reports_dir
from .runner import run_acceptance
from .simulator import run_simulator_acceptance

MODE_ENVIRONMENTS = {
    "soft_plc": "soft-plc",
    "bench": "bench",
    "fat_sat": "fat-sat",
}
MODE_START_MESSAGES = {
    "simulator": "正在启动内置 PLC-Sim 与 SZLab 握手代理",
    "soft_plc": "正在连接供应商软 PLC 并执行 L2 完整门禁",
    "bench": "正在连接真 PLC 与台架机构并执行 L3 自动清单",
    "fat_sat": "正在连接现场真机并执行 L4 FAT/SAT 自动清单",
}


def _utc_now() -> str:
    """返回 GUI 状态使用的 UTC ISO-8601 时间戳。

    参数：无。
    返回：带时区的 UTC 时间文本。
    """

    return datetime.now(timezone.utc).isoformat()


def _case_summary(cases: list[dict[str, Any]]) -> dict[str, int]:
    """统计报告中各用例状态的数量。

    参数：``cases`` 是报告中的用例结果列表。
    返回：按状态索引的数量映射。
    """

    return dict(Counter(str(case.get("status", "UNKNOWN")) for case in cases))


def _report_summary(payload: dict[str, Any]) -> dict[str, Any]:
    """把完整报告压缩为 GUI 可轮询的摘要。

    参数：``payload`` 是 ``run.json`` 或运行结果映射。
    返回：不包含大体积时间线的报告摘要。
    """

    cases = [dict(item) for item in payload.get("cases", [])]
    return {
        "run_id": payload.get("run_id"),
        "status": payload.get("status"),
        "project_id": payload.get("project_id"),
        "protocol_version": payload.get("protocol_version"),
        "environment_id": payload.get("environment_id"),
        "evidence_level": payload.get("evidence_level"),
        "started_at": payload.get("started_at"),
        "ended_at": payload.get("ended_at"),
        "case_summary": _case_summary(cases),
        "cases": cases,
        "findings": payload.get("findings", []),
        "fingerprints": payload.get("fingerprints", {}),
        "metadata": payload.get("metadata", {}),
    }


class AcceptanceRunManager:
    """串行执行 GUI 验收任务，并让并发查询读取稳定快照。"""

    def __init__(
        self, *, kit_root: Path | None = None, output_root: Path | None = None
    ):
        """创建运行管理器。

        参数：``kit_root`` 是验收配置根目录；``output_root`` 是报告根目录。
        返回：无；初始化当前对象。
        """

        self.kit_root = (kit_root or default_kit_root()).resolve()
        self.output_root = (output_root or reports_dir()).resolve()
        self.output_root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._state: dict[str, Any] = {
            "request_id": None,
            "state": "IDLE",
            "message": "准备就绪",
            "started_at": None,
            "ended_at": None,
            "mode": None,
            "report": None,
            "error": None,
        }

    def snapshot(self) -> dict[str, Any]:
        """返回当前运行状态的线程安全副本。

        参数：无。
        返回：可直接序列化给 GUI 的状态映射。
        """

        with self._lock:
            state = json.loads(json.dumps(self._state, ensure_ascii=False, default=str))
        if state["state"] == "RUNNING" and state["started_at"]:
            started = datetime.fromisoformat(state["started_at"])
            state["elapsed_seconds"] = max(
                0.0,
                (datetime.now(timezone.utc) - started).total_seconds(),
            )
        else:
            state["elapsed_seconds"] = None
        return state

    def start(
        self,
        *,
        mode: str,
        endpoint: str | None,
        namespace_uri: str | None,
        confirm_safe_test_mode: bool,
        plc_artifact: Path | None,
        evidence_metadata: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """在后台启动一次完整门禁运行。

        参数：``mode`` 是 L1-L4 环境；``endpoint`` 和 ``namespace_uri`` 是外部
        PLC 地址身份；
        ``confirm_safe_test_mode`` 表示人工确认安全前置；``plc_artifact`` 是候选包；
        ``evidence_metadata`` 保存现场、监护人和物料证据。
        返回：刚进入 ``RUNNING`` 的状态快照。
        异常：已有运行或模式非法时抛出 ``RuntimeError`` 或 ``ValueError``。
        """

        if mode not in {"simulator", *MODE_ENVIRONMENTS}:
            raise ValueError(f"不支持的验收模式: {mode}")
        with self._lock:
            if self._state["state"] == "RUNNING":
                raise RuntimeError("已有验收正在运行，请等待当前任务结束")
            request_id = f"gui-{int(time.time())}-{uuid.uuid4().hex[:6]}"
            self._state = {
                "request_id": request_id,
                "state": "RUNNING",
                "message": MODE_START_MESSAGES[mode],
                "started_at": _utc_now(),
                "ended_at": None,
                "mode": mode,
                "report": None,
                "error": None,
            }
        worker = threading.Thread(
            target=self._run,
            kwargs={
                "mode": mode,
                "endpoint": endpoint,
                "namespace_uri": namespace_uri,
                "confirm_safe_test_mode": confirm_safe_test_mode,
                "plc_artifact": plc_artifact,
                "evidence_metadata": dict(evidence_metadata or {}),
            },
            name=f"plc-acceptance-{request_id}",
            daemon=True,
        )
        worker.start()
        return self.snapshot()

    def _run(
        self,
        *,
        mode: str,
        endpoint: str | None,
        namespace_uri: str | None,
        confirm_safe_test_mode: bool,
        plc_artifact: Path | None,
        evidence_metadata: dict[str, str],
    ) -> None:
        """执行后台验收并原子发布最终状态。

        参数：与 ``start`` 相同，均为本次冻结的运行输入。
        返回：无；最终状态写入管理器。
        """

        try:
            if mode == "simulator":
                result, report_dir = run_simulator_acceptance(
                    self.kit_root,
                    output_root=self.output_root,
                )
            else:
                bundle = load_bundle(
                    self.kit_root,
                    environment_name=MODE_ENVIRONMENTS[mode],
                    endpoint_override=endpoint,
                    namespace_uri_override=namespace_uri,
                )
                result = run_acceptance(
                    bundle,
                    confirm_safe_test_mode=confirm_safe_test_mode,
                    plc_artifact=str(plc_artifact) if plc_artifact else None,
                    evidence_metadata=evidence_metadata,
                )
                report_dir = write_reports(result, self.output_root)
            payload = json.loads((report_dir / "run.json").read_text(encoding="utf-8"))
            final_state = {
                "state": result.status,
                "message": {
                    "PASSED": "全部必跑用例通过",
                    "FAILED": "存在已执行但未通过的门禁用例",
                    "BLOCKED": "环境或安全前置不足，未形成通过结论",
                    "ABORTED": "验收被外部保护或人工操作中止",
                }[result.status],
                "ended_at": _utc_now(),
                "report": _report_summary(payload),
                "error": None,
            }
            if mode in {"bench", "fat_sat"} and result.status == "PASSED":
                final_state["message"] = (
                    "当前自动清单通过；人工与阻塞覆盖项仍须现场关闭"
                )
        except Exception as exc:  # noqa: BLE001 - GUI 必须把运行异常转成可恢复状态
            final_state = {
                "state": "FAILED",
                "message": "验收运行器发生错误，请检查诊断信息后重试",
                "ended_at": _utc_now(),
                "report": None,
                "error": f"{type(exc).__name__}: {exc}",
            }
        with self._lock:
            self._state.update(final_state)

    def history(self, limit: int = 20) -> list[dict[str, Any]]:
        """读取最近的持久报告摘要。

        参数：``limit`` 是最多返回的报告数量。
        返回：按开始时间倒序排列的报告摘要。
        """

        summaries: list[dict[str, Any]] = []
        for report_path in sorted(
            self.output_root.glob("*/run.json"),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        ):
            try:
                payload = json.loads(report_path.read_text(encoding="utf-8"))
                summaries.append(_report_summary(payload))
            except (OSError, ValueError, TypeError):
                continue
            if len(summaries) >= limit:
                break
        return summaries

    def report_dir(self, run_id: str) -> Path:
        """安全解析一个已生成报告目录。

        参数：``run_id`` 是报告中的稳定运行 ID。
        返回：位于报告根目录内的现有目录。
        异常：ID 含路径字符或报告不存在时抛出 ``FileNotFoundError``。
        """

        if not run_id.startswith("plc-") or Path(run_id).name != run_id:
            raise FileNotFoundError(run_id)
        candidate = (self.output_root / run_id).resolve()
        if (
            candidate.parent != self.output_root
            or not (candidate / "run.json").is_file()
        ):
            raise FileNotFoundError(run_id)
        return candidate
