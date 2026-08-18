from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

import szlab_handshake_agent as handshake


class MemoryAdapter:
    def __init__(self) -> None:
        self.values: dict[str, Any] = {
            handshake.ROBOT_TASK_NUMBER: 0,
            handshake.S04_ROBOT_POSITION: 0,
            handshake.S06_PROCESS: 0,
            handshake.S06_PARAMS_WRITTEN: False,
            handshake.s04_process(1): 0,
            handshake.s04_params_written(1): False,
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


def test_catalog_matches_official_workflow_snapshot() -> None:
    """验证 PLC-Sim 目录包含 19 个 SZLab 官方工作流和两个双 TASK 扩展。

    参数：无。
    返回：无；断言工作流（Workflow）标识和动作目录。
    """

    specs = handshake.build_workflow_specs()

    assert len(specs) == 21
    assert len(handshake.SUPPORTED_ACTIONS) == 37
    assert {item.workflow_id for item in specs} == {
        "szlab_magnetic_stirring_workflow",
        "szlab_photoshotting_workflow",
        "szlab_robot_action_workflow",
        "s04_robot_stirring_workflow",
        "s06_robot_workflow",
        "s07_robot_workflow",
        "szlab_s07_solid_addition_workflow",
        "s08_cap_workflow",
        "s09_移液调试",
        "szlab_stack_s05_s06_workflow",
        "szlab_mixer_workflow",
        "szlab_mixer_pump_production",
        "szlab_material_s06_workflow",
        "s07_粉桶与烧杯搬运后固体称量",
        "s_z_lab_标准物料转运",
        "s_z_lab_单样品全流程_物料感知",
        "s_z_lab_单样品原子流程_无_s07_扫码",
        "s_z_lab_单样品原子流程_机器人原子动作",
        "s_z_lab_双任务单样品原子流程_无_s07_扫码",
        "s_z_lab_双任务单样品原子流程_机器人原子动作",
        "s_z_lab_烧杯五工位搬运",
    }

    catalog_actions = {
        action.split("(", maxsplit=1)[0] for spec in specs for action in spec.actions
    }
    assert set(handshake.SUPPORTED_ACTIONS) == catalog_actions


def test_workflow_catalog_uses_canonical_names_and_accepts_legacy_aliases(
    capsys: Any,
) -> None:
    assert handshake.WORKFLOW_ALIASES == {
        "s07_material_dosing": handshake.S07_MATERIAL_WORKFLOW,
        "szlab_s09_pipetting_workflow": handshake.S09_WORKFLOW,
    }
    simulator = handshake.WorkflowHandshakeSimulator(
        MemoryAdapter(),
        workflow="szlab_s09_pipetting_workflow",
    )
    assert simulator.workflow == handshake.S09_WORKFLOW

    assert handshake.main(["list", "--workflow", "szlab_s09_pipetting_workflow"]) == 0
    output = capsys.readouterr().out
    assert f"[{handshake.S09_WORKFLOW}]" in output


def test_s07_material_dosing_catalogs_standard_transfers_and_material_join() -> None:
    specs = handshake.build_workflow_specs()
    material = next(
        item for item in specs if item.workflow_id == handshake.S07_MATERIAL_WORKFLOW
    )

    assert material.actions == (
        "szlab_mixer_robot.pick",
        "szlab_s07_solid_addition.prepare_powder_cartridge_site",
        "szlab_mixer_robot.place",
        "host_node.transfer_resource",
        "szlab_mixer_robot.pick",
        "szlab_mixer_robot.place",
        "host_node.transfer_resource",
        "szlab_s07_solid_addition.dose_powder_with_materials",
    )

    adapter = MemoryAdapter()
    simulator = handshake.WorkflowHandshakeSimulator(
        adapter,
        workflow=handshake.S07_MATERIAL_WORKFLOW,
    )
    simulator.initialize()

    assert adapter.read(handshake.S03_BEAKER_SENSOR) is True
    assert adapter.read(handshake.s071_sensor(1)) is True
    assert adapter.read(handshake.s072_sensor(1)) is False
    assert adapter.read(handshake.s072_sensor(2)) is False
    assert adapter.read(handshake.ROBOT_TOOL_PAYLOAD_SENSOR) is False


def test_standard_transfer_updates_site_and_tool_payload_witnesses() -> None:
    """验证标准转运的取放料完成会同步库位与夹爪负载物理证据。"""

    for workflow in (handshake.STANDARD_TRANSFER_WORKFLOW, "all"):
        adapter = MemoryAdapter()
        simulator = handshake.WorkflowHandshakeSimulator(
            adapter,
            process_delay=0.5,
            workflow=workflow,
        )
        simulator.initialize()

        source_sensor = handshake.s03_sensor(1, 1)
        target_sensor = handshake.s04_sensor(1)
        adapter.write(source_sensor, True)
        adapter.write(target_sensor, False)
        adapter.write(handshake.S03_ROBOT_PRODUCT, 1)
        adapter.write(handshake.S03_ROBOT_POSITION, 1)
        adapter.write(handshake.ROBOT_TASK_NUMBER, 6)
        adapter.write(handshake.ROBOT_WRITE_DONE, True)
        assert adapter.read(handshake.ROBOT_TOOL_PAYLOAD_SENSOR) is False

        simulator.step(now=0.0)
        simulator.step(now=0.5)

        assert adapter.read(source_sensor) is False
        assert adapter.read(handshake.ROBOT_TOOL_PAYLOAD_SENSOR) is True

        adapter.write(handshake.ROBOT_WRITE_DONE, False)
        simulator.step(now=0.6)
        adapter.write(handshake.S04_ROBOT_POSITION, 1)
        adapter.write(handshake.ROBOT_TASK_NUMBER, 7)
        adapter.write(handshake.ROBOT_WRITE_DONE, True)

        simulator.step(now=1.0)
        simulator.step(now=1.5)

        assert adapter.read(target_sensor) is True
        assert adapter.read(handshake.ROBOT_TOOL_PAYLOAD_SENSOR) is False


def test_beaker_transfer_chain_skips_selected_site_and_tool_witnesses() -> None:
    """验证五工位握手旁路 S0722/S05/S06 和全部夹爪负载见证。

    参数：无。
    返回：无；断言旁路变量不作为准入条件且在取放期间保持不变。
    """

    spec = next(
        item
        for item in handshake.build_workflow_specs()
        if item.workflow_id == handshake.BEAKER_TRANSFER_CHAIN_WORKFLOW
    )
    required_opcua_subjects = {
        requirement.subject
        for requirement in spec.requirements
        if requirement.kind == "opcua"
    }
    assert handshake.BEAKER_TRANSFER_UNWITNESSED_SITE_SENSORS.isdisjoint(
        required_opcua_subjects
    )

    adapter = MemoryAdapter()
    unmanaged_values = {
        **{
            sensor: True
            for sensor in handshake.BEAKER_TRANSFER_UNWITNESSED_SITE_SENSORS
        },
        handshake.ROBOT_TOOL_PAYLOAD_SENSOR: True,
    }
    adapter.values.update(unmanaged_values)
    simulator = handshake.WorkflowHandshakeSimulator(
        adapter,
        position=1,
        process_delay=0.5,
        workflow=handshake.BEAKER_TRANSFER_CHAIN_WORKFLOW,
    )
    simulator.initialize()

    assert adapter.read(handshake.S03_BEAKER_SENSOR) is True
    for sensor in (
        handshake.S09_STATION_SENSOR[1],
        handshake.s04_sensor(1),
    ):
        assert adapter.read(sensor) is False
    for name, value in unmanaged_values.items():
        assert adapter.read(name) is value

    clock = 0.0
    steps = (
        (6, handshake.S03_BEAKER_SENSOR, False, True),
        (15, handshake.s072_sensor(2), True, False),
        (16, handshake.s072_sensor(2), True, False),
        (11, handshake.S06_BEAKER_SENSOR, True, False),
        (12, handshake.S06_BEAKER_SENSOR, True, False),
        # S09 烧杯位无独立在位传感器，NO[7] 属于 1 号试剂瓶位，必须保持不变。
        (19, handshake.S09_STATION_SENSOR[1], False, False),
        (20, handshake.S09_STATION_SENSOR[1], False, False),
        (7, handshake.s04_sensor(1), True, True),
        (8, handshake.s04_sensor(1), False, True),
        (9, handshake.S05_MATERIAL_SENSOR, True, False),
    )
    for task, sensor, observed, site_witness_enabled in steps:
        if task in (15, 16):
            adapter.write(handshake.S072_ROBOT_PRODUCT, 2)
        elif task in (19, 20):
            adapter.write(handshake.S09_TRANSFER_PRODUCT, 3)
            adapter.write(handshake.S09_TRANSFER_POSITION, 1)
        elif task in (7, 8):
            adapter.write(handshake.S04_ROBOT_POSITION, 1)

        adapter.write(handshake.ROBOT_TASK_NUMBER, task)
        adapter.write(handshake.ROBOT_WRITE_DONE, True)
        simulator.step(now=clock)
        completion_events = simulator.step(now=clock + 0.5)
        assert adapter.read(sensor) is observed
        assert adapter.read(handshake.ROBOT_TOOL_PAYLOAD_SENSOR) is True
        assert (
            completion_events[-1].detail["site_witness_enabled"]
            is site_witness_enabled
        )
        assert completion_events[-1].detail["tool_witness_enabled"] is False

        adapter.write(handshake.ROBOT_WRITE_DONE, False)
        simulator.step(now=clock + 0.6)
        clock += 1.0

    assert adapter.read(handshake.S03_BEAKER_SENSOR) is False
    assert adapter.read(handshake.s072_sensor(2)) is True
    assert adapter.read(handshake.S06_BEAKER_SENSOR) is True
    assert adapter.read(handshake.S09_STATION_SENSOR[1]) is False
    assert adapter.read(handshake.s04_sensor(1)) is False
    assert adapter.read(handshake.S05_MATERIAL_SENSOR) is True
    cleanup = simulator.cleanup_values()
    assert handshake.BEAKER_TRANSFER_UNWITNESSED_SITE_SENSORS.isdisjoint(cleanup)
    assert handshake.ROBOT_TOOL_PAYLOAD_SENSOR not in cleanup
    assert simulator.completed_actions == len(steps)


def test_s06_robot_workflow_starts_with_empty_beaker_station() -> None:
    adapter = MemoryAdapter()
    simulator = handshake.WorkflowHandshakeSimulator(
        adapter,
        workflow="s06_robot_workflow",
    )
    simulator.initialize()

    assert adapter.read(handshake.S06_BEAKER_SENSOR) is False


def test_s04_three_action_handshake_changes_sensor_and_resets() -> None:
    """验证旧式 S04 三动作握手也维护夹爪负载物理见证。"""

    adapter = MemoryAdapter()
    simulator = handshake.WorkflowHandshakeSimulator(
        adapter,
        position=1,
        process_delay=1.0,
    )
    simulator.initialize()
    assert adapter.read(handshake.s04_status(1)) == 1

    adapter.write(handshake.ROBOT_TASK_NUMBER, 7)
    adapter.write(handshake.S04_ROBOT_POSITION, 1)
    adapter.write(handshake.ROBOT_WRITE_DONE, True)
    events = simulator.step(now=0.0)
    assert [(event.action, event.phase) for event in events] == [
        (handshake.SUPPORTED_ACTIONS[0], "accepted")
    ]
    assert adapter.read(handshake.ROBOT_WRITE_ALLOWED) is False

    events = simulator.step(now=1.0)
    assert [(event.action, event.phase) for event in events] == [
        (handshake.SUPPORTED_ACTIONS[0], "completed")
    ]
    assert adapter.read(handshake.s04_sensor(1)) is True
    assert adapter.read(handshake.ROBOT_TOOL_PAYLOAD_SENSOR) is False
    assert adapter.read(handshake.ROBOT_TASK_COMPLETE) == 7

    adapter.write(handshake.ROBOT_WRITE_DONE, False)
    adapter.write(handshake.ROBOT_TASK_NUMBER, 0)
    simulator.step(now=1.1)
    assert adapter.read(handshake.ROBOT_TASK_COMPLETE) == 0
    assert adapter.read(handshake.ROBOT_WRITE_ALLOWED) is True

    adapter.write(handshake.s04_process(1), 3)
    adapter.write(handshake.s04_params_written(1), True)
    events = simulator.step(now=2.0)
    assert [(event.action, event.phase) for event in events] == [
        (handshake.SUPPORTED_ACTIONS[1], "accepted")
    ]
    assert adapter.read(handshake.s04_status(1)) == 2
    events = simulator.step(now=3.0)
    assert [(event.action, event.phase) for event in events] == [
        (handshake.SUPPORTED_ACTIONS[1], "completed")
    ]
    assert adapter.read(handshake.s04_done(1)) is True
    assert adapter.read(handshake.s04_status(1)) == 1

    adapter.write(handshake.s04_params_written(1), False)
    adapter.write(handshake.s04_process(1), 0)
    simulator.step(now=3.1)
    assert adapter.read(handshake.s04_done(1)) is False
    assert adapter.read(handshake.s04_allow(1)) is True

    adapter.write(handshake.ROBOT_TASK_NUMBER, 8)
    adapter.write(handshake.S04_ROBOT_POSITION, 1)
    adapter.write(handshake.ROBOT_WRITE_DONE, True)
    simulator.step(now=4.0)
    events = simulator.step(now=5.0)
    assert [(event.action, event.phase) for event in events] == [
        (handshake.SUPPORTED_ACTIONS[2], "completed")
    ]
    assert adapter.read(handshake.s04_sensor(1)) is False
    assert adapter.read(handshake.ROBOT_TOOL_PAYLOAD_SENSOR) is True
    assert simulator.completed_actions == 3


def test_s06_handshake_produces_fresh_done_cycle() -> None:
    adapter = MemoryAdapter()
    simulator = handshake.WorkflowHandshakeSimulator(
        adapter,
        pump=1,
        process_delay=0.5,
    )
    simulator.initialize()
    adapter.write(handshake.S06_PROCESS, 1)
    adapter.write(handshake.S06_PARAMS_WRITTEN, True)

    accepted = simulator.step(now=10.0)
    completed = simulator.step(now=10.5)

    assert [(event.action, event.phase) for event in accepted] == [
        (handshake.SUPPORTED_ACTIONS[4], "accepted")
    ]
    assert [(event.action, event.phase) for event in completed] == [
        (handshake.SUPPORTED_ACTIONS[4], "completed")
    ]
    assert adapter.read(handshake.S06_DONE) is True

    adapter.write(handshake.S06_PROCESS, 0)
    adapter.write(handshake.S06_PARAMS_WRITTEN, False)
    reset = simulator.step(now=10.6)
    assert [(event.action, event.phase) for event in reset] == [
        (handshake.SUPPORTED_ACTIONS[4], "reset")
    ]
    assert adapter.read(handshake.S06_DONE) is False
    assert adapter.read(handshake.S06_ALLOW) is True


def test_s06_robot_workflow_runs_place_pump_pick_and_resets_sensor() -> None:
    adapter = MemoryAdapter()
    simulator = handshake.WorkflowHandshakeSimulator(
        adapter,
        pump=1,
        process_delay=0.5,
        workflow="s06_robot_workflow",
    )
    simulator.initialize()
    assert adapter.read(handshake.S06_BEAKER_SENSOR) is False

    adapter.write(handshake.ROBOT_TASK_NUMBER, 11)
    adapter.write(handshake.ROBOT_WRITE_DONE, True)
    accepted = simulator.step(now=0.0)
    completed = simulator.step(now=0.5)
    assert [(event.action, event.phase) for event in accepted] == [
        (handshake.S06_PLACE_ACTION, "accepted")
    ]
    assert [(event.action, event.phase) for event in completed] == [
        (handshake.S06_PLACE_ACTION, "completed")
    ]
    assert adapter.read(handshake.S06_BEAKER_SENSOR) is True
    assert adapter.read(handshake.ROBOT_TASK_COMPLETE) == 11

    adapter.write(handshake.ROBOT_WRITE_DONE, False)
    adapter.write(handshake.ROBOT_TASK_NUMBER, 0)
    reset = simulator.step(now=0.6)
    assert [(event.action, event.phase) for event in reset] == [
        (handshake.S06_PLACE_ACTION, "reset")
    ]
    assert adapter.read(handshake.ROBOT_WRITE_ALLOWED) is True
    assert adapter.read(handshake.ROBOT_TASK_COMPLETE) == 0

    adapter.write(handshake.S06_PROCESS, 1)
    adapter.write(handshake.S06_PARAMS_WRITTEN, True)
    accepted = simulator.step(now=1.0)
    completed = simulator.step(now=1.5)
    assert [(event.action, event.phase) for event in accepted] == [
        (handshake.S06_PUMP_ACTION, "accepted")
    ]
    assert [(event.action, event.phase) for event in completed] == [
        (handshake.S06_PUMP_ACTION, "completed")
    ]
    assert adapter.read(handshake.S06_DONE) is True

    adapter.write(handshake.S06_PROCESS, 0)
    adapter.write(handshake.S06_PARAMS_WRITTEN, False)
    reset = simulator.step(now=1.6)
    assert [(event.action, event.phase) for event in reset] == [
        (handshake.S06_PUMP_ACTION, "reset")
    ]
    assert adapter.read(handshake.S06_DONE) is False

    adapter.write(handshake.ROBOT_TASK_NUMBER, 12)
    adapter.write(handshake.ROBOT_WRITE_DONE, True)
    accepted = simulator.step(now=2.0)
    completed = simulator.step(now=2.5)
    assert [(event.action, event.phase) for event in accepted] == [
        (handshake.S06_PICK_ACTION, "accepted")
    ]
    assert [(event.action, event.phase) for event in completed] == [
        (handshake.S06_PICK_ACTION, "completed")
    ]
    assert adapter.read(handshake.S06_BEAKER_SENSOR) is False
    assert adapter.read(handshake.ROBOT_TASK_COMPLETE) == 12
    assert simulator.completed_actions == 3
    assert simulator.all_cycles_idle() is False

    adapter.write(handshake.ROBOT_WRITE_DONE, False)
    adapter.write(handshake.ROBOT_TASK_NUMBER, 0)
    reset = simulator.step(now=2.6)
    assert [(event.action, event.phase) for event in reset] == [
        (handshake.S06_PICK_ACTION, "reset")
    ]
    assert simulator.all_cycles_idle() is True


def test_material_s06_workflow_tracks_s03_s06_with_standard_transfer_actions() -> None:
    """验证 S06 物料流程使用最新标准取放动作并维护夹爪见证。

    参数：无。
    返回：无；断言 S03 取料、S06 放料/取料与工艺动作顺序。
    """

    adapter = MemoryAdapter()
    simulator = handshake.WorkflowHandshakeSimulator(
        adapter,
        pump=1,
        process_delay=0.5,
        workflow="szlab_material_s06_workflow",
    )
    simulator.initialize()
    assert adapter.read(handshake.S03_BEAKER_SENSOR) is True
    assert adapter.read(handshake.S06_BEAKER_SENSOR) is False

    clock = 0.0
    for task_number, action, expected_sensor, expected_value in (
        (6, "szlab_mixer_robot.pick", handshake.S03_BEAKER_SENSOR, False),
        (11, "szlab_mixer_robot.place", handshake.S06_BEAKER_SENSOR, True),
    ):
        adapter.write(handshake.ROBOT_TASK_NUMBER, task_number)
        adapter.write(handshake.ROBOT_WRITE_DONE, True)
        accepted = simulator.step(now=clock)
        completed = simulator.step(now=clock + 0.5)
        assert [(event.action, event.phase) for event in accepted] == [
            (action, "accepted")
        ]
        assert [(event.action, event.phase) for event in completed] == [
            (action, "completed")
        ]
        assert adapter.read(expected_sensor) is expected_value
        adapter.write(handshake.ROBOT_WRITE_DONE, False)
        simulator.step(now=clock + 0.6)
        clock += 1.0

    adapter.write(handshake.S06_PROCESS, 1)
    adapter.write(handshake.S06_PARAMS_WRITTEN, True)
    accepted = simulator.step(now=clock)
    completed = simulator.step(now=clock + 0.5)
    assert [(event.action, event.phase) for event in accepted] == [
        (handshake.MATERIAL_S06_ADD_ACTION, "accepted")
    ]
    assert [(event.action, event.phase) for event in completed] == [
        (handshake.MATERIAL_S06_ADD_ACTION, "completed")
    ]
    adapter.write(handshake.S06_PARAMS_WRITTEN, False)
    simulator.step(now=clock + 0.6)

    adapter.write(handshake.ROBOT_TASK_NUMBER, 12)
    adapter.write(handshake.ROBOT_WRITE_DONE, True)
    accepted = simulator.step(now=clock + 1.0)
    completed = simulator.step(now=clock + 1.5)
    assert [(event.action, event.phase) for event in accepted] == [
        ("szlab_mixer_robot.pick", "accepted")
    ]
    assert [(event.action, event.phase) for event in completed] == [
        ("szlab_mixer_robot.pick", "completed")
    ]
    assert adapter.read(handshake.S06_BEAKER_SENSOR) is False
    assert simulator.completed_actions == 4


def test_s07_robot_workflow_runs_three_tasks_and_rearms_next_cycle() -> None:
    adapter = MemoryAdapter()
    simulator = handshake.WorkflowHandshakeSimulator(
        adapter,
        process_delay=0.5,
        workflow="s07_robot_workflow",
    )
    simulator.initialize()

    clock = 0.0
    for task_number, position in ((13, 1), (15, 0), (16, 0)):
        adapter.write(handshake.ROBOT_TASK_NUMBER, task_number)
        adapter.write(handshake.ROBOT_WRITE_DONE, True)
        if task_number == 13:
            adapter.write(handshake.S071_ROBOT_POSITION, position)
        elif task_number in (15, 16):
            adapter.write(handshake.S072_ROBOT_PRODUCT, 1)

        accepted = simulator.step(now=clock)
        completed = simulator.step(now=clock + 0.5)
        assert [(event.phase, event.detail["task_number"]) for event in accepted] == [
            ("accepted", task_number)
        ]
        assert [(event.phase, event.detail["task_number"]) for event in completed] == [
            ("completed", task_number)
        ]

        adapter.write(handshake.ROBOT_WRITE_DONE, False)
        adapter.write(handshake.ROBOT_TASK_NUMBER, 0)
        reset = simulator.step(now=clock + 0.6)
        assert [(event.phase, event.detail["task_number"]) for event in reset] == [
            ("reset", task_number)
        ]
        clock += 1.0

    assert adapter.read(handshake.s071_sensor(1)) is False
    assert adapter.read(handshake.s072_sensor(1)) is False
    assert simulator.completed_actions == 3
    assert simulator.all_cycles_idle() is True


def test_s072_product_selector_updates_two_independent_handoff_sensors() -> None:
    adapter = MemoryAdapter()
    simulator = handshake.WorkflowHandshakeSimulator(
        adapter,
        process_delay=0.5,
        workflow=handshake.S07_MATERIAL_WORKFLOW,
    )
    simulator.initialize()

    clock = 0.0
    for product in (2, 1):
        adapter.write(handshake.S072_ROBOT_PRODUCT, product)
        adapter.write(handshake.ROBOT_TASK_NUMBER, 15)
        adapter.write(handshake.ROBOT_WRITE_DONE, True)
        simulator.step(now=clock)
        simulator.step(now=clock + 0.5)
        assert adapter.read(handshake.s072_sensor(product)) is True
        adapter.write(handshake.ROBOT_WRITE_DONE, False)
        adapter.write(handshake.ROBOT_TASK_NUMBER, 0)
        simulator.step(now=clock + 0.6)
        clock += 1.0

    assert adapter.read(handshake.s072_sensor(1)) is True
    assert adapter.read(handshake.s072_sensor(2)) is True


def test_s07_solid_handshake_supports_two_complete_cycles() -> None:
    adapter = MemoryAdapter()
    simulator = handshake.WorkflowHandshakeSimulator(adapter, process_delay=0.5)
    simulator.initialize()

    clock = 0.0
    for process in (1, 2, 3, 1, 2, 3):
        adapter.write(handshake.S07_PROCESS, process)
        adapter.write(handshake.S07_PARAMS_WRITTEN, True)
        accepted = simulator.step(now=clock)
        completed = simulator.step(now=clock + 0.5)
        assert [(event.action, event.phase) for event in accepted] == [
            (handshake.S07_SOLID_ACTION_BY_PROCESS[process], "accepted")
        ]
        assert [(event.action, event.phase) for event in completed] == [
            (handshake.S07_SOLID_ACTION_BY_PROCESS[process], "completed")
        ]
        assert adapter.read(handshake.S07_DONE) == process
        if process == 3:
            assert adapter.read(handshake.S07_BALANCE_READING) == 1.0

        adapter.write(handshake.S07_PROCESS, 0)
        adapter.write(handshake.S07_PARAMS_WRITTEN, False)
        reset = simulator.step(now=clock + 0.6)
        assert [(event.action, event.phase) for event in reset] == [
            (handshake.S07_SOLID_ACTION_BY_PROCESS[process], "reset")
        ]
        assert adapter.read(handshake.S07_DONE) == 0
        assert adapter.read(handshake.S07_ALLOW) is True
        clock += 1.0

    assert simulator.completed_actions == 6
    assert simulator.all_cycles_idle() is True


def test_s08_open_close_handshake_supports_two_complete_cycles() -> None:
    adapter = MemoryAdapter()
    simulator = handshake.WorkflowHandshakeSimulator(adapter, process_delay=0.5)
    simulator.initialize()
    assert adapter.read(handshake.S08_CAP_STATION_SENSOR[2]) is True
    assert adapter.read(handshake.S08_CAP_STORAGE_SENSOR[1]) is False

    clock = 0.0
    for process in (5, 6, 5, 6):
        adapter.write(handshake.S08_PROCESS, process)
        adapter.write(handshake.S08_PARAMS_WRITTEN, True)
        adapter.write(handshake.S08_CAP_STORAGE_SLOT, 1)
        accepted = simulator.step(now=clock)
        completed = simulator.step(now=clock + 0.5)
        assert [(event.action, event.phase) for event in accepted] == [
            (handshake.S08_CAP_ACTION, "accepted")
        ]
        assert [(event.action, event.phase) for event in completed] == [
            (handshake.S08_CAP_ACTION, "completed")
        ]
        assert adapter.read(handshake.S08_DONE) == process
        assert adapter.read(handshake.S08_CAP_STORAGE_SENSOR[1]) is (process == 5)

        adapter.write(handshake.S08_PROCESS, 0)
        adapter.write(handshake.S08_PARAMS_WRITTEN, False)
        adapter.write(handshake.S08_CAP_STORAGE_SLOT, 0)
        reset = simulator.step(now=clock + 0.6)
        assert [(event.action, event.phase) for event in reset] == [
            (handshake.S08_CAP_ACTION, "reset")
        ]
        assert adapter.read(handshake.S08_DONE) == 0
        assert adapter.read(handshake.S08_ALLOW) is True
        clock += 1.0

    assert simulator.completed_actions == 4
    assert simulator.all_cycles_idle() is True


def test_s09_add_liquid_handshake_supports_two_complete_sequences() -> None:
    adapter = MemoryAdapter()
    simulator = handshake.WorkflowHandshakeSimulator(
        adapter,
        process_delay=0.5,
        workflow=handshake.S09_WORKFLOW,
    )
    simulator.initialize()
    assert adapter.read(handshake.S09_TIP_BOX_SENSOR[1]) is True
    assert adapter.read(handshake.S09_STATION_SENSOR[1]) is True

    adapter.write(handshake.S09_PROCESS, 5)
    adapter.write(handshake.S09_PARAMS_WRITTEN, False)
    assert simulator.step(now=-1.0) == []

    clock = 0.0
    for process in (5, 7, 8, 6, 5, 7, 8, 6):
        adapter.write(handshake.S09_PROCESS, process)
        adapter.write(handshake.S09_PARAMS_WRITTEN, True)
        accepted = simulator.step(now=clock)
        completed = simulator.step(now=clock + 0.5)
        assert [(event.action, event.phase) for event in accepted] == [(handshake.S09_ADD_LIQUID_ACTION, "accepted")]
        assert [(event.action, event.phase) for event in completed] == [(handshake.S09_ADD_LIQUID_ACTION, "completed")]
        assert adapter.read(handshake.S09_DONE) == process
        if process == 8:
            assert adapter.read(handshake.S09_BALANCE_READING) == 1.0

        adapter.write(handshake.S09_PROCESS, 0)
        adapter.write(handshake.S09_PARAMS_WRITTEN, False)
        reset = simulator.step(now=clock + 0.6)
        assert [(event.action, event.phase) for event in reset] == [(handshake.S09_ADD_LIQUID_ACTION, "reset")]
        assert adapter.read(handshake.S09_DONE) == 0
        assert adapter.read(handshake.S09_ALLOW) is True
        clock += 1.0

    assert simulator.completed_actions == 8
    assert simulator.all_cycles_idle() is True


def test_s09_density_done_writes_balance_arrays_not_stable_signal() -> None:
    for workflow in (
        handshake.S09_WORKFLOW,
        handshake.SINGLE_SAMPLE_WORKFLOW,
        handshake.ATTACHMENT_SINGLE_SAMPLE_WORKFLOW,
    ):
        spec = next(
            item
            for item in handshake.build_workflow_specs()
            if item.workflow_id == workflow
        )
        assert handshake.S09_BALANCE_STABLE not in {
            requirement.subject for requirement in spec.requirements
        }
        simulator = handshake.WorkflowHandshakeSimulator(
            MemoryAdapter(),
            workflow=workflow,
        )
        assert handshake.S09_BALANCE_STABLE not in simulator.initialization_values()
        assert handshake.S09_BALANCE_STABLE not in simulator.cleanup_values()

    adapter = MemoryAdapter()
    simulator = handshake.WorkflowHandshakeSimulator(
        adapter,
        process_delay=0.5,
        workflow=handshake.S09_WORKFLOW,
    )
    simulator.initialize()
    adapter.write(handshake.S09_DENSITY_COUNT, 3)
    adapter.write(handshake.S09_PROCESS, 9)
    adapter.write(handshake.S09_PARAMS_WRITTEN, True)
    assert simulator.step(now=0.0)
    assert simulator.step(now=0.5)
    assert adapter.read(handshake.S09_DONE) == 9
    assert adapter.read(f"{handshake.S09_ASPIRATE_BALANCE_READINGS}[0]") == 1.0
    assert adapter.read(f"{handshake.S09_ASPIRATE_BALANCE_READINGS}[2]") == 1.0
    assert adapter.read(f"{handshake.S09_DISPENSE_BALANCE_READINGS}[1]") == 1.0
    assert adapter.read(f"{handshake.S09_ASPIRATE_BALANCE_READINGS}[3]") == 0.0
    assert handshake.S09_BALANCE_STABLE not in adapter.values


def test_single_sample_workflow_drives_standard_robot_and_new_action_names() -> None:
    adapter = MemoryAdapter()
    simulator = handshake.WorkflowHandshakeSimulator(
        adapter,
        process_delay=0.5,
        workflow=handshake.SINGLE_SAMPLE_WORKFLOW,
    )
    simulator.initialize()

    assert adapter.read(handshake.ROBOT_HOME) is True
    assert adapter.read(handshake.S03_BEAKER_SENSOR) is True
    assert adapter.read(handshake.S03_SAMPLE_VIAL_SENSOR) is True
    assert adapter.read(handshake.s071_sensor(1)) is True
    assert adapter.read(handshake.s071_sensor(2)) is True
    assert adapter.read(handshake.s10_sensor(1)) is True
    assert adapter.read(handshake.s11_sensor(1, 1)) is False
    assert adapter.read(handshake.s11_sensor(2, 1)) is False
    assert adapter.read(handshake.s04_sensor(1)) is False
    assert adapter.read(handshake.S05_MATERIAL_SENSOR) is False
    assert adapter.read(handshake.S06_BEAKER_SENSOR) is False
    assert adapter.read(handshake.s11_sensor(1, 1)) is False
    assert adapter.read(handshake.s11_sensor(2, 1)) is False

    adapter.write(handshake.S03_ROBOT_PRODUCT, 1)
    adapter.write(handshake.S03_ROBOT_POSITION, 1)
    adapter.write(handshake.ROBOT_TASK_NUMBER, 6)
    adapter.write(handshake.ROBOT_WRITE_DONE, True)
    accepted = simulator.step(now=0.0)
    completed = simulator.step(now=0.5)
    assert [(event.action, event.phase) for event in accepted] == [
        ("szlab_mixer_robot.pick", "accepted")
    ]
    assert [(event.action, event.phase) for event in completed] == [
        ("szlab_mixer_robot.pick", "completed")
    ]
    assert adapter.read(handshake.S03_BEAKER_SENSOR) is False

    adapter.write(handshake.ROBOT_WRITE_DONE, False)
    simulator.step(now=0.6)
    adapter.write(handshake.S11_ROBOT_PRODUCT, 1)
    adapter.write(handshake.S11_ROBOT_POSITION, 1)
    adapter.write(handshake.ROBOT_TASK_NUMBER, 23)
    adapter.write(handshake.ROBOT_WRITE_DONE, True)
    accepted = simulator.step(now=1.0)
    completed = simulator.step(now=1.5)
    assert [(event.action, event.phase) for event in accepted] == [
        ("szlab_mixer_robot.place", "accepted")
    ]
    assert [(event.action, event.phase) for event in completed] == [
        ("szlab_mixer_robot.place", "completed")
    ]
    assert adapter.read(handshake.s11_sensor(1, 1)) is True

    assert simulator._pump_action() == handshake.SINGLE_SAMPLE_PUMP_ACTION
    assert simulator._stirrer_action() == handshake.SINGLE_SAMPLE_STIR_ACTION
    assert simulator._s07_action(3) == handshake.SINGLE_SAMPLE_S07_DOSE_ACTION
    assert simulator._s08_action(5) == handshake.SINGLE_SAMPLE_S08_LIQUID_CAP_ACTION
    assert simulator._s08_action(3) == handshake.SINGLE_SAMPLE_S08_SAMPLE_CAP_ACTION
    assert simulator._s09_action() == handshake.SINGLE_SAMPLE_S09_ACTION


def test_robot_handshake_accepts_next_task_when_reset_pulse_is_overtaken() -> None:
    """紧邻任务覆盖复位沿时，仿真器仍应先复位旧任务再接受新任务。

    参数：无。
    返回：无；断言旧任务复位和新任务准入同轮发生，且夹爪状态允许连续取放。
    """

    adapter = MemoryAdapter()
    simulator = handshake.WorkflowHandshakeSimulator(
        adapter,
        process_delay=0.5,
        workflow=handshake.SINGLE_SAMPLE_WORKFLOW,
    )
    simulator.initialize()

    # 紧邻任务测试从 S05 取料开始，先提供真实的烧杯在位见证。
    adapter.write(handshake.S05_MATERIAL_SENSOR, True)
    adapter.write(handshake.ROBOT_TASK_NUMBER, 10)
    adapter.write(handshake.ROBOT_WRITE_DONE, True)
    simulator.step(now=0.0)
    simulator.step(now=0.5)
    assert adapter.read(handshake.ROBOT_TASK_COMPLETE) == 10

    # Edge 已经开始下一条任务，PLC 仿真轮询未观察到中间极短的 False。
    adapter.write(handshake.S11_ROBOT_PRODUCT, 1)
    adapter.write(handshake.S11_ROBOT_POSITION, 1)
    adapter.write(handshake.ROBOT_TASK_NUMBER, 23)
    events = simulator.step(now=0.6)

    assert [(event.phase, event.detail["task_number"]) for event in events] == [
        ("reset", 10),
        ("accepted", 23),
    ]
    assert adapter.read(handshake.ROBOT_TASK_COMPLETE) == 0
    completed = simulator.step(now=1.1)
    assert [(event.phase, event.detail["task_number"]) for event in completed] == [
        ("completed", 23)
    ]


def test_robot_handshake_waits_for_complete_s09_parameter_snapshot() -> None:
    """旧完成沿与已复位 S09 参数并存时应等待稳定快照，不能退出 Agent。"""

    adapter = MemoryAdapter()
    simulator = handshake.WorkflowHandshakeSimulator(
        adapter,
        process_delay=0.5,
        workflow=handshake.SINGLE_SAMPLE_WORKFLOW,
    )
    simulator.initialize()

    # 模拟 OPC UA 多节点写入窗口：任务号和旧 True 已可见，产品/位置仍为 0。
    adapter.write(handshake.ROBOT_TASK_NUMBER, 20)
    adapter.write(handshake.S09_TRANSFER_PRODUCT, 0)
    adapter.write(handshake.S09_TRANSFER_POSITION, 0)
    adapter.write(handshake.ROBOT_WRITE_DONE, True)

    assert simulator.step(now=0.0) == []
    assert simulator.robot.phase == "idle"
    assert adapter.read(handshake.ROBOT_HOME) is True

    # 下一轮参数完整后，同一任务正常接纳；前一次瞬时快照没有产生物理动作。
    adapter.write(handshake.S09_TRANSFER_PRODUCT, 3)
    adapter.write(handshake.S09_TRANSFER_POSITION, 1)
    events = simulator.step(now=0.1)

    assert [(event.phase, event.detail["task_number"]) for event in events] == [
        ("accepted", 20)
    ]
    assert simulator.robot.phase == "executing"


def test_s09_transfer_accepts_addition_and_density_beakers_at_shared_site() -> None:
    """SZLab 协议中产品 3/4 都是 S09 BEAKER1，且都无独立在位信号。"""

    assert handshake.s09_transfer_sensor(3, 1) == ""
    assert handshake.s09_transfer_sensor(4, 1) == ""


def test_robot_handshake_accepts_s09_density_beaker_transfer() -> None:
    adapter = MemoryAdapter()
    simulator = handshake.WorkflowHandshakeSimulator(
        adapter,
        process_delay=0.5,
        workflow=handshake.SINGLE_SAMPLE_WORKFLOW,
    )
    simulator.initialize()

    adapter.write(handshake.ROBOT_TASK_NUMBER, 19)
    adapter.write(handshake.S09_TRANSFER_PRODUCT, 4)
    adapter.write(handshake.S09_TRANSFER_POSITION, 1)
    adapter.write(handshake.ROBOT_WRITE_DONE, True)

    events = simulator.step(now=0.0)

    assert [(event.phase, event.detail["task_number"]) for event in events] == [
        ("accepted", 19)
    ]
    assert simulator.robot.phase == "executing"


def test_single_sample_initialize_and_cleanup_reset_s11_output_slots() -> None:
    adapter = MemoryAdapter()
    simulator = handshake.WorkflowHandshakeSimulator(
        adapter,
        workflow=handshake.SINGLE_SAMPLE_WORKFLOW,
    )
    beaker_slot = handshake.s11_sensor(1, 1)
    sample_vial_slot = handshake.s11_sensor(2, 1)

    adapter.write(beaker_slot, True)
    adapter.write(sample_vial_slot, True)
    simulator.initialize()
    assert adapter.read(beaker_slot) is False
    assert adapter.read(sample_vial_slot) is False

    adapter.write(beaker_slot, True)
    adapter.write(sample_vial_slot, True)
    simulator.cleanup()
    assert adapter.read(beaker_slot) is False
    assert adapter.read(sample_vial_slot) is False


def test_cli_keeps_workflow_selector_compatibility(capsys: Any) -> None:
    args = handshake.build_parser().parse_args(
        ["serve", "--workflow", "s06_robot_workflow"]
    )
    assert args.workflow == "s06_robot_workflow"

    assert handshake.main(["list", "--workflow", "s06_robot_workflow"]) == 0
    output = capsys.readouterr().out
    assert "当前工作流数量: 1" in output
    assert "[s06_robot_workflow]" in output


def test_selected_workflow_only_initializes_and_polls_its_components() -> None:
    adapter = MemoryAdapter()
    simulator = handshake.WorkflowHandshakeSimulator(
        adapter,
        workflow="szlab_magnetic_stirring_workflow",
    )

    assert simulator.enabled_components == frozenset({"stirrer"})
    assert simulator.initialization_values() == {
        handshake.s04_sensor(1): True,
        handshake.s04_allow(1): True,
        handshake.s04_status(1): 1,
        handshake.s04_done(1): False,
    }
    simulator.initialize()
    simulator.step(now=0.0)


def test_s04_completion_uses_driver_duration_instead_of_default_delay() -> None:
    adapter = MemoryAdapter()
    simulator = handshake.WorkflowHandshakeSimulator(
        adapter,
        workflow="szlab_magnetic_stirring_workflow",
        process_delay=0.1,
    )
    simulator.initialize()
    adapter.write(handshake.s04_duration(1), 30_000)
    adapter.write(handshake.s04_process(1), 3)
    adapter.write(handshake.s04_params_written(1), True)

    accepted = simulator.step(now=2.0)
    assert accepted[0].detail["duration_seconds"] == 30.0
    assert simulator.step(now=31.99) == []
    completed = simulator.step(now=32.0)

    assert [(event.phase, event.detail["duration_seconds"]) for event in completed] == [
        ("completed", 30.0)
    ]
    assert adapter.read(handshake.s04_done(1)) is True


def test_every_handshake_variable_exists_in_deployment_plc_csvs() -> None:
    variables = {
        handshake.ROBOT_TASK_NUMBER,
        handshake.S04_ROBOT_POSITION,
        handshake.S071_ROBOT_POSITION,
        handshake.s04_process(1),
        handshake.s04_params_written(1),
        handshake.S06_PROCESS,
        handshake.S06_PARAMS_WRITTEN,
        handshake.S07_PROCESS,
        handshake.S07_PARAMS_WRITTEN,
        handshake.S08_PROCESS,
        handshake.S08_PARAMS_WRITTEN,
        handshake.S08_CAP_STORAGE_SLOT,
        handshake.S09_PROCESS,
        handshake.S09_PARAMS_WRITTEN,
    }
    for workflow in handshake.WORKFLOW_IDS:
        simulator = handshake.WorkflowHandshakeSimulator(
            MemoryAdapter(),
            pump=3,
            workflow=workflow,
        )
        variables.update(simulator.initialization_values())
        variables.update(simulator.cleanup_values())

    csv_path = Path(__file__).parents[1] / "data" / "szlab_plc_0810.csv"
    text = None
    for encoding in ("utf-8-sig", "utf-16", "utf-16-le", "utf-8", "gbk", "gb18030"):
        try:
            text = csv_path.read_text(encoding=encoding)
            break
        except UnicodeDecodeError:
            continue
    assert text is not None
    first_line = text.splitlines()[0] if text.splitlines() else ""
    delimiter = "\t" if first_line.count("\t") > first_line.count(",") else ","
    rows = csv.reader(text.splitlines(), delimiter=delimiter)
    csv_variables = {
        row[1].strip() for row in rows if len(row) > 1 and row[1].strip()
    }

    missing = sorted(variables - csv_variables)
    assert not missing, missing


def test_cleanup_only_resets_simulator_owned_outputs() -> None:
    adapter = MemoryAdapter()
    simulator = handshake.WorkflowHandshakeSimulator(adapter)
    simulator.initialize()
    adapter.write(handshake.ROBOT_TASK_NUMBER, 7)

    simulator.cleanup()

    assert adapter.read(handshake.ROBOT_HOME) is False
    assert adapter.read(handshake.ROBOT_WRITE_ALLOWED) is False
    assert adapter.read(handshake.s04_sensor(1)) is False
    assert adapter.read(handshake.S05_RESULT) == 0
    assert adapter.read(handshake.S06_BEAKER_SENSOR) is False
    assert adapter.read(handshake.S06_STORAGE_BOTTLE_SENSOR[1]) is False
    assert adapter.read(handshake.ROBOT_TASK_NUMBER) == 7


def test_opcua_adapter_reconnects_and_retries_a_timeout(monkeypatch: Any) -> None:
    import opcua

    clients: list[Any] = []

    class FakeNode:
        def __init__(self, client_number: int) -> None:
            self.client_number = client_number

        def get_value(self) -> int:
            if self.client_number == 1:
                raise TimeoutError
            return 42

        def get_data_type_as_variant_type(self) -> object:
            return object()

    class FakeClient:
        def __init__(self, url: str, timeout: float) -> None:
            self.url = url
            self.timeout = timeout
            self.number = len(clients) + 1
            self.connected = False
            clients.append(self)

        def connect(self) -> None:
            self.connected = True

        def disconnect(self) -> None:
            self.connected = False

        def get_node(self, _node_id: str) -> FakeNode:
            return FakeNode(self.number)

    monkeypatch.setattr(opcua, "Client", FakeClient)
    monkeypatch.setattr(handshake.time, "sleep", lambda _delay: None)
    adapter = handshake.OpcUaVariableAdapter(
        "opc.tcp://example.invalid:4840/sim",
        "ns=4;s=上位机通讯|",
    )
    adapter.connect()

    assert adapter.read("S06工艺选择") == 42
    assert len(clients) == 2
    assert all(client.timeout == 10 for client in clients)
