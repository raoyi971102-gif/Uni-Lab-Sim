"""PTLC PLC 输入、执行器反馈与外部物料事件仿真。

本模块只拥有 PLC 可观测事实。机器人等直连设备通过幂等物料事件更新站点，
不得在这里提交机器人姿态、工具命令或工作流控制。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Any

PTLC_SENSOR_KEYS = frozenset(
    {
        "bottle_present",
        "staging_a_present",
        "staging_b_present",
        "sampling_tray_1_present",
        "sampling_tray_2_present",
        "rack_occupied",
    }
)
PTLC_SENSOR_SITES = frozenset(
    {
        "collect_bottle",
        "staging_a",
        "staging_b",
        "sampling_tray_1",
        "sampling_tray_2",
        *(f"rack_{index:02d}" for index in range(1, 13)),
    }
)
PTLC_TRANSFER_SITES = PTLC_SENSOR_SITES | {"external"}
PTLC_EVENT_KINDS = frozenset({"site_set", "material_transfer"})

_SIMPLE_SITE_SENSOR = {
    "collect_bottle": "bottle_present",
    "staging_a": "staging_a_present",
    "staging_b": "staging_b_present",
    "sampling_tray_1": "sampling_tray_1_present",
    "sampling_tray_2": "sampling_tray_2_present",
}


@dataclass(order=True)
class SensorTransition:
    """一个使用单调时钟推进的 PLC 物理反馈转换。"""

    due_at: float
    sequence: int
    kind: str
    key: str
    value: Any


class PtlcSensorEngine:
    """封装 PTLC 输入映像、气缸到位反馈和物料站点事件。"""

    def __init__(
        self,
        adapter: Any,
        world: dict[str, Any],
        config: Mapping[str, Any] | None = None,
    ) -> None:
        self.adapter = adapter
        self.world = world
        plant = dict(config or {})
        mode = str(plant.get("sensor_mode", "federated")).strip().lower()
        if mode not in {"standalone", "federated"}:
            raise ValueError("plant.sensor_mode 仅支持 standalone/federated")
        self.mode = mode
        self.synthesize_inputs = bool(plant.get("synthesize_inputs", True))
        self.cylinder_s = max(float(plant.get("cylinder_s", 0.2)), 0.0)
        self.external_transition_s = max(
            float(plant.get("external_transition_s", 0.5)), 0.0
        )
        self.world.setdefault("cylinders", {})
        self.world.setdefault("cylinder_feedback", dict(self.world["cylinders"]))
        self.world.setdefault("sensors", {})
        self._transitions: list[SensorTransition] = []
        self._sequence = 0
        self._event_ids: set[str] = set()
        self._recent_events: list[dict[str, Any]] = []

    @property
    def standalone(self) -> bool:
        """是否允许为 PLC 独立调试自动补齐外部设备造成的输入变化。"""

        return self.mode == "standalone"

    def snapshot(self) -> dict[str, Any]:
        """返回传感器模式、在途反馈和最近外部事件。"""

        return {
            "mode": self.mode,
            "cylinder_feedback": dict(self.world["cylinder_feedback"]),
            "sites": {
                site: self.site_present(site) for site in sorted(PTLC_SENSOR_SITES)
            },
            "pending_transitions": [asdict(item) for item in sorted(self._transitions)],
            "recent_events": list(self._recent_events),
        }

    def apply_world_patch(self, payload: Mapping[str, Any]) -> None:
        """应用绝对 PLC 输入事实和幂等外部事件，忽略越界字段。"""

        for name in ("feed_count", "waste_count"):
            if name in payload:
                self.world[name] = max(0, int(payload[name]))
        for name in ("feed_homed", "waste_homed"):
            if name in payload:
                self.world[name] = bool(payload[name])
        sensors = payload.get("sensors")
        if isinstance(sensors, Mapping):
            for key, value in sensors.items():
                if str(key) in PTLC_SENSOR_KEYS:
                    self._set_sensor(str(key), value)
        events = payload.get("events")
        if isinstance(events, list):
            for event in events:
                if isinstance(event, Mapping):
                    self.apply_external_event(event)
        self.sync_inputs()

    def apply_external_event(self, event: Mapping[str, Any]) -> bool:
        """应用一个幂等站点事件；已处理事件返回假，新事件返回真。"""

        event_id = str(event.get("event_id", "")).strip()
        kind = str(event.get("kind", "")).strip()
        if not event_id:
            self._record_event("", kind, "rejected", "缺少 event_id")
            return False
        if event_id in self._event_ids:
            self._record_event(event_id, kind, "duplicate")
            return False
        if kind == "site_set":
            site = str(event.get("site", ""))
            if site not in PTLC_SENSOR_SITES or not isinstance(
                event.get("present"), bool
            ):
                self._record_event(event_id, kind, "rejected", "站点或 present 非法")
                return False
            self.set_site(site, bool(event["present"]))
        elif kind == "material_transfer":
            source = str(event.get("source", ""))
            target = str(event.get("target", ""))
            if (
                source not in PTLC_TRANSFER_SITES
                or target not in PTLC_TRANSFER_SITES
                or source == target
                or source == target == "external"
            ):
                self._record_event(event_id, kind, "rejected", "源或目标站点非法")
                return False
            if source != "external":
                self.set_site(source, False)
            if target != "external":
                self.set_site(target, True)
        else:
            self._record_event(event_id, kind, "rejected", "未知事件类型")
            return False
        self._event_ids.add(event_id)
        self._record_event(event_id, kind, "accepted")
        self.sync_inputs()
        return True

    def schedule_cylinder(
        self,
        name: str,
        target: bool,
        now: float,
        *,
        command_after: float = 0.0,
    ) -> float:
        """计划气缸命令和延迟到位反馈，返回反馈到期时钟。"""

        command_at = float(now) + max(float(command_after), 0.0)
        feedback_at = command_at + self.cylinder_s
        if command_at <= float(now) + 1e-9:
            self.world["cylinders"][name] = bool(target)
        else:
            self._schedule(command_at, "cylinder_command", name, bool(target))
        if feedback_at <= float(now) + 1e-9:
            self.world["cylinder_feedback"][name] = bool(target)
        else:
            self._schedule(feedback_at, "cylinder_feedback", name, bool(target))
        self.sync_inputs()
        return feedback_at

    def schedule_node(
        self, name: str, value: Any, now: float, *, after: float = 0.0
    ) -> float:
        """计划 PLC 控制过程产生的输入节点变化并返回到期时钟。"""

        due_at = float(now) + max(float(after), 0.0)
        if due_at <= float(now) + 1e-9:
            self._write(name, value)
        else:
            self._schedule(due_at, "node", name, value)
        return due_at

    def request_collect_gate(self, code: int, now: float) -> float | None:
        """在独立模式安排收瓶站外部取放；联邦模式不推断机器人行为。"""

        if not self.standalone or code not in {22, 23}:
            return None
        due_at = float(now) + self.external_transition_s
        self._schedule(
            due_at,
            "site",
            "collect_bottle",
            code != 22,
        )
        return due_at

    def advance(self, now: float) -> None:
        """推进所有已到期转换并重新合成 PLC 输入。"""

        current = float(now)
        ready = [item for item in self._transitions if item.due_at <= current + 1e-9]
        self._transitions = [
            item for item in self._transitions if item.due_at > current + 1e-9
        ]
        for item in sorted(ready):
            if item.kind == "cylinder_command":
                self.world["cylinders"][item.key] = bool(item.value)
            elif item.kind == "cylinder_feedback":
                self.world["cylinder_feedback"][item.key] = bool(item.value)
            elif item.kind == "site":
                self.set_site(item.key, bool(item.value))
            elif item.kind == "node":
                self._write(item.key, item.value)
        self.sync_inputs()

    def cylinder_feedback(self, name: str, default: bool = False) -> bool:
        """读取气缸到位事实，而不是输出命令。"""

        return bool(self.world["cylinder_feedback"].get(name, default))

    def site_present(self, site: str) -> bool:
        """读取 PLC 管辖站点的物料存在状态。"""

        sensors = self.world["sensors"]
        if site in _SIMPLE_SITE_SENSOR:
            return bool(sensors.get(_SIMPLE_SITE_SENSOR[site], False))
        if site.startswith("rack_"):
            index = int(site.rsplit("_", 1)[1]) - 1
            rack = list(sensors.get("rack_occupied", []))
            return bool(rack[index]) if index < len(rack) else False
        return False

    def set_site(self, site: str, present: bool) -> None:
        """设置一个 PLC 管辖站点的物料存在状态。"""

        if site in _SIMPLE_SITE_SENSOR:
            self.world["sensors"][_SIMPLE_SITE_SENSOR[site]] = bool(present)
            return
        if site.startswith("rack_") and site in PTLC_SENSOR_SITES:
            index = int(site.rsplit("_", 1)[1]) - 1
            rack = list(self.world["sensors"].get("rack_occupied", []))
            rack.extend([False] * max(0, 12 - len(rack)))
            rack[index] = bool(present)
            self.world["sensors"]["rack_occupied"] = rack[:12]

    def sync_inputs(self) -> None:
        """把世界事实合成为 IX8..IX12，同时保留未托管输入位。"""

        if not self.synthesize_inputs:
            return
        sensors = self.world["sensors"]
        ix8 = int(self._read("IX8", 0) or 0)
        for key, bit in (("bottle_present", 1), ("staging_b_present", 2)):
            if key in sensors:
                ix8 = self._set_bit(ix8, bit, bool(sensors[key]))
        feed_z = float(self._read("FeedLift_1Z_ActPos", 0.0) or 0.0)
        waste_z = float(self._read("FeedLift_2Z_ActPos", 0.0) or 0.0)
        ix8 = self._set_bit(ix8, 3, self._photo("feed", feed_z))
        ix8 = self._set_bit(ix8, 4, self._photo("waste", waste_z))
        ix8 = self._set_bit(ix8, 5, int(self.world["feed_count"]) > 0)
        ix8 = self._set_bit(ix8, 6, int(self.world["waste_count"]) > 0)
        self._write("IX8", ix8)

        ix9 = int(self._read("IX9", 0) or 0)
        for key, bit in (
            ("sampling_tray_1_present", 0),
            ("sampling_tray_2_present", 1),
        ):
            if key in sensors:
                ix9 = self._set_bit(ix9, bit, bool(sensors[key]))
        rotate = self.cylinder_feedback("photoscrape_rotate")
        ix9 = self._set_bit(ix9, 6, not rotate)
        ix9 = self._set_bit(ix9, 7, rotate)
        self._write("IX9", ix9)

        ix10 = int(self._read("IX10", 0) or 0)
        ix10 = self._set_bit(ix10, 0, not self.cylinder_feedback("photoscrape_press"))
        if "staging_a_present" in sensors:
            ix10 = self._set_bit(ix10, 2, bool(sensors["staging_a_present"]))
        self._write("IX10", ix10)

        rack = sensors.get("rack_occupied")
        if isinstance(rack, (list, tuple)):
            bits = [bool(value) for value in rack[:12]]
            bits.extend([False] * (12 - len(bits)))
            self._write(
                "IX11", sum(1 << index for index, value in enumerate(bits[:8]) if value)
            )
            old_ix12 = int(self._read("IX12", 0) or 0) & 0xF0
            self._write(
                "IX12",
                old_ix12
                | sum(1 << index for index, value in enumerate(bits[8:12]) if value),
            )

    def _photo(self, magazine: str, z_mm: float) -> bool:
        if magazine == "feed" and int(self.world["feed_count"]) <= 0:
            return False
        calibration = self.world["feedlift_calibration"][magazine]
        count = int(self.world[f"{magazine}_count"])
        trigger = float(calibration["z_empty_mm"]) - count * float(
            calibration["pitch_mm"]
        )
        return z_mm >= trigger - 1e-9

    def _set_sensor(self, key: str, value: Any) -> None:
        if key == "rack_occupied" and isinstance(value, (list, tuple)):
            rack = [bool(item) for item in value[:12]]
            rack.extend([False] * (12 - len(rack)))
            self.world["sensors"][key] = rack
        elif key != "rack_occupied":
            self.world["sensors"][key] = bool(value)

    def _schedule(self, due_at: float, kind: str, key: str, value: Any) -> None:
        self._sequence += 1
        self._transitions.append(
            SensorTransition(float(due_at), self._sequence, kind, key, value)
        )

    def _record_event(
        self, event_id: str, kind: str, status: str, reason: str = ""
    ) -> None:
        item = {"event_id": event_id, "kind": kind, "status": status}
        if reason:
            item["reason"] = reason
        self._recent_events.append(item)
        self._recent_events = self._recent_events[-50:]

    @staticmethod
    def _set_bit(value: int, bit: int, enabled: bool) -> int:
        return value | (1 << bit) if enabled else value & ~(1 << bit)

    def _read(self, name: str, default: Any) -> Any:
        try:
            return self.adapter.read(name)
        except (KeyError, TypeError, ValueError):
            return default

    def _write(self, name: str, value: Any) -> None:
        try:
            self.adapter.write(name, value)
        except KeyError:
            return
