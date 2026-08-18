from __future__ import annotations

import socket
from typing import Any

import pytest
from opcua import ua

from common import NodeDef
from server import add_nodes, build_server, register_ns_padding
from szlab_handshake_agent import (
    ATOMIC_TRANSFER_ACTION,
    ATTACHMENT_SINGLE_SAMPLE_WORKFLOW,
    DUAL_TASK_ATTACHMENT_WORKFLOW,
    DUAL_TASK_ROBOT_ATOMIC_WORKFLOW,
    ROBOT_HOME,
    ROBOT_TASK_COMPLETE,
    ROBOT_TASK_NUMBER,
    ROBOT_TOOL_PAYLOAD_SENSOR,
    ROBOT_WRITE_ALLOWED,
    ROBOT_WRITE_DONE,
    S03_ROBOT_POSITION,
    S03_ROBOT_PRODUCT,
    S04_ROBOT_POSITION,
    S06_PARAMS_WRITTEN,
    S06_PROCESS,
    S07_PARAMS_WRITTEN,
    S07_PROCESS,
    S08_CAP_STORAGE_SLOT,
    S08_PARAMS_WRITTEN,
    S08_PROCESS,
    S09_PARAMS_WRITTEN,
    S09_PROCESS,
    S09_WORKFLOW,
    OpcUaVariableAdapter,
    WorkflowHandshakeSimulator,
    s03_sensor,
    s04_params_written,
    s04_process,
    s04_sensor,
)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class _MemoryAdapter:
    def __init__(self, values: dict[str, Any]) -> None:
        self.values = values

    def read(self, name: str) -> Any:
        return self.values[name]

    def write(self, name: str, value: Any) -> None:
        self.values[name] = value


def _data_type(value: Any) -> str:
    if isinstance(value, bool):
        return "BOOLEAN"
    if isinstance(value, float):
        return "FLOAT"
    return "INT32"


def _definitions(values: dict[str, Any]) -> list[NodeDef]:
    return [
        NodeDef(
            name,
            "",
            "VARIABLE",
            _data_type(value),
            f"ns=4;s=上位机通讯|{name}",
        )
        for name, value in values.items()
    ]


def _start_server(values: dict[str, Any]):
    port = _free_port()
    endpoint = f"opc.tcp://127.0.0.1:{port}/xuse_sim/"
    server = build_server(endpoint)
    namespace_index = register_ns_padding(server, 4, "urn:szlab:test")
    nodes = add_nodes(server, namespace_index, _definitions(values))
    server.start()
    return server, nodes, endpoint


def test_robot_handshake_through_real_opcua_adapter() -> None:
    """验证真实 OPC UA 适配器暴露并更新机器人夹爪负载见证。"""

    seed = {
        ROBOT_TASK_NUMBER: 0,
        S04_ROBOT_POSITION: 0,
    }
    blueprint = WorkflowHandshakeSimulator(
        _MemoryAdapter(seed),
        workflow="szlab_robot_action_workflow",
        process_delay=0.0,
    )
    values = {**blueprint.initialization_values(), **seed}
    server, nodes, endpoint = _start_server(values)
    adapter = OpcUaVariableAdapter(endpoint, "ns=4;s=上位机通讯|")
    simulator = WorkflowHandshakeSimulator(
        adapter,
        workflow="szlab_robot_action_workflow",
        process_delay=0.0,
    )
    try:
        adapter.connect()
        simulator.initialize()
        nodes[S04_ROBOT_POSITION].set_value(ua.Variant(1, ua.VariantType.Int32))
        nodes[ROBOT_TASK_NUMBER].set_value(ua.Variant(7, ua.VariantType.Int32))
        nodes[ROBOT_WRITE_DONE].set_value(ua.Variant(True, ua.VariantType.Boolean))

        events = simulator.step(now=1.0) + simulator.step(now=1.0)

        assert [(event.phase, event.detail["task_number"]) for event in events] == [
            ("accepted", 7),
            ("completed", 7),
        ]
        assert nodes["Robot_任务完成"].get_value() == 7
        assert nodes["Robot_任务允许写入"].get_value() is False
        assert nodes[s04_sensor(1)].get_value() is True
        assert nodes[ROBOT_TOOL_PAYLOAD_SENSOR].get_value() is False

        nodes[ROBOT_WRITE_DONE].set_value(ua.Variant(False, ua.VariantType.Boolean))
        simulator.step(now=1.01)
        assert nodes["Robot_任务完成"].get_value() == 0
        assert nodes["Robot_任务允许写入"].get_value() is True
    finally:
        adapter.disconnect()
        server.stop()


@pytest.mark.parametrize(
    ("workflow", "expected_action"),
    (
        (DUAL_TASK_ATTACHMENT_WORKFLOW, "szlab_mixer_robot.pick"),
        (DUAL_TASK_ROBOT_ATOMIC_WORKFLOW, ATOMIC_TRANSFER_ACTION),
    ),
)
def test_dual_task_second_pick_is_rejected_through_real_opcua_adapter(
    workflow: str,
    expected_action: str,
) -> None:
    """验证真实 OPC UA 路径拒绝两个双 TASK 场景的第二次取料。

    参数：``workflow`` 是双 TASK 工作流（Workflow），``expected_action`` 是事件动作标识。
    返回：无；断言拒绝命令不会生成完成码、不会移除 Task B 源物料或释放夹爪。
    """

    # PC 写入节点必须与 PLC 输出初值一起预先建入 OPC UA 地址空间。
    seed = {
        ROBOT_TASK_NUMBER: 0,
        S03_ROBOT_PRODUCT: 0,
        S03_ROBOT_POSITION: 0,
        S04_ROBOT_POSITION: 0,
        s04_process(1): 0,
        s04_params_written(1): False,
        S06_PROCESS: 0,
        S06_PARAMS_WRITTEN: False,
        S07_PROCESS: 0,
        S07_PARAMS_WRITTEN: False,
        S08_PROCESS: 0,
        S08_PARAMS_WRITTEN: False,
        S08_CAP_STORAGE_SLOT: 0,
        S09_PROCESS: 0,
        S09_PARAMS_WRITTEN: False,
    }
    blueprint = WorkflowHandshakeSimulator(
        _MemoryAdapter(seed),
        workflow=workflow,
        process_delay=0.0,
    )
    values = {**blueprint.initialization_values(), **seed}
    server, nodes, endpoint = _start_server(values)
    adapter = OpcUaVariableAdapter(endpoint, "ns=4;s=上位机通讯|")
    simulator = WorkflowHandshakeSimulator(
        adapter,
        workflow=workflow,
        process_delay=0.0,
    )
    try:
        adapter.connect()
        simulator.initialize()

        nodes[S03_ROBOT_PRODUCT].set_value(ua.Variant(1, ua.VariantType.Int32))
        nodes[S03_ROBOT_POSITION].set_value(ua.Variant(1, ua.VariantType.Int32))
        nodes[ROBOT_TASK_NUMBER].set_value(ua.Variant(6, ua.VariantType.Int32))
        nodes[ROBOT_WRITE_DONE].set_value(
            ua.Variant(True, ua.VariantType.Boolean)
        )
        first_pick = simulator.step(now=1.0) + simulator.step(now=1.0)
        assert [event.phase for event in first_pick] == ["accepted", "completed"]
        assert {event.action for event in first_pick} == {expected_action}
        assert nodes[ROBOT_TOOL_PAYLOAD_SENSOR].get_value() is True
        assert nodes[s03_sensor(1, 1)].get_value() is False

        nodes[ROBOT_WRITE_DONE].set_value(
            ua.Variant(False, ua.VariantType.Boolean)
        )
        simulator.step(now=1.01)
        nodes[S03_ROBOT_POSITION].set_value(ua.Variant(2, ua.VariantType.Int32))
        nodes[ROBOT_WRITE_DONE].set_value(
            ua.Variant(True, ua.VariantType.Boolean)
        )
        rejected = simulator.step(now=2.0)

        assert [(event.phase, event.detail["reason"]) for event in rejected] == [
            ("rejected", "夹爪已持有物料，禁止再次取料")
        ]
        assert {event.action for event in rejected} == {expected_action}
        assert nodes[ROBOT_HOME].get_value() is True
        assert nodes[ROBOT_WRITE_ALLOWED].get_value() is False
        assert nodes[ROBOT_TASK_COMPLETE].get_value() == 0
        assert nodes[ROBOT_TOOL_PAYLOAD_SENSOR].get_value() is True
        assert nodes[s03_sensor(1, 2)].get_value() is True
        assert simulator.completed_actions == 1
    finally:
        adapter.disconnect()
        server.stop()


def test_latest_s09_cycle_requires_official_parameter_reset() -> None:
    seed = {S09_PROCESS: 0, S09_PARAMS_WRITTEN: False}
    blueprint = WorkflowHandshakeSimulator(
        _MemoryAdapter(seed),
        workflow=S09_WORKFLOW,
        process_delay=0.0,
    )
    values = {**blueprint.initialization_values(), **seed}
    server, nodes, endpoint = _start_server(values)
    adapter = OpcUaVariableAdapter(endpoint, "ns=4;s=上位机通讯|")
    simulator = WorkflowHandshakeSimulator(
        adapter,
        workflow=S09_WORKFLOW,
        process_delay=0.0,
    )
    try:
        adapter.connect()
        simulator.initialize()
        nodes[S09_PROCESS].set_value(ua.Variant(5, ua.VariantType.Int32))
        nodes[S09_PARAMS_WRITTEN].set_value(ua.Variant(True, ua.VariantType.Boolean))

        events = simulator.step(now=2.0) + simulator.step(now=2.0)

        assert [(event.phase, event.detail["process"]) for event in events] == [
            ("accepted", 5),
            ("completed", 5),
        ]
        assert nodes["S09工艺完成"].get_value() == 5

        nodes[S09_PARAMS_WRITTEN].set_value(ua.Variant(False, ua.VariantType.Boolean))
        assert simulator.step(now=2.01) == []
        nodes[S09_PROCESS].set_value(ua.Variant(0, ua.VariantType.Int32))
        reset = simulator.step(now=2.02)
        assert [(event.phase, event.detail["process"]) for event in reset] == [
            ("reset", 5)
        ]
        assert nodes["S09工艺完成"].get_value() == 0
        assert nodes["S09允许加工"].get_value() is True
    finally:
        adapter.disconnect()
        server.stop()


def test_attachment_flow_skips_scan_on_a_real_opcua_s07_cycle() -> None:
    """验证附件流程经真实 OPC UA adapter 直接执行 S07 工艺 2、3。

    参数：无。
    返回：无；断言不请求工艺 1，也不会产生粉桶扫码动作事件。
    """

    seed = {
        ROBOT_TASK_NUMBER: 0,
        S04_ROBOT_POSITION: 0,
        s04_process(1): 0,
        s04_params_written(1): False,
        S06_PROCESS: 0,
        S06_PARAMS_WRITTEN: False,
        S07_PROCESS: 0,
        S07_PARAMS_WRITTEN: False,
        S08_PROCESS: 0,
        S08_PARAMS_WRITTEN: False,
        S08_CAP_STORAGE_SLOT: 0,
        S09_PROCESS: 0,
        S09_PARAMS_WRITTEN: False,
    }
    blueprint = WorkflowHandshakeSimulator(
        _MemoryAdapter(seed),
        workflow=ATTACHMENT_SINGLE_SAMPLE_WORKFLOW,
        process_delay=0.0,
    )
    values = {**blueprint.initialization_values(), **seed}
    server, nodes, endpoint = _start_server(values)
    adapter = OpcUaVariableAdapter(endpoint, "ns=4;s=上位机通讯|")
    simulator = WorkflowHandshakeSimulator(
        adapter,
        workflow=ATTACHMENT_SINGLE_SAMPLE_WORKFLOW,
        process_delay=0.0,
    )
    try:
        adapter.connect()
        simulator.initialize()
        observed_actions: list[str] = []
        clock = 3.0
        for process in (2, 3):
            nodes[S07_PROCESS].set_value(ua.Variant(process, ua.VariantType.Int32))
            nodes[S07_PARAMS_WRITTEN].set_value(
                ua.Variant(True, ua.VariantType.Boolean)
            )
            events = simulator.step(now=clock) + simulator.step(now=clock)
            observed_actions.extend(event.action for event in events)
            assert [event.phase for event in events] == ["accepted", "completed"]
            assert nodes["S07工艺完成"].get_value() == process

            nodes[S07_PROCESS].set_value(ua.Variant(0, ua.VariantType.Int32))
            nodes[S07_PARAMS_WRITTEN].set_value(
                ua.Variant(False, ua.VariantType.Boolean)
            )
            assert [event.phase for event in simulator.step(now=clock + 0.01)] == [
                "reset"
            ]
            clock += 1.0

        assert "szlab_s07_solid_addition.scan_powder_cartridges" not in observed_actions
    finally:
        adapter.disconnect()
        server.stop()
