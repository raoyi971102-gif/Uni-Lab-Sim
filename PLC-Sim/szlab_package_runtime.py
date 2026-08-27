"""SZLab 设备包协议事件到通用仿真运行时的 Adapter。"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

try:
    from .common import load_yaml
    from .package_simulation import (
        BehaviorCoverage,
        PackageSimulationRuntime,
        SimulationClock,
        WorldState,
    )
except ImportError:  # Direct source-checkout imports.
    from common import load_yaml
    from package_simulation import (
        BehaviorCoverage,
        PackageSimulationRuntime,
        SimulationClock,
        WorldState,
    )

PACKAGE_ID = "community.szlab_poly_studio"
EXPECTED_REAL_DEVICE_COUNT = 9
EXPECTED_REAL_ACTION_COUNT = 105
EXPECTED_WORKFLOW_COUNT = 19


def default_package_config_path() -> Path:
    """返回随 PLC-SIM 发布的 SZLab 设备包会话配置。"""

    return Path(__file__).resolve().with_name("config") / "szlab_package.yaml"


def default_behavior_path() -> Path:
    """返回随 PLC-SIM 发布的 SZLab 行为覆盖快照。"""

    return Path(__file__).resolve().with_name("config") / "szlab_behavior.yaml"


def load_szlab_coverage(path: str | Path | None = None) -> BehaviorCoverage:
    """加载并校验真实设备动作覆盖快照。"""

    payload = load_yaml(str(path or default_behavior_path()))
    if payload.get("schema") != "unilab.szlab_behavior/v1":
        raise ValueError("SZLab 行为覆盖 schema 不受支持")
    if payload.get("package_id") != PACKAGE_ID:
        raise ValueError("SZLab 行为覆盖 package_id 不匹配")

    summary = dict(payload.get("catalog_summary", {}))
    expected = {
        "real_devices": EXPECTED_REAL_DEVICE_COUNT,
        "real_actions": EXPECTED_REAL_ACTION_COUNT,
        "workflows": EXPECTED_WORKFLOW_COUNT,
    }
    if summary != expected:
        raise ValueError(f"SZLab Catalog 摘要漂移: expected={expected!r}, actual={summary!r}")

    coverage = BehaviorCoverage()
    seen: set[str] = set()
    devices = dict(payload.get("devices", {}))
    if len(devices) != EXPECTED_REAL_DEVICE_COUNT:
        raise ValueError(f"SZLab 行为覆盖设备数应为 {EXPECTED_REAL_DEVICE_COUNT}")
    for device_id, groups in devices.items():
        for status, actions in dict(groups or {}).items():
            if status not in BehaviorCoverage.VALID:
                raise ValueError(f"{device_id} 使用未知覆盖状态: {status}")
            for action in actions or ():
                fq_action = f"{device_id}.{action}"
                if fq_action in seen:
                    raise ValueError(f"SZLab 动作覆盖重复: {fq_action}")
                seen.add(fq_action)
                coverage.register(fq_action, status)
    if len(seen) != EXPECTED_REAL_ACTION_COUNT:
        raise ValueError(
            f"SZLab 行为覆盖动作数应为 {EXPECTED_REAL_ACTION_COUNT}，实际 {len(seen)}"
        )
    return coverage


def _device_for_action(action: str) -> str:
    """从动作全限定名稳定解析设备身份。"""

    name = str(action)
    if "." not in name:
        return "szlab_poly_plc"
    return name.split(".", maxsplit=1)[0]


class SzlabPackageRuntime:
    """一次启动常驻全部 SZLab 协议的设备包会话状态面。"""

    def __init__(
        self,
        *,
        config_path: str | Path | None = None,
        behavior_path: str | Path | None = None,
        scenario: str | None = None,
        time_scale: float | None = None,
    ) -> None:
        config = load_yaml(str(config_path or default_package_config_path()))
        if config.get("schema") != "unilab.package_simulation/v1":
            raise ValueError("SZLab 设备包会话配置 schema 不受支持")
        if config.get("package_id") != PACKAGE_ID:
            raise ValueError("SZLab 设备包会话 package_id 不匹配")
        world_config = dict(config.get("world", {}))
        world = WorldState(
            sites=dict(world_config.get("sites", {})),
            quantities=dict(world_config.get("quantities", {})),
            devices=dict(world_config.get("devices", {})),
            flags=dict(world_config.get("flags", {})),
        )
        resolved_scenario = str(scenario or config.get("scenario", "ready"))
        world.set_flag("scenario", resolved_scenario)
        world.set_flag("witness_policy", str(config.get("witness_policy", "permissive")))
        self.runtime = PackageSimulationRuntime(
            PACKAGE_ID,
            clock=SimulationClock(
                float(time_scale if time_scale is not None else config.get("time_scale", 1.0))
            ),
            world=world,
            coverage=load_szlab_coverage(behavior_path),
            history_limit=int(config.get("history_limit", 500)),
        )

    @property
    def time_scale(self) -> float:
        """返回设备包仿真时间倍率。"""

        return self.runtime.clock.rate

    def initialize_protocol(self, values: Mapping[str, Any]) -> None:
        """记录 OPC UA 初始化投影，不把独立布尔节点提升为物料真源。"""

        for name, value in values.items():
            if isinstance(value, bool) and (
                str(name).startswith("传感器状态_") or str(name).endswith("信号")
            ):
                self.runtime.world.set_flag(f"opc:{name}", value)
        self.runtime.world.update_device(
            "szlab_poly_plc",
            state="ready",
            initialized_nodes=len(values),
        )

    def observe(self, event: Any) -> None:
        """接收旧协议状态机事件并合并到设备包级运行、物理与审计状态。"""

        action = str(event.action)
        phase = str(event.phase)
        detail = dict(event.detail)
        device_id = _device_for_action(action)
        if device_id == "szlab_mixer_stirrer" and detail.get("position") is not None:
            device_id = f"{device_id}:{int(detail['position'])}"
        self.runtime.record(device_id, action, phase, detail)

        if phase != "completed":
            return
        sensor = str(detail.get("sensor") or "")
        if sensor and bool(detail.get("site_witness_enabled", True)):
            self.runtime.world.set_flag(f"opc:{sensor}", bool(detail.get("occupied")))
        if "tool_holding" in detail and bool(detail.get("tool_witness_enabled", True)):
            self.runtime.world.update_device(
                "szlab_mixer_robot",
                tool_holding=bool(detail["tool_holding"]),
            )
        self.runtime.world.update_device(
            device_id,
            state="await_reset",
            last_result="SUCCEEDED",
            last_detail=detail,
        )

    def observe_external(
        self,
        action: str,
        phase: str,
        detail: Mapping[str, Any] | None = None,
    ) -> None:
        """记录 HTTP 等非 OPC UA Adapter 的设备动作。"""

        fq_action = str(action)
        if "." not in fq_action:
            fq_action = f"s1_workstation.{fq_action}"
        self.runtime.record(
            _device_for_action(fq_action),
            fq_action,
            phase,  # type: ignore[arg-type]
            detail,
        )

    def snapshot(self, protocol_snapshot: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """返回通用会话快照并附加 SZLab 协议周期。"""

        snapshot = self.runtime.snapshot()
        snapshot["protocol"] = dict(protocol_snapshot or {})
        return snapshot

    def stop(self) -> None:
        """停止设备包会话。"""

        self.runtime.stop()
