from __future__ import annotations

from typing import Any

import szlab_handshake_agent as handshake


class MemoryAdapter:
    """为新工作流握手切片保存可观察的 PLC 变量值。"""

    def __init__(self) -> None:
        self.values: dict[str, Any] = {
            handshake.ROBOT_TASK_NUMBER: 0,
            handshake.S04_ROBOT_POSITION: 0,
            handshake.s04_process(1): 0,
            handshake.s04_params_written(1): False,
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
    assert len(specs) == 19
    assert attachment.actions == (
        "szlab_mixer_robot.pick",
        "szlab_mixer_robot.place",
        "host_node.transfer_resource",
        "szlab_s08_cap_station.process_liquid_reagent_100ml_cap_with_material",
        "szlab_s07_solid_addition.dose_powder_with_two_materials",
        "szlab_mixer_pump.add_solvent_with_materials",
        "szlab_mixer_pipetting_station.add_liquid_with_materials",
        "szlab_mixer_stirrer.stir_beaker",
        "szlab_s08_cap_station.process_sample_vial_250ml_cap_with_material",
        "szlab_mixer_photoshotting.inspect_beaker",
        "szlab_mixer_robot.pick_beaker",
        "szlab_mixer_robot.pour_beaker_into_vial",
    )
    assert "szlab_s07_solid_addition.scan_powder_cartridges" not in attachment.actions
    assert "szlab_s07_solid_addition.prepare_powder_cartridge_site" not in attachment.actions


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
