from __future__ import annotations

from typing import Any

import pytest

from ptlc_handshake_agent import OUTPUT_DEFAULTS, PtlcHandshakeSimulator


class MemoryAdapter:
    def __init__(self, values: dict[str, Any]) -> None:
        self.values = values

    def read(self, name: str) -> Any:
        return self.values[name]

    def write(self, name: str, value: Any) -> None:
        self.values[name] = value


def _station_values(station: str) -> dict[str, Any]:
    values = {
        "PLC_Ready": True,
        "PLC_Deploy_State": 0,
        f"{station}_L2_ActionCode": 0,
        f"{station}_L2_RequestSeq": 0,
        f"{station}_L2_Start": False,
        f"{station}_L2_Reset": False,
    }
    values.update({f"{station}_L2_{key}": value for key, value in OUTPUT_DEFAULTS.items()})
    return values


def test_l2_cycle_accepts_completes_applies_rail_effect_and_rearms() -> None:
    values = {
        **_station_values("Rail"),
        "Rail_Target_Position": 2,
        "Rail_Current_Position": 0,
        "Rail_Pos_Target": [10.0, 25.5, 40.0],
        "Rail_ActPos": 0.0,
    }
    config = {
        "stations": ["Rail"],
        "station_effects": {
            "Rail": {
                "copy": [{"from": "Rail_Target_Position", "to": "Rail_Current_Position"}],
                "indexed_copy": [{
                    "from": "Rail_Pos_Target", "index": "Rail_Target_Position",
                    "index_base": 1, "to": "Rail_ActPos",
                }],
            }
        },
    }
    adapter = MemoryAdapter(values)
    sim = PtlcHandshakeSimulator(adapter, config=config, delay_s=0.1)
    sim.initialize()
    values.update({
        "Rail_L2_ActionCode": 10,
        "Rail_L2_RequestSeq": 17,
        "Rail_L2_Start": True,
    })

    assert [event.phase for event in sim.step(now=1.0)] == ["accepted"]
    assert values["Rail_L2_State"] == 10
    assert [event.phase for event in sim.step(now=1.3)] == ["completed"]
    assert values["Rail_L2_State"] == 20
    assert values["Rail_L2_CompletedSeq"] == 17
    assert values["Rail_Current_Position"] == 2
    assert values["Rail_ActPos"] == 25.5

    values["Rail_L2_Start"] = False
    assert [event.phase for event in sim.step(now=1.4)] == ["rearmed"]
    assert values["Rail_L2_State"] == 0


def test_develop_action_50_updates_true_array_slot() -> None:
    values = {
        **_station_values("Develop"),
        "Expand_Target_Tank": 3,
        "Tank_State": [0] * 8,
        "Tank_Drain_Done": [False] * 8,
    }
    config = {
        "stations": ["Develop"],
        "action_effects": {
            "Develop": {"50": {"set_index": [
                {"node": "Tank_State", "index": "Expand_Target_Tank", "index_base": 1, "value": 98},
                {"node": "Tank_Drain_Done", "index": "Expand_Target_Tank", "index_base": 1, "value": True},
            ]}}
        },
    }
    sim = PtlcHandshakeSimulator(MemoryAdapter(values), config=config, delay_s=0)
    sim.initialize()
    values.update({
        "Develop_L2_ActionCode": 50,
        "Develop_L2_RequestSeq": 4,
        "Develop_L2_Start": True,
    })

    assert [event.phase for event in sim.step(now=2.0)] == ["accepted", "completed"]
    assert values["Tank_State"][2] == 98
    assert values["Tank_Drain_Done"][2] is True


def test_fault_injection_rejects_and_reset_returns_idle() -> None:
    values = _station_values("Pump")
    config = {
        "stations": ["Pump"],
        "faults": {"Pump": {"reject_codes": [10]}},
    }
    sim = PtlcHandshakeSimulator(MemoryAdapter(values), config=config, delay_s=0)
    sim.initialize()
    values.update({
        "Pump_L2_ActionCode": 10,
        "Pump_L2_RequestSeq": 8,
        "Pump_L2_Start": True,
    })

    assert [event.phase for event in sim.step(now=3.0)] == ["accepted", "rejected"]
    assert values["Pump_L2_State"] == 30
    assert values["Pump_L2_Retryable"] is True
    assert values["Pump_L2_ErrorCode"] == 102

    values["Pump_L2_Reset"] = True
    assert [event.phase for event in sim.step(now=3.1)] == ["reset"]
    assert values["Pump_L2_State"] == 0


def test_global_ready_gate_rejects_with_protocol_error_190() -> None:
    """验证全局就绪门失败时同一扫描拒绝且不派发动作。

    参数：无；通过公开 ``step`` 接口提交 Sampling 动作。
    返回：无；断言错误码、终态和公开步骤符合 PLC 派发器契约。
    """

    values = _station_values("Sampling")
    values["PLC_Ready"] = False
    sim = PtlcHandshakeSimulator(
        MemoryAdapter(values), config={"stations": ["Sampling"]}, delay_s=0.2
    )
    sim.initialize()
    values.update({
        "Sampling_L2_ActionCode": 40,
        "Sampling_L2_RequestSeq": 3,
        "Sampling_L2_Start": True,
    })
    assert values["PLC_Ready"] is False
    assert [event.phase for event in sim.step(now=4.0)] == ["accepted", "rejected"]
    assert values["Sampling_L2_State"] == 30
    assert values["Sampling_L2_Step"] == 0
    assert values["Sampling_L2_ErrorCode"] == 190
    assert values["Sampling_L2_Retryable"] is True


def test_rail_position_gate_rejects_invalid_index_before_motion() -> None:
    values = {
        **_station_values("Rail"),
        "Rail_Target_Position": 9,
        "Rail_Pos_Target": [1.0, 2.0],
        "Rail_ActPos": 0.0,
    }
    config = {
        "stations": ["Rail"],
        "station_effects": {"Rail": {"indexed_copy": [{
            "from": "Rail_Pos_Target", "index": "Rail_Target_Position",
            "index_base": 1, "to": "Rail_ActPos",
        }]}},
    }
    sim = PtlcHandshakeSimulator(MemoryAdapter(values), config=config, delay_s=0)
    sim.initialize()
    values.update({
        "Rail_L2_ActionCode": 10,
        "Rail_L2_RequestSeq": 7,
        "Rail_L2_Start": True,
    })
    assert [event.phase for event in sim.step(now=5.0)] == ["accepted", "rejected"]
    assert values["Rail_L2_State"] == 30
    assert values["Rail_L2_ErrorCode"] == 101


def test_unknown_action_is_rejected_with_dispatcher_error_101() -> None:
    """验证未知动作在受理扫描直接拒绝，不经历伪造的运行态。

    参数：无；使用内存变量适配器驱动公开 ``step`` 接口。
    返回：无；断言非零默认延时也不会推迟确定性拒绝。
    """

    values = _station_values("Rail")
    sim = PtlcHandshakeSimulator(
        MemoryAdapter(values), config={"stations": ["Rail"]}, delay_s=0.2
    )
    sim.initialize()
    values.update({
        "Rail_L2_ActionCode": 999,
        "Rail_L2_RequestSeq": 12,
        "Rail_L2_Start": True,
    })
    assert [event.phase for event in sim.step(now=1.0)] == ["accepted", "rejected"]
    assert values["Rail_L2_State"] == 30
    assert values["Rail_L2_Step"] == 0
    assert values["Rail_L2_ErrorCode"] == 101
    assert values["Rail_L2_Retryable"] is True


def test_collect_public_step_remains_zero_during_and_after_action() -> None:
    """验证 Collect 内部相位不会泄漏到公开 L2 Step。

    参数：无；以非零动作延时观察 RUNNING 与 DONE 两个阶段。
    返回：无；断言 ``Collect_L2_Step`` 按现役 PLC 契约全程恒为零。
    """

    values = _station_values("Collect")
    simulator = PtlcHandshakeSimulator(
        MemoryAdapter(values), config={"stations": ["Collect"]}, delay_s=0.2
    )
    simulator.initialize()
    values.update({
        "Collect_L2_ActionCode": 21,
        "Collect_L2_RequestSeq": 31,
        "Collect_L2_Start": True,
    })

    assert [event.phase for event in simulator.step(now=1.0)] == ["accepted"]
    assert values["Collect_L2_State"] == 10
    assert values["Collect_L2_Step"] == 0
    assert [event.phase for event in simulator.step(now=1.2)] == ["completed"]
    assert values["Collect_L2_State"] == 20
    assert values["Collect_L2_Step"] == 0


def test_collect_action_30_waits_for_every_drain_and_settle_cycle() -> None:
    """验证 Collect 收集动作按快照计时而非统一短延时完成。

    参数：无；请求两轮收集并使用快照中的 20 秒排液与 5 秒沉淀常量。
    返回：无；断言两轮排液、沉淀及泵查询延时完整经过后才反馈完成。
    """

    values = {
        **_station_values("Collect"),
        "collect_count": 2,
    }
    simulator = PtlcHandshakeSimulator(
        MemoryAdapter(values), config={"stations": ["Collect"]}, delay_s=0.01
    )
    simulator.initialize()
    values.update({
        "Collect_L2_ActionCode": 30,
        "Collect_L2_RequestSeq": 32,
        "Collect_L2_Start": True,
    })

    assert [event.phase for event in simulator.step(now=0.0)] == ["accepted"]
    assert simulator.step(now=50.99) == []
    assert values["Collect_L2_State"] == 10
    assert [event.phase for event in simulator.step(now=51.0)] == ["completed"]
    assert values["Collect_L2_Step"] == 0


def test_collect_extend_waits_while_the_bottle_sensor_is_occupied() -> None:
    """验证 Collect A22 的无瓶互锁会冻结动作而不是伪完成。

    参数：无；以 ``IX8.bit1`` 模拟放瓶位先有瓶、后清空。
    返回：无；断言门禁解除后才开始气缸仿真延时并进入 DONE。
    """

    values = {
        **_station_values("Collect"),
        "IX8": 0b0000_0010,
    }
    simulator = PtlcHandshakeSimulator(
        MemoryAdapter(values), config={"stations": ["Collect"]}, delay_s=0.2
    )
    simulator.initialize()
    values.update({
        "Collect_L2_ActionCode": 22,
        "Collect_L2_RequestSeq": 33,
        "Collect_L2_Start": True,
    })

    assert [event.phase for event in simulator.step(now=1.0)] == ["accepted"]
    assert simulator.step(now=2.0) == []
    assert values["Collect_L2_State"] == 10
    values["IX8"] = 0
    assert simulator.step(now=2.1) == []
    assert [event.phase for event in simulator.step(now=2.3)] == ["completed"]


def test_collect_retract_reports_missing_bottle_as_error_201() -> None:
    """验证 Collect A23 在缩回判瓶时把缺瓶报告为 201。

    参数：无；令 ``IX8.bit1`` 为零表示无瓶。
    返回：无；断言动作经气缸延时后进入 ERROR，且错误可重试。
    """

    values = {
        **_station_values("Collect"),
        "IX8": 0,
    }
    simulator = PtlcHandshakeSimulator(
        MemoryAdapter(values), config={"stations": ["Collect"]}, delay_s=0.2
    )
    simulator.initialize()
    values.update({
        "Collect_L2_ActionCode": 23,
        "Collect_L2_RequestSeq": 34,
        "Collect_L2_Start": True,
    })

    assert [event.phase for event in simulator.step(now=3.0)] == ["accepted"]
    assert [event.phase for event in simulator.step(now=3.2)] == ["error"]
    assert values["Collect_L2_State"] == 40
    assert values["Collect_L2_ErrorCode"] == 201
    assert values["Collect_L2_Retryable"] is True


def test_collect_retract_checks_the_bottle_after_retraction() -> None:
    """验证 Collect A23 在缩回完成扫描才判定瓶传感器。

    参数：无；动作开始时无瓶，缩回延时内令 ``IX8.bit1`` 到位。
    返回：无；断言最终 DONE，避免把开始瞬间的传感器值错误锁存成缺瓶。
    """

    values = {
        **_station_values("Collect"),
        "IX8": 0,
    }
    simulator = PtlcHandshakeSimulator(
        MemoryAdapter(values), config={"stations": ["Collect"]}, delay_s=0.2
    )
    simulator.initialize()
    values.update({
        "Collect_L2_ActionCode": 23,
        "Collect_L2_RequestSeq": 35,
        "Collect_L2_Start": True,
    })

    assert [event.phase for event in simulator.step(now=4.0)] == ["accepted"]
    values["IX8"] = 0b0000_0010
    assert [event.phase for event in simulator.step(now=4.2)] == ["completed"]
    assert values["Collect_L2_State"] == 20


def test_staging_locator_completes_in_the_accepting_scan() -> None:
    """验证 StagingA 定位动作按现役 PLC 在同一扫描完成。

    参数：无；通过公开 ``step`` 接口提交动作 24。
    返回：无；断言事件序列、完成序号和最终步骤 99 均可立即观察。
    """

    values = {
        **_station_values("StagingA"),
        "StagingA_LocatorA_Target": True,
    }
    simulator = PtlcHandshakeSimulator(
        MemoryAdapter(values), config={"stations": ["StagingA"]}, delay_s=0.2
    )
    simulator.initialize()
    values.update({
        "StagingA_L2_ActionCode": 24,
        "StagingA_L2_RequestSeq": 41,
        "StagingA_L2_Start": True,
    })

    assert [event.phase for event in simulator.step(now=2.0)] == [
        "accepted",
        "completed",
    ]
    assert values["StagingA_L2_State"] == 20
    assert values["StagingA_L2_CompletedSeq"] == 41
    assert values["StagingA_L2_Step"] == 99
    values["StagingA_L2_Start"] = False
    assert [event.phase for event in simulator.step(now=2.1)] == ["rearmed"]
    assert values["StagingA_L2_ActiveCode"] == 0
    assert values["StagingA_L2_SafeState"] == 0
    assert values["StagingA_L2_CompletedSeq"] == 41


def test_sampling_locator_actions_complete_in_the_accepting_scan() -> None:
    """验证 Sampling A32/A33 按 ST 同一扫描写位并完成。

    参数：无；依次提交夹紧与松开动作。
    返回：无；断言默认仿真延时不会给即时气缸动作制造 RUNNING 窗口。
    """

    for code in (32, 33):
        values = _station_values("Sampling")
        simulator = PtlcHandshakeSimulator(
            MemoryAdapter(values), config={"stations": ["Sampling"]}, delay_s=0.2
        )
        simulator.initialize()
        values.update({
            "Sampling_L2_ActionCode": code,
            "Sampling_L2_RequestSeq": code,
            "Sampling_L2_Start": True,
        })

        assert [event.phase for event in simulator.step(now=2.0)] == [
            "accepted",
            "completed",
        ]


def test_agent_restart_does_not_replay_a_held_start_request() -> None:
    """验证代理重启不会盲目重放保持为高电平的物理请求。

    参数：无；以内存中的终态和高 ``Start`` 模拟进程重启后的 OPC 状态。
    返回：无；断言初始化保留持久握手事实且首轮扫描不产生新事件。
    """

    values = _station_values("Pump")
    values.update({
        "Pump_L2_ActionCode": 10,
        "Pump_L2_RequestSeq": 9,
        "Pump_L2_Start": True,
        "Pump_L2_State": 20,
        "Pump_L2_ActiveCode": 10,
        "Pump_L2_AcceptedSeq": 9,
        "Pump_L2_CompletedSeq": 9,
    })
    simulator = PtlcHandshakeSimulator(
        MemoryAdapter(values), config={"stations": ["Pump"]}, delay_s=0.2
    )

    simulator.initialize()

    assert simulator.step(now=3.0) == []
    assert values["Pump_L2_State"] == 20
    assert values["Pump_L2_AcceptedSeq"] == 9
    assert values["Pump_L2_CompletedSeq"] == 9


def test_cleanup_stops_outputs_without_erasing_handshake_facts() -> None:
    """验证代理退出只回收物理输出，不清空可恢复的 L2 序号与终态。

    参数：无；预置 Pump 已完成事实和真空泵输出。
    返回：无；断言清理后泵关闭，但 State/AcceptedSeq/CompletedSeq 保留。
    """

    values = _station_values("Pump")
    values.update({
        "Pump_L2_State": 20,
        "Pump_L2_AcceptedSeq": 9,
        "Pump_L2_CompletedSeq": 9,
        "Pump_Vacuum_On": True,
    })
    simulator = PtlcHandshakeSimulator(
        MemoryAdapter(values), config={"stations": ["Pump"]}, delay_s=0.2
    )

    simulator.cleanup()

    assert values["Pump_Vacuum_On"] is False
    assert values["Pump_L2_State"] == 20
    assert values["Pump_L2_AcceptedSeq"] == 9
    assert values["Pump_L2_CompletedSeq"] == 9


def test_staging_reset_interrupts_and_preserves_completed_sequence() -> None:
    """验证 StagingA 运行中 Reset 进入中断态并保留完成序号事实。

    参数：无；以已经处于 RUNNING 的 OPC 状态模拟中断扫描。
    返回：无；断言状态 50、错误 402，且既有 ``CompletedSeq`` 不被清零。
    """

    values = _station_values("StagingA")
    values.update({
        "StagingA_L2_Start": True,
        "StagingA_L2_Reset": False,
        "StagingA_L2_State": 10,
        "StagingA_L2_ActiveCode": 24,
        "StagingA_L2_AcceptedSeq": 12,
        "StagingA_L2_CompletedSeq": 11,
    })
    simulator = PtlcHandshakeSimulator(
        MemoryAdapter(values), config={"stations": ["StagingA"]}, delay_s=0.2
    )
    simulator.initialize()
    values["StagingA_L2_Reset"] = True

    assert [event.phase for event in simulator.step(now=4.0)] == ["interrupted"]
    assert values["StagingA_L2_State"] == 50
    assert values["StagingA_L2_ErrorCode"] == 402
    assert values["StagingA_L2_CompletedSeq"] == 11


def test_staging_rejects_a_duplicate_request_sequence() -> None:
    """验证 StagingA 对已接受或已完成序号关闭失败。

    参数：无；预置序号 20 后再次提交同一身份的定位请求。
    返回：无；断言请求以 DUPLICATE_SEQ(102) 拒绝且不进入物理动作。
    """

    values = _station_values("StagingA")
    values.update({
        "StagingA_L2_AcceptedSeq": 20,
        "StagingA_L2_CompletedSeq": 20,
    })
    simulator = PtlcHandshakeSimulator(
        MemoryAdapter(values), config={"stations": ["StagingA"]}, delay_s=0.2
    )
    simulator.initialize()
    values.update({
        "StagingA_L2_ActionCode": 24,
        "StagingA_L2_RequestSeq": 20,
        "StagingA_L2_Start": True,
    })

    assert [event.phase for event in simulator.step(now=5.0)] == ["rejected"]
    assert values["StagingA_L2_State"] == 30
    assert values["StagingA_L2_ErrorCode"] == 102
    assert values["StagingA_L2_AcceptedSeq"] == 20


def test_staging_rejects_a_new_start_while_the_channel_is_busy() -> None:
    """验证 StagingA 在非 IDLE 收到新 Start 上升沿时报告 BUSY(101)。

    参数：无；预置运行态后提交新请求序号。
    返回：无；断言新请求以可重试拒绝闭合，而不是静默忽略。
    """

    values = _station_values("StagingA")
    values.update({
        "StagingA_L2_State": 10,
        "StagingA_L2_ActiveCode": 24,
        "StagingA_L2_AcceptedSeq": 30,
        "StagingA_L2_CompletedSeq": 29,
    })
    simulator = PtlcHandshakeSimulator(
        MemoryAdapter(values), config={"stations": ["StagingA"]}, delay_s=0.2
    )
    simulator.initialize()
    values.update({
        "StagingA_L2_ActionCode": 25,
        "StagingA_L2_RequestSeq": 31,
        "StagingA_L2_Start": True,
    })

    assert [event.phase for event in simulator.step(now=6.0)] == ["rejected"]
    assert values["StagingA_L2_State"] == 30
    assert values["StagingA_L2_ErrorCode"] == 101
    assert values["StagingA_L2_Retryable"] is True
    assert values["StagingA_L2_CompletedSeq"] == 31


def test_runtime_fault_can_interrupt_a_valid_action() -> None:
    values = _station_values("Pump")
    sim = PtlcHandshakeSimulator(
        MemoryAdapter(values), config={"stations": ["Pump"]}, delay_s=0
    )
    sim.runtime_faults.set("Pump", 10, "interrupt")
    sim.initialize()
    values.update({
        "Pump_L2_ActionCode": 10,
        "Pump_L2_RequestSeq": 13,
        "Pump_L2_Start": True,
    })
    assert [event.phase for event in sim.step(now=2.0)] == ["accepted", "interrupted"]
    assert values["Pump_L2_State"] == 50
    assert values["Pump_L2_ErrorCode"] == 202


def test_deploy_fsm_prepares_commits_and_requires_fail_closed_reset() -> None:
    values = {
        **_station_values("Rail"),
        "PLC_Axis_CommOperational": [True] * 11,
        "PLC_Deploy_RequestSeq": 7,
        "PLC_Deploy_CommitSeq": 0,
        "PLC_Deploy_Start": False,
        "PLC_Deploy_Reset": False,
        "PLC_Deploy_AcceptedSeq": 0,
        "PLC_Deploy_ErrorCode": 0,
    }
    sim = PtlcHandshakeSimulator(
        MemoryAdapter(values),
        config={"stations": ["Rail"], "deploy_prepare_ms": 20},
        delay_s=0,
    )
    sim.initialize()
    values["PLC_Deploy_Start"] = True
    sim.step(now=10.0)
    assert values["PLC_Deploy_State"] == 10
    assert values["PLC_Deploy_AcceptedSeq"] == 7
    sim.step(now=10.02)
    assert values["PLC_Deploy_State"] == 20
    values["PLC_Deploy_CommitSeq"] = 7
    sim.step(now=10.03)
    assert values["PLC_Deploy_State"] == 25
    values["PLC_Axis_CommOperational"][3] = False
    sim.step(now=10.04)
    assert values["PLC_Deploy_State"] == 25
    assert values["PLC_Deploy_ErrorCode"] == 5
    values.update({
        "PLC_Deploy_Start": False,
        "PLC_Deploy_CommitSeq": 0,
        "PLC_Deploy_Reset": True,
    })
    sim.step(now=10.05)
    assert values["PLC_Deploy_State"] == 0


def test_motion_progresses_continuously_before_done() -> None:
    """验证 Rail 轴在动作完成前连续推进实际位置。

    参数：无；使用 10 mm/s 速度和 10 mm 目标形成一秒运动。
    返回：无；断言中点位置和动作完成时刻均符合可观察运动契约。
    """

    values = {
        **_station_values("Rail"),
        "Rail_Target_Position": 1,
        "Rail_Pos_Target": [10.0],
        "Rail_ActPos": 0.0,
    }
    sim = PtlcHandshakeSimulator(
        MemoryAdapter(values),
        config={
            "stations": ["Rail"],
            "motion_speed": {"Rail": 10.0},
        },
        delay_s=0,
    )
    sim.initialize()
    values.update({
        "Rail_L2_ActionCode": 10,
        "Rail_L2_RequestSeq": 20,
        "Rail_L2_Start": True,
    })
    assert [event.phase for event in sim.step(now=0.0)] == ["accepted"]
    assert sim.step(now=0.5) == []
    assert values["Rail_ActPos"] == pytest.approx(5.0)
    assert [event.phase for event in sim.step(now=1.0)] == ["completed"]
    assert values["Rail_ActPos"] == pytest.approx(10.0)


def test_sampling_init_moves_5z_before_4x_and_never_moves_3y() -> None:
    """验证 Sampling 初始化的可见轴序与现役 PLC 一致。

    参数：无；以三个非零实际位置观察初始化前半段。
    返回：无；断言先回 5Z、随后才回 4X，且 3Y 在整个初始化中保持不动。
    """

    values = {
        **_station_values("Sampling"),
        "Sampling_4X_Target": 100.0,
        "Sampling_4X_ActPos": 12.0,
        "Sampling_3Y_Target": 100.0,
        "Sampling_3Y_ActPos": 8.0,
        "Sampling_5Z_Target": 100.0,
        "Sampling_5Z_ActPos": 6.0,
    }
    simulator = PtlcHandshakeSimulator(
        MemoryAdapter(values),
        config={
            "stations": ["Sampling"],
            "motion_speed": {"Sampling": 100.0},
            "motion_effects": {
                "Sampling": [
                    {"from": "Sampling_4X_Target", "to": "Sampling_4X_ActPos"},
                    {"from": "Sampling_3Y_Target", "to": "Sampling_3Y_ActPos"},
                    {"from": "Sampling_5Z_Target", "to": "Sampling_5Z_ActPos"},
                ]
            },
            "station_effects": {
                "Sampling": {
                    "copy": [
                        {"from": "Sampling_4X_Target", "to": "Sampling_4X_ActPos"},
                        {"from": "Sampling_3Y_Target", "to": "Sampling_3Y_ActPos"},
                        {"from": "Sampling_5Z_Target", "to": "Sampling_5Z_ActPos"},
                    ]
                }
            },
        },
        delay_s=0.2,
    )
    simulator.initialize()
    values.update({
        "Sampling_L2_ActionCode": 10,
        "Sampling_L2_RequestSeq": 51,
        "Sampling_L2_Start": True,
    })

    assert [event.phase for event in simulator.step(now=0.0)] == ["accepted"]
    simulator.step(now=0.03)
    assert values["Sampling_5Z_ActPos"] == pytest.approx(3.0)
    assert values["Sampling_4X_ActPos"] == pytest.approx(12.0)
    assert values["Sampling_3Y_ActPos"] == pytest.approx(8.0)
    simulator.step(now=0.07)
    assert values["Sampling_5Z_ActPos"] == pytest.approx(0.0)
    assert values["Sampling_4X_ActPos"] < 12.0
    assert values["Sampling_3Y_ActPos"] == pytest.approx(8.0)
    assert simulator.step(now=0.2) == []
    assert values["Sampling_4X_ActPos"] == pytest.approx(0.0)
    assert values["Sampling_3Y_ActPos"] == pytest.approx(8.0)
    assert [event.phase for event in simulator.step(now=0.68)] == ["completed"]


def test_sampling_action_50_rejects_invalid_p_instruction_without_hanging() -> None:
    """验证吸液动作已建模，并对无效泵指令返回确定性错误。

    参数：无；提交需要泵协议、行程校验和安全抬针的 Sampling A50。
    返回：无；断言动作不会永久 RUNNING，而是按 PLC 契约返回 463。
    """

    values = _station_values("Sampling")
    simulator = PtlcHandshakeSimulator(
        MemoryAdapter(values), config={"stations": ["Sampling"]}, delay_s=0.01
    )
    simulator.initialize()
    values.update({
        "Sampling_L2_ActionCode": 50,
        "Sampling_L2_RequestSeq": 52,
        "Sampling_L2_Start": True,
    })

    assert [event.phase for event in simulator.step(now=0.0)] == ["accepted"]
    assert [event.phase for event in simulator.step(now=60.0)] == ["error"]
    assert values["Sampling_L2_State"] == 40
    assert values["Sampling_L2_ErrorCode"] == 463
    assert simulator.snapshot()["plant"]["coverage"]["unmodeled"]["Sampling"] == []


def test_tank_drain_runs_phases_and_updates_native_array() -> None:
    values = {
        **_station_values("Develop"),
        "Expand_Target_Tank": 2,
        "Tank_State": [0] * 8,
        "Tank_Drain_Enable": [False] * 8,
        "Tank_Drain_Done": [False] * 8,
        "Tank_Drain_CapHit": [False] * 8,
        "Tank_Drain_S": 0.2,
        "Tank_Drain_Cap_S": 0.5,
        "Tank_Blow_S": 0.1,
        "Tank_Dry_S": 0.1,
    }
    sim = PtlcHandshakeSimulator(
        MemoryAdapter(values), config={"stations": ["Develop"]}, delay_s=0
    )
    sim.initialize()
    values.update({
        "Develop_L2_ActionCode": 50,
        "Develop_L2_RequestSeq": 21,
        "Develop_L2_Start": True,
    })
    assert [event.phase for event in sim.step(now=0.0)] == ["accepted"]
    assert values["Tank_State"][1] == 50
    sim.step(now=0.25)
    assert values["Tank_State"][1] == 55
    sim.step(now=0.35)
    assert values["Tank_State"][1] == 56
    assert [event.phase for event in sim.step(now=0.4)] == ["completed"]
    assert values["Tank_State"][1] == 98
    assert values["Tank_Drain_Enable"][1] is False
    assert values["Tank_Drain_Done"][1] is True
