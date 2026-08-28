"""PTLC 的 PLC 设备仿真深模块。

本模块只复刻 PLC 可观察行为：轴、气缸、泵、阀、液位、输入映像与动作时序。
工作流、机器人、相机、视觉和主机动作由 Uni-Lab OS 或独立设备模拟器负责。
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

try:
    from .ptlc_behavior import StationContract
    from .ptlc_runtime import MODELED_ACTIONS, MotionSegment, VariableAdapter
    from .ptlc_sensors import PTLC_SENSOR_KEYS, PtlcSensorEngine
except ImportError:  # Direct source execution compatibility.
    from ptlc_behavior import StationContract
    from ptlc_runtime import MODELED_ACTIONS, MotionSegment, VariableAdapter
    from ptlc_sensors import PTLC_SENSOR_KEYS, PtlcSensorEngine


@dataclass
class PlantAction:
    """握手层与 PLC 设备仿真之间唯一的活动动作载体。"""

    station: str
    code: int
    started_at: float
    duration: float
    outcome: str = "done"
    error_code: int = 0
    safe_state: int = 10
    retryable: bool = False
    steps: tuple[int, ...] = ()
    motion: tuple[MotionSegment, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)


class PtlcPlant:
    """封装全部 PTLC PLC 侧物理行为，对握手层提供小而稳定的接口。"""

    _P_VALUE = re.compile(r"P\s*([+-]?\d+)", re.IGNORECASE)
    _A_VALUE = re.compile(r"A\s*([+-]?\d+)", re.IGNORECASE)
    SENSOR_KEYS = PTLC_SENSOR_KEYS

    _REQUIRED_NODES = frozenset(
        {
            "IX8",
            "IX9",
            "IX10",
            "IX11",
            "IX12",
            "collect_count",
            "collect_forward_instructions",
            "Collect_BottleLocate_Target",
            "Expand_Target_Tank",
            "Expand_forward_instructions",
            "Expand_rinse_count",
            "Expand_up_liquid_count",
            "Expand_Waste_Empty_G1",
            "Expand_Waste_Empty_G2",
            "Tank_State",
            "Tank_Drain_Enable",
            "Tank_Drain_Done",
            "Tank_Drain_CapHit",
            "Tank_Drain_S",
            "Tank_Drain_Cap_S",
            "Tank_Blow_S",
            "Tank_Dry_S",
            "Tank_Suction_Settle_S",
            "Tank_Suction_Empty_S",
            "Tank_Suction_Blow_S",
            "Tank_Suction_Cap_S",
            "Sampling_clean_count",
            "Sampling_clean_mode",
            "Sampling_sample_instructions",
            "Sampling_rinse_mix_instructions",
            "Sampling_rinse_mix_count",
            "Sampling_band_run_instruction",
            "Sampling_band_dry_cycles",
            "Sampling_band_end_position",
            "Sampling_4X_Target",
            "Sampling_3Y_Target",
            "Sampling_4X_WashTarget",
            "Sampling_4X_ActPos",
            "Sampling_3Y_ActPos",
            "Sampling_5Z_ActPos",
            "Spot_6X_StartTarget",
            "Spot_6X_EndTarget",
            "Spot_7Y_Target",
            "Spot_6X_ActPos",
            "Spot_7Y_ActPos",
            "FeedLift_1Z_ActPos",
            "FeedLift_2Z_ActPos",
            "FeedLift_1Z_SearchLowTarget",
            "FeedLift_1Z_SearchHighTarget",
            "FeedLift_2Z_SearchLowTarget",
            "FeedLift_2Z_SearchHighTarget",
            "FeedLift_DebugAxis",
            "FeedLift_DebugExpectedFinal",
            "Photo_8Y_Target",
            "Photo_8Y_ActPos",
            "PhotoScrape_8Y_ActPos",
            "PhotoScrape_9X_ActPos",
            "PhotoScrape_10Z_ActPos",
            "PhotoScrape_Align_TargetX",
            "PhotoScrape_Align_TargetY",
            "PhotoScrape_Align_TargetZ",
            "PhotoScrape_CamLocate_Target",
            "PhotoScrape_CamPress_Target",
            "PhotoScrape_PowderCollectorLocate_Target",
            "Pump_Vacuum_On",
            "Rail_Target_Position",
            "Rail_Current_Position",
            "Rail_Pos_Target",
            "Rail_ActPos",
            "Rail_Homed",
            "StagingA_LocatorA_Target",
            "StagingA_LocatorB_Target",
        }
    )

    def __init__(
        self,
        adapter: VariableAdapter,
        contracts: Mapping[str, StationContract],
        config: Mapping[str, Any] | None = None,
    ) -> None:
        """创建 PLC 设备仿真；依赖的变量端口、契约与配置均由外部注入。"""

        self.adapter = adapter
        self.contracts = dict(contracts)
        self.config = dict(config or {})
        plant = dict(self.config.get("plant", {}))
        material = dict(dict(self.config.get("process", {})).get("material", {}))
        self.world: dict[str, Any] = {
            "feed_count": max(0, int(material.get("feed_count", 12))),
            "waste_count": max(0, int(material.get("waste_count", 0))),
            "capacity": max(1, int(material.get("capacity", 30))),
            "feed_homed": bool(plant.get("feed_homed", True)),
            "waste_homed": bool(plant.get("waste_homed", True)),
            "waste_armed": False,
            "vacuum_on": False,
            "cylinders": {},
            "cylinder_feedback": {},
            "outputs": {},
            "sensors": dict(plant.get("sensors", {})),
            "pump_position": {
                "sampling": 0,
                "collect": 0,
                "develop_1": 0,
                "develop_2": 0,
            },
        }
        calibration = dict(plant.get("feedlift_calibration", {}))
        self.feedlift_calibration = {
            "feed": {
                "z_empty_mm": float(
                    dict(calibration.get("feed", {})).get("z_empty_mm", 500.0)
                ),
                "pitch_mm": float(
                    dict(calibration.get("feed", {})).get("pitch_mm", 2.5)
                ),
            },
            "waste": {
                "z_empty_mm": float(
                    dict(calibration.get("waste", {})).get("z_empty_mm", 500.0)
                ),
                "pitch_mm": float(
                    dict(calibration.get("waste", {})).get("pitch_mm", 2.5)
                ),
            },
        }
        self.world["feedlift_calibration"] = self.feedlift_calibration
        self.phase_s = max(float(plant.get("phase_s", 0.05)), 0.001)
        self.cylinder_s = max(float(plant.get("cylinder_s", 0.2)), 0.0)
        self.cnc_s = max(float(plant.get("cnc_s", 2.0)), 0.0)
        self.jog_speed = max(float(plant.get("jog_speed_mm_s", 15.0)), 0.001)
        self.sensors = PtlcSensorEngine(self.adapter, self.world, plant)

    @property
    def modeled_actions(self) -> dict[str, frozenset[int]]:
        """返回 PLC 设备仿真覆盖的动作矩阵。"""

        return MODELED_ACTIONS

    def required_nodes(self) -> tuple[str, ...]:
        """返回 PLC 设备仿真直接依赖的现有 OPC UA BrowseName。"""

        return tuple(sorted(self._REQUIRED_NODES))

    def snapshot(self) -> dict[str, Any]:
        """返回不含外部设备状态的可序列化 PLC 世界快照。"""

        result = dict(self.world)
        result.pop("feedlift_calibration", None)
        result["cylinders"] = dict(self.world["cylinders"])
        result["cylinder_feedback"] = dict(self.world["cylinder_feedback"])
        result["outputs"] = dict(self.world["outputs"])
        result["sensors"] = dict(self.world["sensors"])
        result["pump_position"] = dict(self.world["pump_position"])
        result["coverage"] = {
            "modeled": sum(len(codes) for codes in MODELED_ACTIONS.values()),
            "accepted": sum(
                len(contract.accepts) for contract in self.contracts.values()
            ),
            "unmodeled": {
                station: sorted(
                    set(contract.accepts) - MODELED_ACTIONS.get(station, frozenset())
                )
                for station, contract in self.contracts.items()
            },
        }
        result["sensor_simulation"] = self.sensors.snapshot()
        return result

    def apply_world_patch(self, payload: Mapping[str, Any]) -> None:
        """接收外部设备模拟器提供的 PLC 输入事实，不接受工作流或机器人命令。"""

        self.sensors.apply_world_patch(payload)

    def sync_inputs(self) -> None:
        """把 PLC 世界事实合成为现有 IX8..IX12 输入节点，保留未托管位。"""

        self.sensors.sync_inputs()

    def advance(self, now: float) -> None:
        """推进不依附单个活动周期的传感器与执行器反馈。"""

        self.sensors.advance(now)

    def request_gate_resolution(
        self, station: str, code: int, now: float
    ) -> float | None:
        """请求独立模式补齐外部物料门禁；联邦模式返回空。"""

        if station == "Collect":
            return self.sensors.request_collect_gate(code, now)
        return None

    def begin(
        self, station: str, code: int, now: float, default_delay: float
    ) -> PlantAction:
        """校验并构造一个 PLC 动作执行计划；不处理 L2 序号和终态。"""

        contract = self.contracts[station]
        action_contract = contract.action(code)
        steps = action_contract.steps if action_contract is not None else ()
        result = PlantAction(
            station, code, now, max(float(default_delay), 0.0), steps=steps
        )
        validation = self._validate(station, code)
        if validation is not None:
            result.outcome, result.error_code, result.safe_state, result.retryable = (
                validation
            )
        if result.outcome == "done" or station == "Sampling" and code == 50:
            result.motion = self._motion_for(station, code)
        result.duration = max(
            self._duration(station, code, result.duration),
            max(
                (segment.starts_after + segment.duration for segment in result.motion),
                default=0.0,
            ),
        )
        if (
            action_contract is not None
            and action_contract.kind == "instant"
            and result.outcome == "done"
        ):
            result.duration = 0.0
        if result.outcome in {"rejected", "error"} and result.duration <= 0:
            result.duration = 0.0
        if result.outcome == "done":
            sensor_duration = self._schedule_action_feedback(
                result,
                max(
                    (
                        segment.starts_after + segment.duration
                        for segment in result.motion
                    ),
                    default=0.0,
                ),
            )
            result.duration = max(result.duration, sensor_duration)
        return result

    def progress(self, action: PlantAction, now: float) -> None:
        """推进活动动作的轴位置和输入合成。"""

        self.advance(now)
        elapsed = max(0.0, float(now) - action.started_at)
        for segment in action.motion:
            if elapsed < segment.starts_after:
                continue
            fraction = (
                1.0
                if segment.duration == 0
                else min(
                    max((elapsed - segment.starts_after) / segment.duration, 0.0), 1.0
                )
            )
            self._write(
                segment.actual_name,
                segment.start + (segment.target - segment.start) * fraction,
            )
            if segment.actual_name == "Photo_8Y_ActPos":
                self._write(
                    "PhotoScrape_8Y_ActPos", self._read(segment.actual_name, 0.0)
                )
        self.sync_inputs()

    def finish(self, action: PlantAction) -> None:
        """提交正常完成动作的 PLC 侧副作用。"""

        if action.outcome not in {"done", "tank_drain", "tank_release"}:
            return
        station, code = action.station, action.code
        if station == "Sampling":
            self._finish_sampling(code)
        elif station == "Collect":
            self._finish_collect(code)
        elif station == "Develop":
            self._finish_develop(code)
        elif station == "PhotoScrape":
            self._finish_photoscrape(code)
        elif station == "FeedLift":
            if self.sensors.standalone and code == 12:
                self.world["feed_count"] = max(0, int(self.world["feed_count"]) - 1)
            elif self.sensors.standalone and code == 21:
                self.world["waste_count"] = min(
                    int(self.world["capacity"]), int(self.world["waste_count"]) + 1
                )
                self.world["waste_armed"] = True
            elif code == 21:
                self.world["waste_armed"] = True
        elif station == "Pump":
            self.world["vacuum_on"] = code == 10
            self._write("Pump_Vacuum_On", code == 10)
        elif station == "Rail" and code == 10:
            self._write(
                "Rail_Current_Position", int(self._read("Rail_Target_Position", 0))
            )
            self._write("Rail_Homed", True)
        elif station == "StagingA":
            target_name = (
                "StagingA_LocatorA_Target" if code == 24 else "StagingA_LocatorB_Target"
            )
            cylinder = "staging_a_locator" if code == 24 else "collect_bottle_locator"
            self.world["cylinders"][cylinder] = bool(self._read(target_name, False))
        self.sync_inputs()

    def cancel(self, action: PlantAction | None) -> None:
        """撤销 PLC 动作的在途命令；已形成的物理状态保持。"""

        if (
            action is not None
            and action.station == "Sampling"
            and action.code in {55, 62}
        ):
            self.world["outputs"]["sampling_air"] = False
            self.world["outputs"]["sampling_three_way"] = False

    def _schedule_action_feedback(
        self, action: PlantAction, motion_duration: float
    ) -> float:
        """把动作中的气缸/过程输入转换安排到传感器时钟。"""

        station, code, now = action.station, action.code, action.started_at
        commands: list[tuple[float, str, bool]] = []
        wait_for_feedback = (station, code) in {
            ("Collect", 10),
            ("Collect", 21),
            ("Collect", 22),
            ("Collect", 30),
            ("Collect", 41),
            ("Collect", 42),
            ("Collect", 43),
            ("Develop", 10),
            ("Develop", 21),
            ("Develop", 31),
            ("Develop", 32),
            ("PhotoScrape", 10),
            ("PhotoScrape", 34),
            ("PhotoScrape", 35),
            ("PhotoScrape", 51),
        }
        if station == "Sampling":
            if code == 32:
                commands.append((0.0, "sampling_locator", True))
            elif code in {33, 60}:
                commands.append((0.0, "sampling_locator", False))
        elif station == "Collect":
            if code == 10:
                commands.extend(
                    (0.0, name, False)
                    for name in (
                        "collect_press",
                        "collect_clamp",
                        "collect_lift",
                        "collect_extend",
                        "collect_fill",
                        "collect_drain",
                    )
                )
            elif code == 21:
                commands.append((0.0, "collect_clamp", True))
            elif code == 22:
                commands.append((0.0, "collect_extend", True))
            elif code == 23:
                commands.extend(
                    (
                        (0.0, "collect_extend", False),
                        (self.cylinder_s, "collect_lift", True),
                        (self.cylinder_s * 2, "collect_press", True),
                    )
                )
            elif code == 24:
                commands.append(
                    (
                        0.0,
                        "collect_bottle_locator",
                        bool(self._read("Collect_BottleLocate_Target", False)),
                    )
                )
            elif code == 30:
                switch = max(action.duration * 0.5, self.cylinder_s)
                end = max(action.duration - self.cylinder_s, switch)
                commands.extend(
                    (
                        (0.0, "collect_fill", True),
                        (switch, "collect_fill", False),
                        (switch, "collect_drain", True),
                        (end, "collect_drain", False),
                    )
                )
            elif code == 41:
                commands.extend(
                    (
                        (0.0, "collect_press", False),
                        (self.cylinder_s, "collect_lift", False),
                        (self.cylinder_s * 2, "collect_extend", True),
                    )
                )
            elif code == 42:
                commands.append((0.0, "collect_extend", False))
            elif code == 43:
                commands.append((0.0, "collect_clamp", False))
        elif station == "Develop":
            target = int(self._read("Expand_Target_Tank", 1) or 1)
            tank = f"tank_{target}"
            if code in {10, 31}:
                commands.append((0.0, tank, False))
            elif code in {21, 32}:
                commands.append((0.0, tank, True))
            if code == 26:
                settle = max(
                    0.0, float(self._read("Tank_Suction_Settle_S", 0.0) or 0.0)
                )
                empty = max(0.0, float(self._read("Tank_Suction_Empty_S", 0.0) or 0.0))
                group = 1 if target <= 4 else 2
                self.sensors.schedule_node(
                    f"Expand_Waste_Empty_G{group}",
                    True,
                    now,
                    after=settle + empty,
                )
        elif station == "PhotoScrape":
            if code == 10:
                commands.extend(
                    (0.0, name, False)
                    for name in (
                        "photoscrape_locator",
                        "powder_collector_locator",
                        "photoscrape_shade",
                        "photoscrape_rotate",
                        "photoscrape_press",
                    )
                )
            elif code == 32:
                commands.append(
                    (
                        0.0,
                        "photoscrape_locator",
                        bool(self._read("PhotoScrape_CamLocate_Target", False)),
                    )
                )
            elif code == 33:
                target = bool(self._read("PhotoScrape_CamPress_Target", False))
                commands.append((0.0, "photoscrape_press", target))
                wait_for_feedback = not target
            elif code == 34:
                commands.append((motion_duration, "photoscrape_shade", True))
            elif code == 35:
                commands.append((0.0, "photoscrape_shade", False))
            elif code == 36:
                commands.append(
                    (
                        0.0,
                        "powder_collector_locator",
                        bool(
                            self._read(
                                "PhotoScrape_PowderCollectorLocate_Target", False
                            )
                        ),
                    )
                )
            elif code == 41:
                commands.append((0.0, "photoscrape_rotate", True))
            elif code == 51:
                commands.append((0.0, "photoscrape_press", False))
            elif code == 52:
                commands.append((0.0, "photoscrape_rotate", False))
        elif station == "StagingA":
            target_name = (
                "StagingA_LocatorA_Target" if code == 24 else "StagingA_LocatorB_Target"
            )
            cylinder = "staging_a_locator" if code == 24 else "collect_bottle_locator"
            commands.append((0.0, cylinder, bool(self._read(target_name, False))))

        latest = now
        for offset, name, target in commands:
            latest = max(
                latest,
                self.sensors.schedule_cylinder(
                    name, target, now, command_after=max(offset, 0.0)
                ),
            )
        return max(latest - now, 0.0) if wait_for_feedback else 0.0

    def _validate(self, station: str, code: int) -> tuple[str, int, int, bool] | None:
        """执行只依赖 PLC 节点和 PLC 世界的动作门禁。"""

        if station == "Sampling" and code == 50:
            instructions = self._read("Sampling_sample_instructions", ["", ""])
            text = str(next(iter(instructions))) if instructions else ""
            match = self._P_VALUE.search(text)
            amount = int(match.group(1)) if match else 0
            position = int(self.world["pump_position"]["sampling"])
            if amount <= 0 or position + amount > int(
                self.contracts[station].constants.get("piston_step_max", 6000)
            ):
                return "error", 463, 90, True
        elif station == "Sampling" and code == 55:
            count = int(self._read("Sampling_rinse_mix_count", 0) or 0)
            instructions = list(self._read("Sampling_rinse_mix_instructions", []))
            if (
                not 1 <= count <= 20
                or len(instructions) != 4
                or not all(str(item).strip() for item in instructions)
            ):
                return "error", 466, 90, False
        elif station == "Sampling" and code == 62:
            instruction = str(self._read("Sampling_band_run_instruction", ""))
            target_match = self._A_VALUE.search(instruction)
            if target_match:
                target = int(target_match.group(1))
                current = int(self.world["pump_position"]["sampling"])
                pass_size = max(abs(current - target), 1)
                passes = math.ceil(
                    abs(current - int(self._read("Sampling_band_end_position", 0) or 0))
                    / pass_size
                )
                if passes > int(
                    self.contracts[station].constants.get("band_pass_max", 60)
                ):
                    return "error", 462, 90, True
        elif station == "Rail" and code == 10:
            position = int(self._read("Rail_Target_Position", 0) or 0)
            targets = list(self._read("Rail_Pos_Target", []))
            if not 1 <= position <= 6:
                return "rejected", 101, 0, True
            if (
                position > len(targets)
                or not 0.0 < float(targets[position - 1]) <= 3000.0
            ):
                return "rejected", 102, 0, True
        elif station == "FeedLift":
            return self._validate_feedlift(code)
        elif station == "Develop":
            target = int(self._read("Expand_Target_Tank", 0) or 0)
            if not 1 <= target <= 8:
                return "error", 500, 90, False
        elif station == "PhotoScrape":
            return self._validate_photoscrape(code)
        return None

    def _validate_feedlift(self, code: int) -> tuple[str, int, int, bool] | None:
        """复刻 FeedLift 前置门、搜索区间和调试轴校验。"""

        if code == 10 and not (self.world["feed_homed"] and self.world["waste_homed"]):
            return "error", 308, 90, True
        if code == 91:
            if int(self._read("FeedLift_DebugAxis", 0) or 0) not in {1, 2}:
                return "error", 306, 90, True
            return None
        if code not in {11, 13, 21, 22}:
            return None
        axis = 1 if code in {11, 13} else 2
        low = float(self._read(f"FeedLift_{axis}Z_SearchLowTarget", 0.0) or 0.0)
        high = float(self._read(f"FeedLift_{axis}Z_SearchHighTarget", 0.0) or 0.0)
        if low >= high:
            return "error", 303, 90, True
        if code in {11, 13} and (
            not self.world["feed_homed"] or self.world["feed_count"] <= 0
        ):
            return "error", 301, 90, True
        if code == 21 and (
            not self.world["waste_homed"]
            or self.world["waste_count"] >= self.world["capacity"]
        ):
            return "error", 302, 90, True
        if code == 22 and (
            not self.world["waste_homed"] or self.world["waste_count"] <= 0
        ):
            return "error", 302, 90, True
        target = self._feedlift_target(code)
        if target < low or target > high:
            error = {11: 304, 13: 307, 21: 305, 22: 305}[code]
            return "error", error, 90, True
        return None

    def _validate_photoscrape(self, code: int) -> tuple[str, int, int, bool] | None:
        constants = self.contracts["PhotoScrape"].constants
        shade_upper = not self.sensors.cylinder_feedback("photoscrape_shade")
        if code in {42, 43} and not shade_upper:
            return "error", 425, 90, True
        if code == 42:
            if float(self._read("PhotoScrape_10Z_ActPos", 0.0) or 0.0) >= float(
                constants.get("ALIGN_Z_XY_GATE", 6.0)
            ):
                return "error", 421, 90, True
            x = float(self._read("PhotoScrape_Align_TargetX", 0.0) or 0.0)
            y = float(self._read("PhotoScrape_Align_TargetY", 0.0) or 0.0)
            if not self._inside_alignment_window(x, y):
                return "error", 422, 90, True
        if code == 44:
            z = float(self._read("PhotoScrape_Align_TargetZ", 0.0) or 0.0)
            if not 0.0 <= z <= float(constants.get("ALIGN_Z_CHECK_MAX", 18.0)):
                return "error", 421, 90, True
            if z > 0 and not self._inside_alignment_window(
                float(self._read("PhotoScrape_9X_ActPos", 0.0) or 0.0),
                float(self._read("PhotoScrape_8Y_ActPos", 0.0) or 0.0),
            ):
                return "error", 424, 90, True
        return None

    def _inside_alignment_window(self, x: float, y: float) -> bool:
        constants = self.contracts["PhotoScrape"].constants
        return float(constants.get("ALIGN_X_WIN_MIN", 0.0)) <= x <= float(
            constants.get("ALIGN_X_WIN_MAX", -1.0)
        ) and float(constants.get("ALIGN_Y_WIN_MIN", 0.0)) <= y <= float(
            constants.get("ALIGN_Y_WIN_MAX", -1.0)
        )

    def _duration(self, station: str, code: int, fallback: float) -> float:
        action_delays = dict(self.config.get("action_delay_ms", {}))
        configured = dict(action_delays.get(station, {})).get(str(code))
        if configured is not None:
            return max(float(configured), 0.0) / 1000.0
        action = self.contracts[station].action(code)
        if action is not None and action.kind == "instant":
            return 0.0
        if station == "Collect" and code == 30:
            constants = self.contracts[station].constants
            count = max(1, int(self._read("collect_count", 1) or 1))
            return count * sum(
                float(constants.get(name, 0.0))
                for name in ("a30_query_delay_s", "a30_drain_s", "a30_settle_s")
            )
        if station == "Develop":
            constants = self.contracts[station].constants
            if code == 20:
                return float(constants.get("a20_first_query_delay_s", 1.0)) + float(
                    constants.get("a20_settle_after_pump_s", 3.0)
                )
            if code == 21:
                count = max(1, int(self._read("Expand_rinse_count", 1) or 1))
                return count * float(
                    constants.get("a21_first_query_delay_s", 0.1)
                ) + float(constants.get("a21_settle_after_pump_s", 5.0))
            if code == 22:
                count = max(1, int(self._read("Expand_up_liquid_count", 1) or 1))
                return count * float(constants.get("a22_first_query_delay_s", 0.5))
            if code == 26:
                return (
                    max(0.0, float(self._read("Tank_Suction_Settle_S", 3.0) or 0.0))
                    + max(0.0, float(self._read("Tank_Suction_Empty_S", 10.0) or 0.0))
                    + max(0.0, float(self._read("Tank_Suction_Blow_S", 30.0) or 0.0))
                )
        if station == "Sampling":
            poll = float(
                self.contracts[station].constants.get("pump_poll_interval_s", 0.5)
            )
            if code == 20:
                count = max(1, int(self._read("Sampling_clean_count", 1) or 1))
                return poll * (
                    2
                    if int(self._read("Sampling_clean_mode", 0) or 0) == 1
                    else 3 * count
                )
            if code == 55:
                return poll * (
                    3 + max(1, int(self._read("Sampling_rinse_mix_count", 1) or 1))
                )
            if code in {40, 50, 60}:
                return poll * 2
            if code == 62:
                dry = max(1, int(self._read("Sampling_band_dry_cycles", 1) or 1))
                return max(fallback, dry * self.phase_s * 2 + 0.2)
        if station == "FeedLift":
            constants = self.contracts[station].constants
            if code == 91:
                return float(constants.get("debug_stable_ms", 200)) / 1000.0
            if code in {11, 13, 21, 22}:
                actual = float(
                    self._read(
                        "FeedLift_1Z_ActPos"
                        if code in {11, 13}
                        else "FeedLift_2Z_ActPos",
                        0.0,
                    )
                    or 0.0
                )
                return (
                    abs(self._feedlift_target(code) - actual) / self.jog_speed
                    + float(constants.get("stable_confirm_ms", 300)) / 1000.0
                )
        if station == "PhotoScrape" and code == 40:
            return self.cnc_s
        if action is not None and action.kind == "cylinder":
            return max(fallback, self.cylinder_s)
        phase_duration = self.phase_s * max(len(action.steps) - 1, 0) if action else 0.0
        return max(fallback, phase_duration)

    def _motion_for(self, station: str, code: int) -> tuple[MotionSegment, ...]:
        speed = max(
            float(dict(self.config.get("motion_speed", {})).get(station, 100.0)), 0.001
        )
        segments: list[MotionSegment] = []
        cursor = 0.0
        positions: dict[str, float] = {}

        def move(
            actual: str, target: float, *, move_speed: float | None = None
        ) -> None:
            nonlocal cursor
            start = positions.get(actual, float(self._read(actual, 0.0) or 0.0))
            duration = abs(float(target) - start) / max(move_speed or speed, 0.001)
            segments.append(
                MotionSegment(actual, start, float(target), cursor, duration)
            )
            positions[actual] = float(target)
            cursor += duration

        if station == "Sampling":
            z_down = float(self._read("Sampling_5Z_Target", 45.0) or 45.0)
            if code == 10:
                move("Sampling_5Z_ActPos", 0.0)
                for actual in (
                    "Sampling_4X_ActPos",
                    "Spot_6X_ActPos",
                    "Spot_7Y_ActPos",
                ):
                    move(actual, 0.0)
            elif code == 20:
                move("Sampling_5Z_ActPos", 0.0)
                move(
                    "Sampling_4X_ActPos",
                    float(self._read("Sampling_4X_WashTarget", 0.0) or 0.0),
                )
                move("Sampling_5Z_ActPos", z_down)
            elif code in {31, 61}:
                key = "sampling_place_7y" if code == 31 else "sampling_spray_7y"
                target = float(
                    dict(
                        dict(self.config.get("plant", {})).get("nominal_positions", {})
                    ).get(key, self._read("Spot_7Y_Target", 0.0) or 0.0)
                )
                move("Spot_7Y_ActPos", target)
            elif code == 40:
                move("Sampling_5Z_ActPos", 0.0)
            elif code in {50, 55}:
                move("Sampling_5Z_ActPos", 0.0)
                move(
                    "Sampling_4X_ActPos",
                    float(self._read("Sampling_4X_Target", 0.0) or 0.0),
                )
                move(
                    "Sampling_3Y_ActPos",
                    float(self._read("Sampling_3Y_Target", 0.0) or 0.0),
                )
                move("Sampling_5Z_ActPos", z_down)
                if code == 55:
                    move("Sampling_5Z_ActPos", 0.0)
                    move("Sampling_5Z_ActPos", z_down)
                move("Sampling_5Z_ActPos", 0.0)
            elif code in {60, 62}:
                move(
                    "Spot_6X_ActPos",
                    float(self._read("Spot_6X_StartTarget", 0.0) or 0.0),
                )
                move("Spot_7Y_ActPos", float(self._read("Spot_7Y_Target", 0.0) or 0.0))
                move(
                    "Spot_6X_ActPos", float(self._read("Spot_6X_EndTarget", 0.0) or 0.0)
                )
        elif station == "PhotoScrape":
            constants = self.contracts[station].constants
            if code in {10, 43}:
                move("PhotoScrape_10Z_ActPos", 0.0)
                move(
                    "PhotoScrape_9X_ActPos",
                    float(constants.get("cam_x335_target", 335.0)),
                )
                move("Photo_8Y_ActPos", 0.0)
            elif code == 31:
                move(
                    "PhotoScrape_9X_ActPos",
                    float(constants.get("cam_x335_target", 335.0)),
                )
            elif code == 34:
                move(
                    "Photo_8Y_ActPos", float(self._read("Photo_8Y_Target", 0.0) or 0.0)
                )
            elif code == 35:
                move("Photo_8Y_ActPos", 0.0)
            elif code == 42:
                move(
                    "PhotoScrape_9X_ActPos",
                    float(self._read("PhotoScrape_Align_TargetX", 0.0) or 0.0),
                    move_speed=40.0,
                )
                move(
                    "Photo_8Y_ActPos",
                    float(self._read("PhotoScrape_Align_TargetY", 0.0) or 0.0),
                    move_speed=40.0,
                )
            elif code == 44:
                move(
                    "PhotoScrape_10Z_ActPos",
                    float(self._read("PhotoScrape_Align_TargetZ", 0.0) or 0.0),
                    move_speed=5.0,
                )
        elif station == "FeedLift":
            if code in {11, 12, 13}:
                target = (
                    float(self._read("FeedLift_1Z_ActPos", 0.0) or 0.0) - 5.0
                    if code == 12
                    else self._feedlift_target(code)
                )
                move("FeedLift_1Z_ActPos", target, move_speed=self.jog_speed)
            elif code in {21, 22}:
                move(
                    "FeedLift_2Z_ActPos",
                    self._feedlift_target(code),
                    move_speed=self.jog_speed,
                )
        elif station == "Rail" and code == 10:
            position = int(self._read("Rail_Target_Position", 0) or 0)
            targets = list(self._read("Rail_Pos_Target", []))
            if 1 <= position <= len(targets):
                move("Rail_ActPos", float(targets[position - 1]))
        return tuple(segments)

    def _feedlift_target(self, code: int) -> float:
        magazine = "feed" if code in {11, 13} else "waste"
        count = int(self.world[f"{magazine}_count"])
        calibration = self.feedlift_calibration[magazine]
        trigger = calibration["z_empty_mm"] - count * calibration["pitch_mm"]
        return trigger if code in {11, 21} else trigger - 0.5

    def _finish_sampling(self, code: int) -> None:
        cylinders = self.world["cylinders"]
        if code == 32:
            cylinders["sampling_locator"] = True
        elif code == 33:
            cylinders["sampling_locator"] = False
        elif code == 50:
            instructions = list(self._read("Sampling_sample_instructions", []))
            match = self._P_VALUE.search(str(instructions[0])) if instructions else None
            if match:
                self.world["pump_position"]["sampling"] += int(match.group(1))
        elif code == 60:
            cylinders["sampling_locator"] = False
        elif code == 62:
            self.world["pump_position"]["sampling"] = int(
                self._read("Sampling_band_end_position", 0) or 0
            )

    def _finish_collect(self, code: int) -> None:
        cylinders = self.world["cylinders"]
        if code == 10:
            for key in (
                "collect_press",
                "collect_clamp",
                "collect_lift",
                "collect_extend",
                "collect_fill",
                "collect_drain",
            ):
                cylinders[key] = False
        elif code == 21:
            cylinders["collect_clamp"] = True
        elif code == 22:
            cylinders["collect_extend"] = True
        elif code == 23:
            cylinders.update(
                {"collect_extend": False, "collect_lift": True, "collect_press": True}
            )
        elif code == 24:
            cylinders["collect_bottle_locator"] = bool(
                self._read("Collect_BottleLocate_Target", False)
            )
        elif code == 30:
            cylinders.update({"collect_fill": False, "collect_drain": False})
            self._apply_pump_instruction(
                "collect", str(self._read("collect_forward_instructions", ""))
            )
        elif code == 41:
            cylinders.update(
                {"collect_press": False, "collect_lift": False, "collect_extend": True}
            )
        elif code == 42:
            cylinders["collect_extend"] = False
        elif code == 43:
            cylinders["collect_clamp"] = False

    def _finish_develop(self, code: int) -> None:
        target = int(self._read("Expand_Target_Tank", 1) or 1)
        prefix = f"tank_{target}"
        cylinders = self.world["cylinders"]
        outputs = self.world["outputs"]
        if code == 10:
            cylinders[prefix] = False
            outputs.update(
                {
                    f"{prefix}_inlet": False,
                    f"{prefix}_drain": False,
                    f"{prefix}_blow": False,
                }
            )
        elif code == 20:
            self._apply_pump_instruction(
                self._develop_pump_key(target),
                str(self._read("Expand_forward_instructions", "")),
            )
        elif code == 21:
            cylinders[prefix] = True
            outputs.update({f"{prefix}_inlet": True, f"{prefix}_drain": True})
            group = 1 if target <= 4 else 2
            self._write(f"Expand_Waste_Empty_G{group}", False)
            self._apply_pump_instruction(
                self._develop_pump_key(target),
                str(self._read("Expand_forward_instructions", "")),
            )
        elif code == 22:
            outputs[f"{prefix}_inlet"] = False
            self._apply_pump_instruction(
                self._develop_pump_key(target),
                str(self._read("Expand_forward_instructions", "")),
            )
        elif code == 26:
            outputs.update(
                {
                    f"{prefix}_inlet": False,
                    f"{prefix}_drain": False,
                    f"{prefix}_blow": False,
                }
            )
        elif code == 31:
            cylinders[prefix] = False
        elif code == 32:
            cylinders[prefix] = True

    def _finish_photoscrape(self, code: int) -> None:
        cylinders = self.world["cylinders"]
        outputs = self.world["outputs"]
        if code == 10:
            for key in (
                "photoscrape_locator",
                "powder_collector_locator",
                "photoscrape_shade",
                "photoscrape_rotate",
                "photoscrape_press",
            ):
                cylinders[key] = False
            outputs.update({"photoscrape_vacuum": False, "photoscrape_motor": False})
        elif code == 32:
            cylinders["photoscrape_locator"] = bool(
                self._read("PhotoScrape_CamLocate_Target", False)
            )
        elif code == 33:
            cylinders["photoscrape_press"] = bool(
                self._read("PhotoScrape_CamPress_Target", False)
            )
        elif code == 34:
            cylinders["photoscrape_shade"] = True
        elif code == 35:
            cylinders["photoscrape_shade"] = False
        elif code == 36:
            cylinders["powder_collector_locator"] = bool(
                self._read("PhotoScrape_PowderCollectorLocate_Target", False)
            )
        elif code == 40:
            outputs.update({"photoscrape_vacuum": True, "photoscrape_motor": True})
        elif code == 41:
            outputs.update({"photoscrape_vacuum": False, "photoscrape_motor": False})
            cylinders["photoscrape_rotate"] = True
        elif code == 51:
            cylinders["photoscrape_press"] = False
        elif code == 52:
            cylinders["photoscrape_rotate"] = False

    def _apply_pump_instruction(self, key: str, instruction: str) -> None:
        absolute = self._A_VALUE.search(instruction)
        relative = self._P_VALUE.search(instruction)
        if absolute:
            self.world["pump_position"][key] = int(absolute.group(1))
        elif relative:
            self.world["pump_position"][key] += int(relative.group(1))

    @staticmethod
    def _develop_pump_key(target_tank: int) -> str:
        return "develop_1" if target_tank <= 4 else "develop_2"

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
