"""设备包级仿真运行时的通用状态、时钟和事件合同。

该模块不理解 OPC UA、SZLab 或具体工作流。协议适配器把已受理、完成、失败和
复位事件送入 :class:`PackageSimulationRuntime`，运行时负责稳定的运行身份、
共享世界状态、覆盖报告和可持久化快照。
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable, Iterable, Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

ActionPhase = Literal["accepted", "running", "completed", "failed", "reset"]
ActionState = Literal[
    "ACCEPTED",
    "RUNNING",
    "SUCCEEDED",
    "FAILED",
    "CANCELED",
]
CoverageStatus = Literal["modeled", "delegated", "query", "external", "unsupported"]


class WorldStateError(ValueError):
    """共享物理状态违反库存、数量或身份不变量。"""


class SimulationClock:
    """可调倍率、保持连续的单调仿真时钟。"""

    def __init__(
        self,
        rate: float = 1.0,
        *,
        source: Callable[[], float] = time.monotonic,
    ) -> None:
        """创建时钟；``rate`` 必须大于零，``source`` 供确定性测试注入。"""

        self._source = source
        self._lock = threading.RLock()
        self._rate = self._validate_rate(rate)
        self._real_anchor = float(source())
        self._sim_anchor = 0.0

    @staticmethod
    def _validate_rate(rate: float) -> float:
        value = float(rate)
        if value <= 0:
            raise ValueError("仿真时间倍率必须大于 0")
        return value

    @property
    def rate(self) -> float:
        """返回当前仿真时间倍率。"""

        with self._lock:
            return self._rate

    @rate.setter
    def rate(self, rate: float) -> None:
        """连续地切换时间倍率，不让仿真时间跳变。"""

        value = self._validate_rate(rate)
        with self._lock:
            now = float(self._source())
            self._sim_anchor += (now - self._real_anchor) * self._rate
            self._real_anchor = now
            self._rate = value

    def now(self) -> float:
        """返回从本会话启动起计算的仿真单调秒。"""

        with self._lock:
            return self._sim_anchor + (float(self._source()) - self._real_anchor) * self._rate

    def real_delay(self, simulated_seconds: float) -> float:
        """把仿真秒换算为当前倍率下的真实等待秒。"""

        return max(float(simulated_seconds), 0.0) / self.rate


@dataclass(frozen=True)
class SimulationEvent:
    """一条有稳定序号和运行身份的设备包仿真事件。"""

    sequence: int
    session_id: str
    run_id: str
    device_id: str
    action: str
    phase: ActionPhase
    timestamp: float
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class ActionRun:
    """一次协议动作从受理到终态的当前快照。"""

    run_id: str
    device_id: str
    action: str
    state: ActionState
    accepted_at: float
    updated_at: float
    completed_at: float | None = None
    detail: dict[str, Any] = field(default_factory=dict)


class WorldState:
    """设备状态、库位占用和物料数量的单一内存事实源。"""

    def __init__(
        self,
        *,
        sites: Mapping[str, str | None] | None = None,
        quantities: Mapping[str, float] | None = None,
        devices: Mapping[str, Mapping[str, Any]] | None = None,
        flags: Mapping[str, Any] | None = None,
    ) -> None:
        self._lock = threading.RLock()
        self._sites = {str(site): self._resource_or_none(value) for site, value in dict(sites or {}).items()}
        self._quantities = {str(key): self._quantity(value) for key, value in dict(quantities or {}).items()}
        self._devices = {str(key): dict(value) for key, value in dict(devices or {}).items()}
        self._flags = dict(flags or {})
        self._assert_unique_resources(self._sites)

    @staticmethod
    def _resource_or_none(value: str | None) -> str | None:
        if value is None:
            return None
        normalized = str(value).strip()
        return normalized or None

    @staticmethod
    def _quantity(value: float) -> float:
        normalized = float(value)
        if normalized < 0:
            raise WorldStateError("物料数量不得小于 0")
        return normalized

    @staticmethod
    def _assert_unique_resources(sites: Mapping[str, str | None]) -> None:
        occupied = [resource for resource in sites.values() if resource is not None]
        if len(occupied) != len(set(occupied)):
            raise WorldStateError("同一物料不能同时占用两个 Site")

    def occupy(self, site: str, resource: str, *, replace: bool = False) -> None:
        """把唯一物料放入 Site；默认拒绝覆盖或一物多位。"""

        site_id = str(site)
        resource_id = self._resource_or_none(resource)
        if resource_id is None:
            raise WorldStateError("物料身份不得为空")
        with self._lock:
            current = self._sites.get(site_id)
            if current not in (None, resource_id) and not replace:
                raise WorldStateError(f"Site {site_id} 已被 {current} 占用")
            for other_site, other_resource in self._sites.items():
                if other_site != site_id and other_resource == resource_id:
                    raise WorldStateError(f"物料 {resource_id} 已位于 {other_site}")
            self._sites[site_id] = resource_id

    def vacate(self, site: str, *, expected_resource: str | None = None) -> str | None:
        """清空 Site 并返回原物料；可校验调用方预期身份。"""

        site_id = str(site)
        with self._lock:
            current = self._sites.get(site_id)
            if expected_resource is not None and current != str(expected_resource):
                raise WorldStateError(
                    f"Site {site_id} 当前物料 {current!r} 与预期 {expected_resource!r} 不符"
                )
            self._sites[site_id] = None
            return current

    def move(self, source: str, target: str) -> str:
        """原子移动一个物料，目标被占用时不修改任何状态。"""

        with self._lock:
            resource = self._sites.get(str(source))
            if resource is None:
                raise WorldStateError(f"来源 Site {source} 没有物料")
            target_value = self._sites.get(str(target))
            if target_value is not None:
                raise WorldStateError(f"目标 Site {target} 已被 {target_value} 占用")
            self._sites[str(source)] = None
            self._sites[str(target)] = resource
            return resource

    def set_quantity(self, key: str, value: float) -> None:
        """设置非负物料数量。"""

        with self._lock:
            self._quantities[str(key)] = self._quantity(value)

    def adjust_quantity(self, key: str, delta: float) -> float:
        """增减数量并返回新值；结果为负时不修改状态。"""

        with self._lock:
            name = str(key)
            value = self._quantity(self._quantities.get(name, 0.0) + float(delta))
            self._quantities[name] = value
            return value

    def update_device(self, device_id: str, **state: Any) -> None:
        """合并一个设备的可观察状态。"""

        with self._lock:
            self._devices.setdefault(str(device_id), {}).update(state)

    def set_flag(self, name: str, value: Any) -> None:
        """写入场景或协议级标志。"""

        with self._lock:
            self._flags[str(name)] = value

    def snapshot(self) -> dict[str, Any]:
        """返回与内部容器隔离的 JSON 同构快照。"""

        with self._lock:
            return {
                "sites": dict(self._sites),
                "quantities": dict(self._quantities),
                "devices": {key: dict(value) for key, value in self._devices.items()},
                "flags": dict(self._flags),
            }

    def reset(self, snapshot: Mapping[str, Any]) -> None:
        """用已校验快照替换全部世界状态。"""

        replacement = WorldState(
            sites=dict(snapshot.get("sites", {})),
            quantities=dict(snapshot.get("quantities", {})),
            devices=dict(snapshot.get("devices", {})),
            flags=dict(snapshot.get("flags", {})),
        )
        with self._lock:
            state = replacement.snapshot()
            self._sites = state["sites"]
            self._quantities = state["quantities"]
            self._devices = state["devices"]
            self._flags = state["flags"]


class BehaviorCoverage:
    """按动作全限定名维护互斥的仿真覆盖分类。"""

    VALID = frozenset({"modeled", "delegated", "query", "external", "unsupported"})

    def __init__(self, actions: Mapping[str, CoverageStatus] | None = None) -> None:
        self._actions: dict[str, CoverageStatus] = {}
        for action, status in dict(actions or {}).items():
            self.register(action, status)

    def register(self, action: str, status: CoverageStatus) -> None:
        """登记或替换一个动作的覆盖状态。"""

        normalized = str(status)
        if normalized not in self.VALID:
            raise ValueError(f"未知覆盖状态: {status}")
        self._actions[str(action)] = normalized  # type: ignore[assignment]

    def status(self, action: str) -> CoverageStatus:
        """查询覆盖状态；未登记动作按 unsupported 关闭失败。"""

        return self._actions.get(str(action), "unsupported")

    def snapshot(self) -> dict[str, Any]:
        """返回逐动作分类和分类计数。"""

        counts = {status: 0 for status in sorted(self.VALID)}
        for status in self._actions.values():
            counts[status] += 1
        return {
            "counts": counts,
            "actions": dict(sorted(self._actions.items())),
            "total": len(self._actions),
        }


class PackageSimulationRuntime:
    """为一个设备包会话集中管理动作运行、事件和共享状态。"""

    def __init__(
        self,
        package_id: str,
        *,
        clock: SimulationClock | None = None,
        world: WorldState | None = None,
        coverage: BehaviorCoverage | None = None,
        history_limit: int = 500,
    ) -> None:
        if int(history_limit) < 1:
            raise ValueError("history_limit 必须大于 0")
        self.package_id = str(package_id)
        self.session_id = f"{self.package_id}-{uuid.uuid4().hex}"
        self.clock = clock or SimulationClock()
        self.world = world or WorldState()
        self.coverage = coverage or BehaviorCoverage()
        self.history_limit = int(history_limit)
        self._events: deque[SimulationEvent] = deque(maxlen=self.history_limit)
        self._runs: dict[str, ActionRun] = {}
        self._active_by_device: dict[str, str] = {}
        self._sequence = 0
        self._started_at = self.clock.now()
        self._stopped_at: float | None = None
        self._lock = threading.RLock()

    def record(
        self,
        device_id: str,
        action: str,
        phase: ActionPhase,
        detail: Mapping[str, Any] | None = None,
    ) -> SimulationEvent:
        """记录协议阶段并维护每设备唯一活动运行。"""

        device = str(device_id)
        action_name = str(action)
        payload = dict(detail or {})
        with self._lock:
            now = self.clock.now()
            if phase == "accepted":
                active = self._active_by_device.get(device)
                if active is not None and self._runs[active].state in {"ACCEPTED", "RUNNING"}:
                    raise RuntimeError(f"设备 {device} 已有活动运行 {active}")
                run_id = f"{self.session_id}:{uuid.uuid4().hex}"
                self._runs[run_id] = ActionRun(
                    run_id=run_id,
                    device_id=device,
                    action=action_name,
                    state="ACCEPTED",
                    accepted_at=now,
                    updated_at=now,
                    detail=payload,
                )
                self._active_by_device[device] = run_id
            else:
                run_id = self._active_by_device.get(device, "")
                if not run_id:
                    raise RuntimeError(f"设备 {device} 没有可接收 {phase} 的活动运行")
                run = self._runs[run_id]
                if run.action != action_name:
                    raise RuntimeError(
                        f"设备 {device} 的活动动作是 {run.action}，不能记录 {action_name}"
                    )
                if phase in {"running", "completed", "failed"} and run.state not in {
                    "ACCEPTED",
                    "RUNNING",
                }:
                    raise RuntimeError(
                        f"动作 {action_name} 已是终态 {run.state}，不能记录 {phase}"
                    )
                run.updated_at = now
                run.detail.update(payload)
                if phase == "running":
                    run.state = "RUNNING"
                elif phase == "completed":
                    run.state = "SUCCEEDED"
                    run.completed_at = now
                elif phase == "failed":
                    run.state = "FAILED"
                    run.completed_at = now
                elif phase == "reset":
                    if run.state in {"ACCEPTED", "RUNNING"}:
                        run.state = "CANCELED"
                        run.completed_at = now
                    self._active_by_device.pop(device, None)
            self._sequence += 1
            event = SimulationEvent(
                sequence=self._sequence,
                session_id=self.session_id,
                run_id=run_id,
                device_id=device,
                action=action_name,
                phase=phase,
                timestamp=now,
                detail=payload,
            )
            self._events.append(event)
            self.world.update_device(
                device,
                action=action_name,
                phase=phase,
                run_id=run_id,
                updated_at=now,
            )
            return event

    def snapshot(self) -> dict[str, Any]:
        """返回完整且可 JSON 序列化的设备包会话快照。"""

        with self._lock:
            active_ids = set(self._active_by_device.values())
            active = [asdict(run) for run_id, run in self._runs.items() if run_id in active_ids]
            terminal = [asdict(run) for run_id, run in self._runs.items() if run_id not in active_ids]
            terminal.sort(key=lambda item: item["updated_at"], reverse=True)
            return {
                "schema": "unilab.package_simulation/v1",
                "package_id": self.package_id,
                "session_id": self.session_id,
                "state": "stopped" if self._stopped_at is not None else "running",
                "time_scale": self.clock.rate,
                "started_at": self._started_at,
                "stopped_at": self._stopped_at,
                "sequence": self._sequence,
                "active_runs": active,
                "recent_runs": terminal[:100],
                "events": [asdict(event) for event in self._events],
                "world": self.world.snapshot(),
                "coverage": self.coverage.snapshot(),
            }

    def stop(self) -> None:
        """标记会话停止；重复调用安全。"""

        with self._lock:
            if self._stopped_at is None:
                self._stopped_at = self.clock.now()


def write_snapshot_atomic(path: str | Path, snapshot: Mapping[str, Any]) -> None:
    """把 JSON 快照原子替换到目标路径，避免 GUI 读到半个文件。"""

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent)
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def coverage_from_groups(
    *,
    modeled: Iterable[str] = (),
    delegated: Iterable[str] = (),
    query: Iterable[str] = (),
    external: Iterable[str] = (),
    unsupported: Iterable[str] = (),
) -> BehaviorCoverage:
    """从互斥动作集合构造覆盖表，重复动作会抛错防止分类漂移。"""

    groups: tuple[tuple[CoverageStatus, Iterable[str]], ...] = (
        ("modeled", modeled),
        ("delegated", delegated),
        ("query", query),
        ("external", external),
        ("unsupported", unsupported),
    )
    result = BehaviorCoverage()
    seen: set[str] = set()
    for status, actions in groups:
        for action in actions:
            name = str(action)
            if name in seen:
                raise ValueError(f"动作覆盖分类重复: {name}")
            seen.add(name)
            result.register(name, status)
    return result
