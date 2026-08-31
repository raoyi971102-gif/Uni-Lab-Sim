from __future__ import annotations

from typing import Any

import szlab_handshake_agent as handshake
from szlab_package_runtime import SzlabPackageRuntime


class MemoryAdapter:
    """为新工作流握手切片保存可观察的 PLC 变量值。"""

    def __init__(self) -> None:
        self.values: dict[str, Any] = {
            handshake.ROBOT_TASK_NUMBER: 0,
            handshake.S04_ROBOT_POSITION: 0,
            handshake.s04_process(1): 0,
            handshake.s04_params_written(1): False,
            handshake.s04_process(2): 0,
            handshake.s04_params_written(2): False,
            handshake.S06_PROCESS: 0,
            handshake.S06_PARAMS_WRITTEN: False,
            handshake.S07_PROCESS: 0,
            handshake.S07_PARAMS_WRITTEN: False,
            handshake.S08_PROCESS: 0,
            handshake.S08_PARAMS_WRITTEN: False,
            handshake.S08_CAP_STORAGE_SLOT: 0,
            handshake.S09_PROCESS: 0,
            handshake.S09_PARAMS_WRITTEN: False,
        }

    def read(self, name: str) -> Any:
        return self.values[name]

    def write(self, name: str, value: Any) -> None:
        self.values[name] = value


def test_attachment_flow_has_an_independent_scan_free_handshake_catalog() -> None:
    """证明附件流程具有独立且无 S07 扫码/粉桶上机的握手目录。

    参数：无。
    返回：无；断言动作按新内核源码首次出现顺序登记，旧流程保持可用。
    """

    specs = {
        spec.workflow_id: spec for spec in handshake.build_workflow_specs()
    }
    attachment = specs[handshake.ATTACHMENT_SINGLE_SAMPLE_WORKFLOW]

    assert handshake.SINGLE_SAMPLE_WORKFLOW in specs
    assert len(specs) == 21
    assert attachment.actions == (
        "szlab_mixer_robot.pick",
        "szlab_mixer_robot.place",
        "host_node.transfer_resource",
        "szlab_s08_cap_station.process_liquid_reagent_100ml_cap_with_material",
        "szlab_s07_solid_addition.dose_powder_with_two_materials",
        "szlab_mixer_pump.add_solvent_with_materials",
        "szlab_mixer_pipetting_station.add_liquid_with_materials",
        "szlab_mixer_pipetting_station.measure_density_with_materials",
        "szlab_mixer_stirrer.stir_beaker",
        "szlab_s08_cap_station.process_sample_vial_250ml_cap_with_material",
        "szlab_mixer_photoshotting.inspect_beaker",
        "szlab_mixer_robot.pick_beaker",
        "szlab_mixer_robot.pour_beaker_into_vial",
    )
    assert "szlab_s07_solid_addition.scan_powder_cartridges" not in attachment.actions
    assert "szlab_s07_solid_addition.prepare_powder_cartridge_site" not in attachment.actions


def test_robot_atomic_profile_only_exposes_composite_robot_actions() -> None:
    """单、双 TASK 机器人原子动作场景只登记两个复合机器人动作。

    参数：无。
    返回：无；断言普通机器人子动作没有重新进入新工作流的动作目录。
    """

    specs = {
        spec.workflow_id: spec for spec in handshake.build_workflow_specs()
    }
    atomic = specs[handshake.ROBOT_ATOMIC_SINGLE_SAMPLE_WORKFLOW]
    dual_atomic = specs[handshake.DUAL_TASK_ROBOT_ATOMIC_WORKFLOW]

    assert handshake.ATOMIC_TRANSFER_ACTION in atomic.actions
    assert handshake.ATOMIC_PICK_POUR_PLACE_ACTION in atomic.actions
    assert "szlab_mixer_robot.pick" not in atomic.actions
    assert "szlab_mixer_robot.place" not in atomic.actions
    assert "szlab_mixer_robot.pick_beaker" not in atomic.actions
    assert "szlab_mixer_robot.pour_beaker_into_vial" not in atomic.actions
    assert dual_atomic.actions == atomic.actions
    dual_requirement_subjects = {
        requirement.subject for requirement in dual_atomic.requirements
    }
    assert {
        handshake.s03_sensor(1, 2),
        handshake.s03_sensor(3, 2),
        handshake.s10_sensor(2),
        handshake.s11_sensor(1, 2),
        handshake.s11_sensor(3, 2),
    } <= dual_requirement_subjects


def test_dual_task_robot_atomic_profile_rejects_parallel_second_pick() -> None:
    """双 TASK 机器人原子动作场景共享夹爪，持料时拒绝另一通道取料。

    参数：无。
    返回：无；断言 Task A 映射为原子搬运，Task B 第二次取料不产生物理效果。
    """

    adapter = MemoryAdapter()
    simulator = handshake.WorkflowHandshakeSimulator(
        adapter,
        process_delay=0.5,
        workflow=handshake.DUAL_TASK_ROBOT_ATOMIC_WORKFLOW,
    )
    simulator.initialize()

    assert adapter.read(handshake.s03_sensor(1, 2)) is True
    adapter.write(handshake.S03_ROBOT_PRODUCT, 1)
    adapter.write(handshake.S03_ROBOT_POSITION, 1)
    adapter.write(handshake.ROBOT_TASK_NUMBER, 6)
    adapter.write(handshake.ROBOT_WRITE_DONE, True)
    first_pick = simulator.step(now=0.0) + simulator.step(now=0.5)

    assert [(event.action, event.phase) for event in first_pick] == [
        (handshake.ATOMIC_TRANSFER_ACTION, "accepted"),
        (handshake.ATOMIC_TRANSFER_ACTION, "completed"),
    ]
    assert adapter.read(handshake.ROBOT_TOOL_PAYLOAD_SENSOR) is True

    adapter.write(handshake.ROBOT_WRITE_DONE, False)
    simulator.step(now=0.6)
    adapter.write(handshake.S03_ROBOT_POSITION, 2)
    adapter.write(handshake.ROBOT_WRITE_DONE, True)
    rejected = simulator.step(now=1.0)

    assert [(event.action, event.phase) for event in rejected] == [
        (handshake.ATOMIC_TRANSFER_ACTION, "rejected")
    ]
    assert rejected[0].detail["reason"] == "夹爪已持有物料，禁止再次取料"
    assert adapter.read(handshake.s03_sensor(1, 2)) is True
    assert adapter.read(handshake.ROBOT_TASK_COMPLETE) == 0
    assert simulator.completed_actions == 1


def test_dual_task_robot_atomic_profile_places_to_second_s04_position() -> None:
    """双 TASK 场景必须响应位置 2 的 S04 放料，而不是只监听默认位置 1。

    参数：无。
    返回：无；断言任务 7 在 S042 完成并只更新位置 2 的物理在位见证。
    """

    adapter = MemoryAdapter()
    simulator = handshake.WorkflowHandshakeSimulator(
        adapter,
        process_delay=0.5,
        workflow=handshake.DUAL_TASK_ROBOT_ATOMIC_WORKFLOW,
    )
    simulator.initialize()
    adapter.write(handshake.ROBOT_TOOL_PAYLOAD_SENSOR, True)
    adapter.write(handshake.S04_ROBOT_POSITION, 2)
    adapter.write(handshake.ROBOT_TASK_NUMBER, 7)
    adapter.write(handshake.ROBOT_WRITE_DONE, True)

    events = simulator.step(now=0.0) + simulator.step(now=0.5)

    assert [(event.action, event.phase) for event in events] == [
        (handshake.ATOMIC_TRANSFER_ACTION, "accepted"),
        (handshake.ATOMIC_TRANSFER_ACTION, "completed"),
    ]
    assert adapter.read(handshake.ROBOT_TASK_COMPLETE) == 7
    assert adapter.read(handshake.s04_sensor(1)) is False
    assert adapter.read(handshake.s04_sensor(2)) is True
    assert adapter.read(handshake.ROBOT_TOOL_PAYLOAD_SENSOR) is False


def test_dual_task_robot_atomic_profile_runs_two_s04_stirrers_independently() -> None:
    """双 TASK 场景的 S041/S042 搅拌周期必须能独立并发推进。

    参数：无。
    返回：无；断言两个位置各自产生接单、完成事件和独立完成信号。
    """

    adapter = MemoryAdapter()
    adapter.write(handshake.s04_process(2), 0)
    adapter.write(handshake.s04_params_written(2), False)
    simulator = handshake.WorkflowHandshakeSimulator(
        adapter,
        process_delay=0.5,
        workflow=handshake.DUAL_TASK_ROBOT_ATOMIC_WORKFLOW,
    )
    simulator.initialize()
    adapter.write(handshake.s04_process(1), 1)
    adapter.write(handshake.s04_params_written(1), True)
    adapter.write(handshake.s04_process(2), 2)
    adapter.write(handshake.s04_params_written(2), True)

    accepted = simulator.step(now=0.0)
    completed = simulator.step(now=0.5)

    assert {(event.phase, event.detail["position"]) for event in accepted} == {
        ("accepted", 1),
        ("accepted", 2),
    }
    assert {(event.phase, event.detail["position"]) for event in completed} == {
        ("completed", 1),
        ("completed", 2),
    }
    assert adapter.read(handshake.s04_done(1)) is True
    assert adapter.read(handshake.s04_done(2)) is True


def test_dual_task_attachment_profile_initializes_two_independent_material_lanes() -> None:
    """双 Task 场景同时提供 A/B 源物料，并保持两条成品目标通道为空。"""

    adapter = MemoryAdapter()
    simulator = handshake.WorkflowHandshakeSimulator(
        adapter,
        process_delay=0.5,
        workflow=handshake.DUAL_TASK_ATTACHMENT_WORKFLOW,
    )

    simulator.initialize()

    for sensor in (
        handshake.s03_sensor(1, 1),
        handshake.s03_sensor(1, 2),
        handshake.s03_sensor(3, 1),
        handshake.s03_sensor(3, 2),
        handshake.s10_sensor(1),
        handshake.s10_sensor(2),
    ):
        assert adapter.read(sensor) is True
    for sensor in (
        handshake.s11_sensor(1, 1),
        handshake.s11_sensor(1, 2),
        handshake.s11_sensor(3, 1),
        handshake.s11_sensor(3, 2),
    ):
        assert adapter.read(sensor) is False

    simulator.cleanup()

    for sensor in (
        handshake.s03_sensor(1, 1),
        handshake.s03_sensor(1, 2),
        handshake.s03_sensor(3, 1),
        handshake.s03_sensor(3, 2),
        handshake.s10_sensor(1),
        handshake.s10_sensor(2),
        handshake.s11_sensor(1, 1),
        handshake.s11_sensor(1, 2),
        handshake.s11_sensor(3, 1),
        handshake.s11_sensor(3, 2),
    ):
        assert adapter.read(sensor) is False


def test_every_single_sample_profile_seeds_the_full_material_stack_pool() -> None:
    """每个单样品握手场景都提供完整源位池，并将全部成品目标库位初始化为空。

    参数：无。
    返回：无；断言工作流（Workflow）选择不再把物料物理证据限制为固定 A/B 库位（Site）。
    """

    assert len(handshake.S03_BEAKER_SOURCE_SENSORS) == 18
    assert len(handshake.S03_SAMPLE_VIAL_SOURCE_SENSORS) == 18
    assert len(handshake.S10_REAGENT_SOURCE_SENSORS) == 20
    assert len(handshake.S11_BEAKER_TARGET_SENSORS) == 18
    assert len(handshake.S11_SAMPLE_VIAL_TARGET_SENSORS) == 18

    for workflow_id in handshake.MATERIAL_STACK_POOL_WORKFLOWS:
        simulator = handshake.WorkflowHandshakeSimulator(
            MemoryAdapter(),
            workflow=workflow_id,
        )
        initial_values = simulator.initialization_values()

        assert all(
            initial_values[sensor] is True
            for sensor in handshake.SINGLE_SAMPLE_SOURCE_STACK_SENSORS
        )
        assert all(
            initial_values[sensor] is False
            for sensor in handshake.SINGLE_SAMPLE_TARGET_STACK_SENSORS
        )


def test_one_atomic_profile_updates_only_the_runtime_selected_stack_positions() -> None:
    """同一机器人原子动作场景可依次使用不同烧杯和试剂瓶位置。

    参数：无。
    返回：无；断言运行时位置 18/20 能被取放，且相邻未命中库位（Site）保持不变。
    """

    adapter = MemoryAdapter()
    simulator = handshake.WorkflowHandshakeSimulator(
        adapter,
        process_delay=0.5,
        workflow=handshake.ROBOT_ATOMIC_SINGLE_SAMPLE_WORKFLOW,
    )
    simulator.initialize()

    # 从 S03 最后一个烧杯源位取料，只消费本次命中的物理证据。
    adapter.write(handshake.S03_ROBOT_PRODUCT, 1)
    adapter.write(handshake.S03_ROBOT_POSITION, 18)
    adapter.write(handshake.ROBOT_TASK_NUMBER, 6)
    adapter.write(handshake.ROBOT_WRITE_DONE, True)
    picked_beaker = simulator.step(now=0.0) + simulator.step(now=0.5)
    assert {(event.action, event.phase) for event in picked_beaker} == {
        (handshake.ATOMIC_TRANSFER_ACTION, "accepted"),
        (handshake.ATOMIC_TRANSFER_ACTION, "completed"),
    }
    assert adapter.read(handshake.s03_sensor(1, 18)) is False
    assert adapter.read(handshake.s03_sensor(1, 17)) is True

    adapter.write(handshake.ROBOT_WRITE_DONE, False)
    simulator.step(now=0.6)
    adapter.write(handshake.S11_ROBOT_PRODUCT, 1)
    adapter.write(handshake.S11_ROBOT_POSITION, 18)
    adapter.write(handshake.ROBOT_TASK_NUMBER, 23)
    adapter.write(handshake.ROBOT_WRITE_DONE, True)
    simulator.step(now=1.0)
    simulator.step(now=1.5)
    assert adapter.read(handshake.s11_sensor(1, 18)) is True
    assert adapter.read(handshake.s11_sensor(1, 17)) is False
    assert adapter.read(handshake.ROBOT_TOOL_PAYLOAD_SENSOR) is False

    # 同一代理无需重启即可按 S10 运行时编号选择另一瓶试剂。
    adapter.write(handshake.ROBOT_WRITE_DONE, False)
    simulator.step(now=1.6)
    adapter.write(handshake.S10_ROBOT_POSITION, 20)
    adapter.write(handshake.ROBOT_TASK_NUMBER, 22)
    adapter.write(handshake.ROBOT_WRITE_DONE, True)
    picked_reagent = simulator.step(now=2.0) + simulator.step(now=2.5)
    assert {(event.action, event.phase) for event in picked_reagent} == {
        (handshake.ATOMIC_TRANSFER_ACTION, "accepted"),
        (handshake.ATOMIC_TRANSFER_ACTION, "completed"),
    }
    assert adapter.read(handshake.s10_sensor(20)) is False
    assert adapter.read(handshake.s10_sensor(19)) is True

    simulator.cleanup()
    assert all(
        adapter.read(sensor) is False
        for sensor in (
            *handshake.SINGLE_SAMPLE_SOURCE_STACK_SENSORS,
            *handshake.SINGLE_SAMPLE_TARGET_STACK_SENSORS,
        )
    )


def test_dual_task_attachment_profile_updates_only_the_commanded_lane() -> None:
    """位置 2 的取放料握手只改变 Task B 传感器，不覆盖 Task A。"""

    adapter = MemoryAdapter()
    simulator = handshake.WorkflowHandshakeSimulator(
        adapter,
        process_delay=0.5,
        workflow=handshake.DUAL_TASK_ATTACHMENT_WORKFLOW,
    )
    simulator.initialize()

    adapter.write(handshake.S03_ROBOT_PRODUCT, 1)
    adapter.write(handshake.S03_ROBOT_POSITION, 2)
    adapter.write(handshake.ROBOT_TASK_NUMBER, 6)
    adapter.write(handshake.ROBOT_WRITE_DONE, True)
    simulator.step(now=0.0)
    simulator.step(now=0.5)

    assert adapter.read(handshake.s03_sensor(1, 1)) is True
    assert adapter.read(handshake.s03_sensor(1, 2)) is False

    adapter.write(handshake.ROBOT_WRITE_DONE, False)
    simulator.step(now=0.6)
    adapter.write(handshake.S11_ROBOT_PRODUCT, 1)
    adapter.write(handshake.S11_ROBOT_POSITION, 2)
    adapter.write(handshake.ROBOT_TASK_NUMBER, 23)
    adapter.write(handshake.ROBOT_WRITE_DONE, True)
    simulator.step(now=1.0)
    simulator.step(now=1.5)

    assert adapter.read(handshake.s11_sensor(1, 1)) is False
    assert adapter.read(handshake.s11_sensor(1, 2)) is True


def test_dual_task_rejects_second_pick_until_shared_gripper_is_released() -> None:
    """双 TASK 并行取料共用同一夹爪，Task A 持料时必须拒绝 Task B 第二次取料。

    参数：无。
    返回：无；断言拒绝期间不更改任一库位（Site）或完成计数，放料后 Task B 才可取料。
    """

    adapter = MemoryAdapter()
    simulator = handshake.WorkflowHandshakeSimulator(
        adapter,
        process_delay=0.5,
        workflow=handshake.DUAL_TASK_ATTACHMENT_WORKFLOW,
    )
    simulator.initialize()

    # Task A 的烧杯先从 L1B1 取出，夹爪负载成为共享物理事实。
    adapter.write(handshake.S03_ROBOT_PRODUCT, 1)
    adapter.write(handshake.S03_ROBOT_POSITION, 1)
    adapter.write(handshake.ROBOT_TASK_NUMBER, 6)
    adapter.write(handshake.ROBOT_WRITE_DONE, True)
    assert [event.phase for event in simulator.step(now=0.0)] == ["accepted"]
    assert [event.phase for event in simulator.step(now=0.5)] == ["completed"]
    assert adapter.read(handshake.ROBOT_TOOL_PAYLOAD_SENSOR) is True
    assert adapter.read(handshake.s03_sensor(1, 1)) is False

    adapter.write(handshake.ROBOT_WRITE_DONE, False)
    simulator.step(now=0.6)

    # Task B 不能在 Task A 仍占用夹爪时取 L1B2。
    adapter.write(handshake.S03_ROBOT_POSITION, 2)
    adapter.write(handshake.ROBOT_TASK_NUMBER, 6)
    adapter.write(handshake.ROBOT_WRITE_DONE, True)
    rejected = simulator.step(now=1.0)

    assert [(event.phase, event.detail["reason"]) for event in rejected] == [
        ("rejected", "夹爪已持有物料，禁止再次取料")
    ]
    assert adapter.read(handshake.ROBOT_HOME) is True
    assert adapter.read(handshake.ROBOT_WRITE_ALLOWED) is False
    assert adapter.read(handshake.ROBOT_TASK_COMPLETE) == 0
    assert adapter.read(handshake.ROBOT_TOOL_PAYLOAD_SENSOR) is True
    assert adapter.read(handshake.s03_sensor(1, 2)) is True
    assert simulator.completed_actions == 1
    assert simulator.step(now=1.5) == []

    # Edge 撤回被拒绝命令后，Task A 先放到 S0722 释放夹爪。
    adapter.write(handshake.ROBOT_WRITE_DONE, False)
    reset = simulator.step(now=1.6)
    assert reset[0].phase == "reset"
    assert reset[0].detail["rejected"] is True

    adapter.write(handshake.S072_ROBOT_PRODUCT, 2)
    adapter.write(handshake.ROBOT_TASK_NUMBER, 15)
    adapter.write(handshake.ROBOT_WRITE_DONE, True)
    assert [event.phase for event in simulator.step(now=2.0)] == ["accepted"]
    assert [event.phase for event in simulator.step(now=2.5)] == ["completed"]
    assert adapter.read(handshake.ROBOT_TOOL_PAYLOAD_SENSOR) is False

    adapter.write(handshake.ROBOT_WRITE_DONE, False)
    simulator.step(now=2.6)

    # 夹爪空闲后，同一条 Task B 取料命令可正常完成。
    adapter.write(handshake.S03_ROBOT_PRODUCT, 1)
    adapter.write(handshake.S03_ROBOT_POSITION, 2)
    adapter.write(handshake.ROBOT_TASK_NUMBER, 6)
    adapter.write(handshake.ROBOT_WRITE_DONE, True)
    assert [event.phase for event in simulator.step(now=3.0)] == ["accepted"]
    assert [event.phase for event in simulator.step(now=3.5)] == ["completed"]
    assert adapter.read(handshake.s03_sensor(1, 2)) is False
    assert adapter.read(handshake.ROBOT_TOOL_PAYLOAD_SENSOR) is True
    assert simulator.completed_actions == 3


def test_robot_atomic_profile_maps_physical_phases_and_keeps_pour_payload() -> None:
    """机器人原子动作场景依次执行取料、倒液、放料，倒液本身不释放夹爪。

    参数：无。
    返回：无；断言 PLC 子阶段归属正确的原子动作并维持物理负载顺序。
    """

    adapter = MemoryAdapter()
    simulator = handshake.WorkflowHandshakeSimulator(
        adapter,
        process_delay=0.5,
        workflow=handshake.ROBOT_ATOMIC_SINGLE_SAMPLE_WORKFLOW,
    )
    simulator.initialize()

    # 代表性原子搬运的 S03 取料阶段。
    adapter.write(handshake.S03_ROBOT_PRODUCT, 1)
    adapter.write(handshake.S03_ROBOT_POSITION, 1)
    adapter.write(handshake.ROBOT_TASK_NUMBER, 6)
    adapter.write(handshake.ROBOT_WRITE_DONE, True)
    accepted = simulator.step(now=0.0)
    completed = simulator.step(now=0.5)
    assert [(event.action, event.phase) for event in accepted + completed] == [
        (handshake.ATOMIC_TRANSFER_ACTION, "accepted"),
        (handshake.ATOMIC_TRANSFER_ACTION, "completed"),
    ]
    assert adapter.read(handshake.ROBOT_TOOL_PAYLOAD_SENSOR) is True

    adapter.write(handshake.ROBOT_WRITE_DONE, False)
    simulator.step(now=0.6)
    adapter.write(handshake.S072_ROBOT_PRODUCT, 2)
    adapter.write(handshake.ROBOT_TASK_NUMBER, 15)
    adapter.write(handshake.ROBOT_WRITE_DONE, True)
    simulator.step(now=1.0)
    simulator.step(now=1.5)
    assert adapter.read(handshake.ROBOT_TOOL_PAYLOAD_SENSOR) is False

    # 末段 pick-pour-place 从 S05 取烧杯，倒液后仍持料，到 S11 放料才释放。
    adapter.write(handshake.ROBOT_WRITE_DONE, False)
    simulator.step(now=1.6)
    adapter.write(handshake.S05_MATERIAL_SENSOR, True)
    adapter.write(handshake.ROBOT_TASK_NUMBER, 10)
    adapter.write(handshake.ROBOT_WRITE_DONE, True)
    picked = simulator.step(now=2.0) + simulator.step(now=2.5)
    assert {event.action for event in picked} == {
        handshake.ATOMIC_PICK_POUR_PLACE_ACTION
    }
    assert adapter.read(handshake.ROBOT_TOOL_PAYLOAD_SENSOR) is True

    adapter.write(handshake.ROBOT_WRITE_DONE, False)
    simulator.step(now=2.6)
    adapter.write(handshake.S08_POUR_PRODUCT, 1)
    adapter.write(handshake.ROBOT_TASK_NUMBER, 25)
    adapter.write(handshake.ROBOT_WRITE_DONE, True)
    poured = simulator.step(now=3.0) + simulator.step(now=3.5)
    assert {event.action for event in poured} == {
        handshake.ATOMIC_PICK_POUR_PLACE_ACTION
    }
    assert adapter.read(handshake.ROBOT_TOOL_PAYLOAD_SENSOR) is True

    adapter.write(handshake.ROBOT_WRITE_DONE, False)
    simulator.step(now=3.6)
    adapter.write(handshake.S11_ROBOT_PRODUCT, 1)
    adapter.write(handshake.S11_ROBOT_POSITION, 1)
    adapter.write(handshake.ROBOT_TASK_NUMBER, 23)
    adapter.write(handshake.ROBOT_WRITE_DONE, True)
    placed = simulator.step(now=4.0) + simulator.step(now=4.5)
    assert {event.action for event in placed} == {
        handshake.ATOMIC_PICK_POUR_PLACE_ACTION
    }
    assert adapter.read(handshake.ROBOT_TOOL_PAYLOAD_SENSOR) is False


def test_robot_task_23_keeps_atomic_action_when_edge_clears_s11_parameters() -> None:
    """任务 23 的动作身份必须跨 accepted/completed/reset 保持稳定。

    Edge 消费完成码后会先清空 S11 产品和位置参数，再撤回任务写入信号。
    仿真器不得在 reset 阶段根据已变化参数重新推导动作名称，否则设备包
    运行时会把同一物理周期识别成两个动作并拒绝记录。
    """

    adapter = MemoryAdapter()
    simulator = handshake.WorkflowHandshakeSimulator(
        adapter,
        process_delay=0.5,
        workflow=handshake.ROBOT_ATOMIC_SINGLE_SAMPLE_WORKFLOW,
    )
    simulator.initialize()
    runtime = SzlabPackageRuntime(
        scenario=handshake.ROBOT_ATOMIC_SINGLE_SAMPLE_WORKFLOW,
    )

    # 任务 23 是放料阶段；夹爪持料且 S11 目标为空才允许接单。
    adapter.write(handshake.ROBOT_TOOL_PAYLOAD_SENSOR, True)
    adapter.write(handshake.S11_ROBOT_PRODUCT, 1)
    adapter.write(handshake.S11_ROBOT_POSITION, 1)
    adapter.write(handshake.ROBOT_TASK_NUMBER, 23)
    adapter.write(handshake.ROBOT_WRITE_DONE, True)

    accepted = simulator.step(now=0.0)
    completed = simulator.step(now=0.5)
    for event in accepted + completed:
        runtime.observe(event)

    # 复现真实 Edge 的消费顺序：完成后清空 S11 参数并撤回写入信号。
    adapter.write(handshake.S11_ROBOT_PRODUCT, 0)
    adapter.write(handshake.S11_ROBOT_POSITION, 0)
    adapter.write(handshake.ROBOT_WRITE_DONE, False)
    reset = simulator.step(now=0.6)
    for event in reset:
        runtime.observe(event)

    assert [event.action for event in accepted + completed + reset] == [
        handshake.ATOMIC_PICK_POUR_PLACE_ACTION,
        handshake.ATOMIC_PICK_POUR_PLACE_ACTION,
        handshake.ATOMIC_PICK_POUR_PLACE_ACTION,
    ]


def test_s07_completion_does_not_clear_concurrent_s081_presence() -> None:
    """S07 注粉完成不得覆盖并发工作流已经写入的 S081 在位信号。"""

    adapter = MemoryAdapter()
    simulator = handshake.WorkflowHandshakeSimulator(
        adapter,
        process_delay=0.5,
        workflow=handshake.DUAL_TASK_ROBOT_ATOMIC_WORKFLOW,
    )
    simulator.initialize()
    runtime = SzlabPackageRuntime(
        scenario=handshake.DUAL_TASK_ROBOT_ATOMIC_WORKFLOW,
    )

    # 任务 17 将夹爪中的样品瓶放到 S081。
    adapter.write(handshake.ROBOT_TOOL_PAYLOAD_SENSOR, True)
    adapter.write(handshake.S08_ROBOT_POSITION, 1)
    adapter.write(handshake.ROBOT_TASK_NUMBER, 17)
    adapter.write(handshake.ROBOT_WRITE_DONE, True)
    for event in simulator.step(now=0.0) + simulator.step(now=0.5):
        runtime.observe(event)

    adapter.write(handshake.ROBOT_WRITE_DONE, False)
    for event in simulator.step(now=0.6):
        runtime.observe(event)

    # 另一条工作流并发完成 S07 注粉。
    adapter.write(handshake.S07_PROCESS, 3)
    adapter.write(handshake.S07_PARAMS_WRITTEN, True)
    for event in simulator.step(now=1.0) + simulator.step(now=1.5):
        runtime.observe(event)

    sensor = handshake.S08_CAP_STATION_SENSOR[1]
    assert adapter.read(sensor) is True
    assert runtime.snapshot()["world"]["flags"][f"opc:{sensor}"] is True

    s072_sensor = handshake.s072_sensor(1)
    assert adapter.read(s072_sensor) is False
    assert runtime.snapshot()["world"]["flags"][f"opc:{s072_sensor}"] is False


def test_s072_presence_signals_do_not_alias_s08_or_s11_sites() -> None:
    """S072 仿真在位信号不得占用 S08 或 S11 的真实库位信号。"""

    s072_sensors = set(handshake.S072_SENSOR_BY_POSITION.values())
    s08_sensors = set(handshake.S08_CAP_STATION_SENSOR.values())
    s11_sensors = {
        handshake.s11_sensor(product_type, position)
        for product_type in (1, 2)
        for position in range(1, 19)
    }

    assert s072_sensors.isdisjoint(s08_sensors)
    assert s072_sensors.isdisjoint(s11_sensors)


def test_attachment_flow_completes_s07_dose_without_prepare_or_scan() -> None:
    """证明新流程可直接完成 S07 双粉桶注粉握手，不再走转盘准备工艺。

    参数：无。
    返回：无；验证工艺 3 的 accepted/completed/reset 边沿和动作身份。
    """

    adapter = MemoryAdapter()
    simulator = handshake.WorkflowHandshakeSimulator(
        adapter,
        process_delay=0.5,
        workflow=handshake.ATTACHMENT_SINGLE_SAMPLE_WORKFLOW,
    )
    simulator.initialize()

    adapter.write(handshake.S07_PROCESS, 3)
    adapter.write(handshake.S07_PARAMS_WRITTEN, True)
    accepted = simulator.step(now=0.0)
    completed = simulator.step(now=0.5)

    assert [(event.action, event.phase) for event in accepted] == [
        (handshake.SINGLE_SAMPLE_S07_DOSE_ACTION, "accepted")
    ]
    assert [(event.action, event.phase) for event in completed] == [
        (handshake.SINGLE_SAMPLE_S07_DOSE_ACTION, "completed")
    ]
    assert adapter.read(handshake.S07_DONE) == 3

    adapter.write(handshake.S07_PROCESS, 0)
    adapter.write(handshake.S07_PARAMS_WRITTEN, False)
    reset = simulator.step(now=0.6)
    assert [(event.action, event.phase) for event in reset] == [
        (handshake.SINGLE_SAMPLE_S07_DOSE_ACTION, "reset")
    ]

    assert simulator.completed_actions == 1
    assert simulator.all_cycles_idle() is True
