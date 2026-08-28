"""PTLC V2 OPC UA / L2 握手仿真代理。

该进程只依赖 PLC-Sim 内置协议快照，不导入或修改 PTLC 仓库。它监视八个
``<Station>_L2_*`` 通道，模拟接单、运行、完成、复位以及少量可配置的设备副作用。
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any

try:
    from .ptlc_behavior import StationContract, load_behavior_contracts
    from .ptlc_deploy import step_deploy
    from .ptlc_effects import (
        all_effects,
        apply_effect,
        apply_process_effects,
        effect_names,
        effects_for,
        fault_codes,
    )
    from .ptlc_plant import PtlcPlant
    from .ptlc_runtime import (
        INPUT_FIELDS,
        INSTANT_ACTIONS,
        MODELED_ACTIONS,
        OUTPUT_DEFAULTS,
        STATIONS,
        TERMINAL_STATES,
        HandshakeEvent,
        OpcUaVariableAdapter,
        RuntimeFaults,
        VariableAdapter,
    )
    from .ptlc_runtime import ActionCycle as _Cycle
    from .ptlc_runtime import DeployCycle as _DeployCycle
except ImportError:  # Direct `python ptlc_handshake_agent.py` compatibility.
    from ptlc_behavior import StationContract, load_behavior_contracts
    from ptlc_deploy import step_deploy
    from ptlc_effects import (
        all_effects,
        apply_effect,
        apply_process_effects,
        effect_names,
        effects_for,
        fault_codes,
    )
    from ptlc_plant import PtlcPlant
    from ptlc_runtime import (
        INPUT_FIELDS,
        INSTANT_ACTIONS,
        MODELED_ACTIONS,
        OUTPUT_DEFAULTS,
        STATIONS,
        TERMINAL_STATES,
        HandshakeEvent,
        OpcUaVariableAdapter,
        RuntimeFaults,
        VariableAdapter,
    )
    from ptlc_runtime import ActionCycle as _Cycle
    from ptlc_runtime import DeployCycle as _DeployCycle


__all__ = [
    "OUTPUT_DEFAULTS",
    "HandshakeEvent",
    "OpcUaVariableAdapter",
    "PtlcHandshakeSimulator",
    "RuntimeFaults",
    "main",
]


class PtlcHandshakeSimulator:
    """同步、可注入时钟的 PTLC L2 状态机，便于单元测试和独立进程复用。"""

    def __init__(
        self,
        adapter: VariableAdapter,
        *,
        config: Mapping[str, Any] | None = None,
        delay_s: float = 0.2,
        stations: tuple[str, ...] = STATIONS,
        contracts: Mapping[str, StationContract] | None = None,
        plant: PtlcPlant | None = None,
    ) -> None:
        """创建状态机；参数为变量端口、配置、延时、工位和可选契约，无返回值。"""

        self.adapter = adapter
        self.config = dict(config or {})
        configured = tuple(str(item) for item in self.config.get("stations", stations))
        unknown = sorted(set(configured) - set(STATIONS))
        if unknown:
            raise ValueError(f"未知 PTLC L2 工位: {', '.join(unknown)}")
        self.stations = configured
        self.delay_s = max(float(delay_s), 0.0)
        self.contracts = dict(contracts or load_behavior_contracts())
        missing_contracts = sorted(set(self.stations) - set(self.contracts))
        if missing_contracts:
            raise ValueError(f"缺少 PTLC 工位行为契约: {', '.join(missing_contracts)}")
        self.plant = plant or PtlcPlant(adapter, self.contracts, self.config)
        self._previous_start = {station: False for station in self.stations}
        self._cycles: dict[str, _Cycle] = {}
        self._previous_deploy_start = False
        self._deploy_cycle: _DeployCycle | None = None
        self.runtime_faults = RuntimeFaults()
        # 兼容旧快照键名；PLC 过程状态的唯一真源已经收敛到 PtlcPlant。
        self.process_state = self.plant.world
        self.events: list[dict[str, Any]] = []

    @staticmethod
    def node(station: str, field: str) -> str:
        """拼接 L2 节点名；参数为工位和字段，返回 BrowseName。"""

        return f"{station}_L2_{field}"

    def contract_names(self) -> tuple[str, ...]:
        """返回状态机需要访问的全部节点名；无参数。"""

        names = {
            self.node(station, field)
            for station in self.stations
            for field in (*INPUT_FIELDS, *OUTPUT_DEFAULTS)
        }
        names.update(str(name) for name in self.config.get("initial_values", {}))
        names.update(
            (
                "PLC_Ready",
                "PLC_Axis_CommOperational",
                "PLC_Deploy_RequestSeq",
                "PLC_Deploy_CommitSeq",
                "PLC_Deploy_Start",
                "PLC_Deploy_Reset",
                "PLC_Deploy_State",
                "PLC_Deploy_AcceptedSeq",
                "PLC_Deploy_ErrorCode",
                "Pump_Vacuum_On",
            )
        )
        names.update(self.plant.required_nodes())
        for effect in all_effects(self.config):
            names.update(effect_names(effect))
        for items in dict(self.config.get("motion_effects", {})).values():
            for item in items or ():
                if isinstance(item, Mapping):
                    names.update((str(item.get("from", "")), str(item.get("to", ""))))
        names.discard("")
        return tuple(sorted(names))

    def initialize(self, *, reset_outputs: bool = False) -> None:
        """初始化仿真初值并从 OPC 状态恢复边沿观察基线。

        参数：``reset_outputs`` 仅供新建隔离服务器显式清空 L2 输出；默认保留现有
        握手事实，防止代理重启把保持为高电平的非幂等请求当成新请求。
        返回：无。缺少可选节点时按最小耦合原则降级。
        """

        if reset_outputs:
            for station in self.stations:
                for field, value in OUTPUT_DEFAULTS.items():
                    try:
                        self.adapter.write(self.node(station, field), value)
                    except KeyError:
                        continue
        for name, value in dict(self.config.get("initial_values", {})).items():
            try:
                self.adapter.write(str(name), value)
            except KeyError:
                continue
        for station in self.stations:
            try:
                self._previous_start[station] = bool(
                    self.adapter.read(self.node(station, "Start"))
                )
            except KeyError:
                self._previous_start[station] = False
        try:
            self._previous_deploy_start = bool(self.adapter.read("PLC_Deploy_Start"))
        except KeyError:
            self._previous_deploy_start = False
        self.plant.sync_inputs()

    def cleanup(self) -> None:
        """停止代理管理的物理输出，同时保留 L2 终态与序号事实。

        参数和返回值均无。活动周期只存在于进程内，退出时丢弃；远端握手事实留给
        下一代理实例恢复边沿基线，避免保持 Start 时重放非幂等动作。
        """

        for cycle in self._cycles.values():
            self.plant.cancel(cycle.plant_action)
        self._cycles.clear()
        try:
            self.adapter.write("Pump_Vacuum_On", False)
            self.process_state["vacuum_on"] = False
        except KeyError:
            pass
        try:
            enables = list(self.adapter.read("Tank_Drain_Enable"))
            self.adapter.write("Tank_Drain_Enable", [False] * len(enables))
        except (KeyError, TypeError):
            pass

    def snapshot(self) -> dict[str, Any]:
        """返回活动周期、进程、故障和最近事件的可序列化快照。"""

        return {
            "active_cycles": {
                station: {
                    "action_code": cycle.action_code,
                    "request_seq": cycle.request_seq,
                    "outcome": cycle.outcome,
                }
                for station, cycle in self._cycles.items()
            },
            "deploy_active": self._deploy_cycle is not None,
            "process": dict(self.process_state),
            "plant": self.plant.snapshot(),
            "faults": self.runtime_faults.snapshot(),
            "events": self.events[-200:],
        }

    def _record(self, event: HandshakeEvent) -> None:
        """追加握手事件并限制历史长度；参数为事件，返回无。"""

        self.events.append(
            {
                "station": event.station,
                "phase": event.phase,
                "action_code": event.action_code,
                "request_seq": event.request_seq,
            }
        )
        if len(self.events) > 1000:
            del self.events[:-500]

    def check(self) -> list[str]:
        """读取契约节点完成兼容性检查；返回缺失节点名列表。"""

        missing: list[str] = []
        for name in self.contract_names():
            try:
                self.adapter.read(name)
            except (KeyError, RuntimeError):
                missing.append(name)
        return missing

    def _station_delay(self, station: str, code: int) -> float:
        """返回动作的仿真持续时间，显式配置优先于行为快照常量。

        参数：工位名和动作码。
        返回：秒数；Collect A30 会按请求轮数累计查询、排液和沉淀时间。
        """

        action_delays = dict(self.config.get("action_delay_ms", {}))
        station_delays = dict(action_delays.get(station, {}))
        if str(code) in station_delays:
            return max(float(station_delays[str(code)]), 0.0) / 1000.0
        if station == "Collect" and code == 30:
            constants = self.contracts[station].constants
            try:
                count = max(1, int(self.adapter.read("collect_count")))
            except (KeyError, TypeError, ValueError):
                count = 1
            per_cycle = sum(
                max(float(constants.get(name, 0.0)), 0.0)
                for name in ("a30_query_delay_s", "a30_drain_s", "a30_settle_s")
            )
            return count * per_cycle
        delays = dict(self.config.get("station_delay_ms", {}))
        if station in delays:
            return max(float(delays[station]), 0.0) / 1000.0
        return self.delay_s

    @staticmethod
    def _publishes_public_step(station: str, code: int) -> bool:
        """判断现役 PLC 是否把动作内部相位发布到公开 L2 Step。

        参数：``station`` 是 L2 工位前缀，``code`` 是已锁存动作码。
        返回：需要发布公开步骤时为真；静默工位保持 Step=0。
        """

        return (
            station == "StagingA"
            or station == "FeedLift"
            and code != 12
            or station == "Develop"
            and code in {50, 51}
            or station == "Sampling"
            and code == 62
        )

    def _validate_action(
        self, station: str, code: int
    ) -> tuple[str, int, int, bool] | None:
        """复刻能由 flat OPC UA 参数确定的受理门；返回终态近似或 None。"""
        try:
            if station == "Rail" and code == 10:
                position = int(self.adapter.read("Rail_Target_Position"))
                if not 1 <= position <= 6:
                    return "rejected", 101, 0, True
                targets = list(self.adapter.read("Rail_Pos_Target"))
                target = float(targets[position - 1])
                if not 0.0 < target <= 3000.0:
                    return "rejected", 102, 0, True
            elif station == "Sampling" and code == 55:
                count = int(self.adapter.read("Sampling_rinse_mix_count"))
                instructions = list(
                    self.adapter.read("Sampling_rinse_mix_instructions")
                )
                if (
                    not 1 <= count <= 20
                    or len(instructions) != 4
                    or not all(str(value).strip() for value in instructions)
                ):
                    return "error", 466, 90, False
            elif station == "Collect" and code == 22:
                if int(self.adapter.read("IX8")) & (1 << 1):
                    return "collect_wait_empty", 0, 10, False
            elif station == "Collect" and code == 23:
                return "collect_check_bottle", 0, 10, False
            elif station == "FeedLift" and code == 91:
                if int(self.adapter.read("FeedLift_DebugAxis")) not in {1, 2}:
                    return "error", 306, 90, False
        except (KeyError, IndexError, TypeError, ValueError):
            # 旧快照缺参数时由 contract check 报漂移，运行路径保持可降级。
            return None
        return None

    def _reset_station(self, station: str, state: int, action_code: int) -> str:
        """按现役工位派发器语义处理 Reset。

        参数：``station`` 是工位前缀，``state`` 与 ``action_code`` 是本扫描锁存值。
        返回：供事件流发布的 ``reset`` 或 ``interrupted`` 阶段名。
        序号是已发生的握手事实，任何 Reset 都不得清除。
        """

        old_cycle = self._cycles.pop(station, None)
        self.plant.cancel(old_cycle.plant_action if old_cycle is not None else None)
        if state == 10 and station == "StagingA":
            self.adapter.write(self.node(station, "State"), 50)
            self.adapter.write(self.node(station, "ErrorCode"), 402)
            self.adapter.write(self.node(station, "Retryable"), False)
            return "interrupted"
        if state == 10 and station == "Sampling" and action_code == 55:
            self.adapter.write(self.node(station, "State"), 50)
            self.adapter.write(self.node(station, "SafeState"), 90)
            self.adapter.write(self.node(station, "Retryable"), False)
            return "interrupted"

        self.adapter.write(self.node(station, "State"), 0)
        self.adapter.write(self.node(station, "Step"), 0)
        self.adapter.write(self.node(station, "ErrorCode"), 0)
        self.adapter.write(self.node(station, "SafeState"), 0)
        self.adapter.write(self.node(station, "Retryable"), False)
        if station == "StagingA":
            self.adapter.write(self.node(station, "ActiveCode"), 0)
        return "reset"

    def _start_cycle(self, station: str, now: float) -> tuple[_Cycle, HandshakeEvent]:
        """受理一个动作；参数为工位和时钟，返回活动周期及受理事件。"""

        code = int(self.adapter.read(self.node(station, "ActionCode")))
        seq = int(self.adapter.read(self.node(station, "RequestSeq")))
        ready = bool(self.adapter.read("PLC_Ready"))
        deploy_state = int(self.adapter.read("PLC_Deploy_State"))
        contract = self.contracts[station]
        runtime_outcome = self.runtime_faults.outcome(station, code)
        error_code = 0
        safe_state = 10
        retryable = False
        if not ready or deploy_state != 0:
            outcome, error_code, safe_state, retryable = "global_reject", 190, 0, True
        elif code not in contract.accepts:
            outcome = "unknown"
            error_code, safe_state, retryable = contract.unknown_code_error, 0, True
        elif runtime_outcome:
            outcome = runtime_outcome
        elif (validation := self._validate_action(station, code)) is not None:
            outcome, error_code, safe_state, retryable = validation
        elif code in fault_codes(self.config, station, "reject_codes"):
            outcome = "rejected"
        elif code in fault_codes(self.config, station, "error_codes"):
            outcome = "error"
        elif code in fault_codes(self.config, station, "hang_codes"):
            outcome = "hang"
        else:
            outcome = "done" if code in MODELED_ACTIONS[station] else "unmodeled"
        if outcome in {"rejected", "reject"}:
            error_code, safe_state, retryable = error_code or 102, 0, True
        elif outcome == "error":
            error_code, safe_state = error_code or 201, 90
        elif outcome == "interrupt":
            error_code, safe_state = 202, 90
        action = contract.action(code)
        plant_action = None
        if outcome == "done":
            plant_action = self.plant.begin(
                station, code, now, self._station_delay(station, code)
            )
            outcome = plant_action.outcome
            error_code = plant_action.error_code
            safe_state = plant_action.safe_state
            retryable = plant_action.retryable
        elif outcome == "collect_check_bottle":
            # A23 的缩回过程立即开始，瓶传感器只在动作计时结束时判定。
            plant_action = self.plant.begin(
                station, code, now, self._station_delay(station, code)
            )
        motion = plant_action.motion if plant_action is not None else ()
        motion_duration = max(
            (segment.starts_after + segment.duration for segment in motion), default=0.0
        )
        delay = max(
            plant_action.duration
            if plant_action is not None
            else self._station_delay(station, code),
            motion_duration,
        )
        if station == "Sampling" and code == 10 and outcome == "done":
            pump_poll = max(
                float(
                    self.contracts[station].constants.get("pump_poll_interval_s", 0.0)
                ),
                0.0,
            )
            delay = motion_duration + pump_poll
        if outcome in {"collect_wait_empty", "collect_check_bottle"}:
            gate_due = self.plant.request_gate_resolution(station, code, now)
            if outcome == "collect_wait_empty":
                delay = float("inf") if gate_due is None else max(gate_due - now, 0.0)
            elif gate_due is not None:
                delay = max(delay, gate_due - now)
        if outcome == "unmodeled":
            delay = float("inf")
        if outcome in {"global_reject", "rejected", "reject", "unknown"}:
            # 确定性受理门失败在同一 PLC 扫描进入 REJECTED，不能伪造 RUNNING 窗口。
            delay = 0.0
        elif (
            outcome == "done"
            and action is not None
            and (action.kind == "instant" or (station, code) in INSTANT_ACTIONS)
        ):
            # 真 PLC 的内联写位动作在受理扫描内完成，不受仿真默认延时影响。
            delay = 0.0
        steps = action.steps if action is not None else ()
        if station == "Develop" and code in (50, 51) and outcome == "done":
            try:
                tank_state = self._target_tank_state()
            except KeyError:
                # 精简测试/旧快照没有 Tank 辅助节点时，保留原有 action_effects 近似。
                outcome = "done"
            except (IndexError, TypeError, ValueError):
                outcome, error_code, safe_state = "error", 500, 90
            else:
                if code == 50 and tank_state in {10, 90}:
                    outcome, error_code, safe_state, retryable = (
                        "rejected",
                        501,
                        0,
                        True,
                    )
                elif code == 51 and tank_state not in {0, 98, 99}:
                    outcome, error_code, safe_state, retryable = (
                        "rejected",
                        511,
                        0,
                        True,
                    )
                else:
                    outcome = "tank_drain" if code == 50 else "tank_release"
                    try:
                        delay = max(delay, self._prepare_tank_action(code))
                    except KeyError:
                        outcome = "done"
        cycle = _Cycle(
            code,
            seq,
            now,
            now + delay,
            outcome,
            error_code=error_code,
            safe_state=safe_state,
            retryable=retryable,
            steps=steps,
            motion=motion,
            plant_action=plant_action,
        )
        self._cycles[station] = cycle
        self.adapter.write(self.node(station, "AcceptedSeq"), seq)
        self.adapter.write(self.node(station, "ActiveCode"), code)
        initial_step = 0
        if self._publishes_public_step(station, code) and steps:
            initial_step = int(steps[0])
        self.adapter.write(self.node(station, "Step"), initial_step)
        self.adapter.write(self.node(station, "State"), 10)
        return cycle, HandshakeEvent(station, "accepted", code, seq)

    def _target_tank_state(self) -> int:
        """读取目标展缸状态；无参数，索引非法时抛错。"""

        index = int(self.adapter.read("Expand_Target_Tank")) - 1
        states = list(self.adapter.read("Tank_State"))
        if not 0 <= index < len(states):
            raise IndexError("Expand_Target_Tank 超出 1..8")
        return int(states[index])

    def _prepare_tank_action(self, code: int) -> float:
        """初始化展缸动作；参数为动作码，返回专用状态机持续秒数。"""

        index = int(self.adapter.read("Expand_Target_Tank")) - 1
        states = list(self.adapter.read("Tank_State"))
        state = int(states[index])
        if code == 50:
            if state in {98, 99}:
                return 0.0
            states[index] = 50
            enables = list(self.adapter.read("Tank_Drain_Enable"))
            dones = list(self.adapter.read("Tank_Drain_Done"))
            cap_hits = list(self.adapter.read("Tank_Drain_CapHit"))
            enables[index], dones[index], cap_hits[index] = True, False, False
            self.adapter.write("Tank_State", states)
            self.adapter.write("Tank_Drain_Enable", enables)
            self.adapter.write("Tank_Drain_Done", dones)
            self.adapter.write("Tank_Drain_CapHit", cap_hits)
            drain = max(0.0, float(self.adapter.read("Tank_Drain_S")))
            cap = max(0.0, float(self.adapter.read("Tank_Drain_Cap_S")))
            blow = max(0.0, float(self.adapter.read("Tank_Blow_S")))
            dry = max(0.0, float(self.adapter.read("Tank_Dry_S")))
            phase_a = min(drain, cap) if cap > 0 else drain
            return phase_a + blow + dry
        return 0.0

    def _progress_cycle(self, station: str, cycle: _Cycle, now: float) -> None:
        """推进动作的门禁、公开步骤、轴位置及专用子状态机。

        参数：工位、活动周期和当前单调时钟。
        返回：无；满足动态门禁时会从冻结态进入可计时执行态。
        """

        self.plant.advance(now)
        if cycle.outcome == "collect_wait_empty":
            try:
                occupied = bool(int(self.adapter.read("IX8")) & (1 << 1))
            except (KeyError, TypeError, ValueError):
                occupied = True
            if occupied:
                return
            cycle.outcome = "done"
            cycle.started_at = now
            cycle.plant_action = self.plant.begin(
                station,
                cycle.action_code,
                now,
                self._station_delay(station, cycle.action_code),
            )
            cycle.outcome = cycle.plant_action.outcome
            cycle.error_code = cycle.plant_action.error_code
            cycle.safe_state = cycle.plant_action.safe_state
            cycle.retryable = cycle.plant_action.retryable
            cycle.motion = cycle.plant_action.motion
            cycle.due_at = now + cycle.plant_action.duration
        elif cycle.outcome == "collect_check_bottle" and now >= cycle.due_at - 1e-9:
            try:
                bottle_present = bool(int(self.adapter.read("IX8")) & (1 << 1))
            except (KeyError, TypeError, ValueError):
                bottle_present = False
            if bottle_present:
                if cycle.plant_action is None:
                    cycle.plant_action = self.plant.begin(
                        station,
                        cycle.action_code,
                        cycle.started_at,
                        self._station_delay(station, cycle.action_code),
                    )
                cycle.outcome = cycle.plant_action.outcome
                cycle.error_code = cycle.plant_action.error_code
                cycle.safe_state = cycle.plant_action.safe_state
                cycle.retryable = cycle.plant_action.retryable
                cycle.motion = cycle.plant_action.motion
            else:
                cycle.outcome = "error"
                cycle.error_code = 201
                cycle.safe_state = 90
                cycle.retryable = True
        duration = max(cycle.due_at - cycle.started_at, 0.0)
        fraction = (
            1.0
            if duration == 0
            else min(max((now - cycle.started_at) / duration, 0.0), 1.0)
        )
        if cycle.steps and self._publishes_public_step(station, cycle.action_code):
            index = min(int(fraction * len(cycle.steps)), len(cycle.steps) - 1)
            if index != cycle.last_step_index:
                self.adapter.write(self.node(station, "Step"), cycle.steps[index])
                cycle.last_step_index = index
        if cycle.plant_action is not None:
            self.plant.progress(cycle.plant_action, now)
        if cycle.outcome == "tank_drain":
            self._progress_tank_drain(cycle, now)

    def _progress_tank_drain(self, cycle: _Cycle, now: float) -> None:
        """推进展缸排液；参数为活动周期和时钟，返回无。"""

        index = int(self.adapter.read("Expand_Target_Tank")) - 1
        states = list(self.adapter.read("Tank_State"))
        if int(states[index]) == 90:
            cycle.outcome, cycle.error_code, cycle.safe_state = "error", 502, 90
            cycle.due_at = now
            return
        if int(states[index]) in {98, 99}:
            cycle.due_at = min(cycle.due_at, now)
            return
        drain = max(0.0, float(self.adapter.read("Tank_Drain_S")))
        cap = max(0.0, float(self.adapter.read("Tank_Drain_Cap_S")))
        blow = max(0.0, float(self.adapter.read("Tank_Blow_S")))
        phase_a = min(drain, cap) if cap > 0 else drain
        elapsed = max(0.0, now - cycle.started_at)
        if elapsed < phase_a:
            state = 50
        elif elapsed < phase_a + blow:
            state = 55
            if cap > 0 and cap <= drain:
                cap_hits = list(self.adapter.read("Tank_Drain_CapHit"))
                cap_hits[index] = True
                self.adapter.write("Tank_Drain_CapHit", cap_hits)
        elif now < cycle.due_at:
            state = 56
        else:
            state = 98
        if int(states[index]) != state:
            states[index] = state
            self.adapter.write("Tank_State", states)
        self.adapter.write(self.node("Develop", "Step"), state)

    def _finish_cycle(self, station: str, cycle: _Cycle) -> HandshakeEvent:
        """提交动作终态；参数为工位和周期，返回终态事件。"""

        if cycle.outcome in {"done", "tank_drain", "tank_release"}:
            try:
                if cycle.plant_action is not None:
                    cycle.plant_action.outcome = cycle.outcome
                    self.plant.finish(cycle.plant_action)
                for effect in effects_for(self.config, station, cycle.action_code):
                    apply_effect(self.adapter, effect)
                apply_process_effects(
                    self.adapter, self.process_state, station, cycle.action_code
                )
            except (KeyError, IndexError, TypeError, ValueError):
                state, error, safe, retryable, phase = 40, 500, 90, False, "error"
            else:
                state, error, safe, retryable, phase = 20, 0, 10, False, "completed"
        elif cycle.outcome in {"global_reject", "rejected", "reject", "unknown"}:
            state, error, safe, retryable, phase = (
                30,
                cycle.error_code,
                cycle.safe_state,
                cycle.retryable,
                "rejected",
            )
        elif cycle.outcome == "interrupt":
            state, error, safe, retryable, phase = (
                50,
                cycle.error_code,
                cycle.safe_state,
                cycle.retryable,
                "interrupted",
            )
        else:
            state, error, safe, retryable, phase = (
                40,
                cycle.error_code or 201,
                cycle.safe_state or 90,
                cycle.retryable,
                "error",
            )
        terminal_step = 0
        if self._publishes_public_step(station, cycle.action_code) and cycle.steps:
            terminal_step = int(cycle.steps[-1])
        self.adapter.write(self.node(station, "Step"), terminal_step)
        self.adapter.write(self.node(station, "ErrorCode"), error)
        self.adapter.write(self.node(station, "SafeState"), safe)
        self.adapter.write(self.node(station, "Retryable"), retryable)
        self.adapter.write(self.node(station, "CompletedSeq"), cycle.request_seq)
        self.adapter.write(self.node(station, "State"), state)
        self._cycles.pop(station, None)
        return HandshakeEvent(station, phase, cycle.action_code, cycle.request_seq)

    def step(self, now: float | None = None) -> list[HandshakeEvent]:
        """执行一次全工位扫描；参数为可选时钟，返回本扫描产生的事件。"""

        current = time.monotonic() if now is None else float(now)
        self.plant.advance(current)
        events: list[HandshakeEvent] = []
        try:
            self._previous_deploy_start, self._deploy_cycle = step_deploy(
                self.adapter,
                self.stations,
                self._previous_deploy_start,
                self._deploy_cycle,
                current,
                float(self.config.get("deploy_prepare_ms", 40)),
            )
        except KeyError:
            # 兼容只构造单工位字段的单元测试/旧 PTLC 快照；check() 仍会报告缺项。
            pass
        for station in self.stations:
            reset = bool(self.adapter.read(self.node(station, "Reset")))
            start = bool(self.adapter.read(self.node(station, "Start")))
            state = int(self.adapter.read(self.node(station, "State")))
            if reset:
                if state != 0 or station in self._cycles:
                    old = self._cycles.get(station)
                    action_code = (
                        old.action_code
                        if old is not None
                        else int(self.adapter.read(self.node(station, "ActiveCode")))
                    )
                    request_seq = (
                        old.request_seq
                        if old is not None
                        else int(self.adapter.read(self.node(station, "AcceptedSeq")))
                    )
                    phase = self._reset_station(station, state, action_code)
                    events.append(
                        HandshakeEvent(station, phase, action_code, request_seq)
                    )
            elif (
                station == "StagingA"
                and start
                and not self._previous_start[station]
                and state != 0
            ):
                code = int(self.adapter.read(self.node(station, "ActionCode")))
                request_seq = int(self.adapter.read(self.node(station, "RequestSeq")))
                self._cycles.pop(station, None)
                self.adapter.write(self.node(station, "Step"), 0)
                self.adapter.write(self.node(station, "ErrorCode"), 101)
                self.adapter.write(self.node(station, "SafeState"), 0)
                self.adapter.write(self.node(station, "Retryable"), True)
                self.adapter.write(self.node(station, "CompletedSeq"), request_seq)
                self.adapter.write(self.node(station, "State"), 30)
                events.append(HandshakeEvent(station, "rejected", code, request_seq))
            elif start and not self._previous_start[station] and state == 0:
                if station == "StagingA":
                    request_seq = int(
                        self.adapter.read(self.node(station, "RequestSeq"))
                    )
                    accepted_seq = int(
                        self.adapter.read(self.node(station, "AcceptedSeq"))
                    )
                    completed_seq = int(
                        self.adapter.read(self.node(station, "CompletedSeq"))
                    )
                    if request_seq <= accepted_seq or request_seq <= completed_seq:
                        self.adapter.write(self.node(station, "Step"), 0)
                        self.adapter.write(self.node(station, "ErrorCode"), 102)
                        self.adapter.write(self.node(station, "SafeState"), 0)
                        self.adapter.write(self.node(station, "Retryable"), True)
                        self.adapter.write(self.node(station, "State"), 30)
                        events.append(
                            HandshakeEvent(
                                station,
                                "rejected",
                                int(
                                    self.adapter.read(self.node(station, "ActionCode"))
                                ),
                                request_seq,
                            )
                        )
                        self._previous_start[station] = start
                        continue
                cycle, accepted = self._start_cycle(station, current)
                events.append(accepted)
                if cycle.outcome not in {"hang", "unmodeled"} and (
                    cycle.due_at <= current + 1e-9
                ):
                    self._progress_cycle(station, cycle, current)
                    events.append(self._finish_cycle(station, cycle))
            elif station in self._cycles:
                cycle = self._cycles[station]
                self._progress_cycle(station, cycle, current)
                if cycle.outcome not in {"hang", "unmodeled"} and (
                    cycle.due_at <= current + 1e-9
                ):
                    events.append(self._finish_cycle(station, cycle))
            elif not start and state in TERMINAL_STATES:
                self.adapter.write(self.node(station, "State"), 0)
                self.adapter.write(self.node(station, "Step"), 0)
                if station == "StagingA":
                    self.adapter.write(self.node(station, "ActiveCode"), 0)
                    self.adapter.write(self.node(station, "ErrorCode"), 0)
                    self.adapter.write(self.node(station, "SafeState"), 0)
                    self.adapter.write(self.node(station, "Retryable"), False)
                events.append(
                    HandshakeEvent(
                        station,
                        "rearmed",
                        int(self.adapter.read(self.node(station, "ActiveCode"))),
                        int(self.adapter.read(self.node(station, "CompletedSeq"))),
                    )
                )
            self._previous_start[station] = start
        for event in events:
            self._record(event)
        return events


def main(argv: list[str] | None = None) -> int:
    """兼容旧入口；参数为可选 argv，返回独立 CLI 的退出码。"""

    try:
        from .ptlc_agent_cli import main as cli_main
    except ImportError:
        from ptlc_agent_cli import main as cli_main
    return cli_main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
