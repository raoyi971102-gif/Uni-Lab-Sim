from __future__ import annotations

import socket
from typing import Any

from opcua import ua

from common import NodeDef
from server import add_nodes, build_server, register_ns_padding
from szlab_handshake_agent import (
    ATTACHMENT_SINGLE_SAMPLE_WORKFLOW,
    ROBOT_TASK_NUMBER,
    ROBOT_TOOL_PAYLOAD_SENSOR,
    ROBOT_WRITE_DONE,
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
    s04_done,
    s04_duration,
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


def test_one_package_session_runs_s04_and_s06_without_workflow_restart() -> None:
    """设备包模式经真实 OPC UA 同时响应两个协议族和任意 S04 位置。"""

    seed = {
        ROBOT_TASK_NUMBER: 0,
        S04_ROBOT_POSITION: 0,
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
    for position in range(1, 7):
        seed[s04_process(position)] = 0
        seed[s04_params_written(position)] = False
        seed[s04_duration(position)] = 0
    blueprint = WorkflowHandshakeSimulator(
        _MemoryAdapter(seed),
        workflow="szlab_magnetic_stirring_workflow",
        package_mode=True,
        process_delay=0.0,
    )
    values = {**blueprint.initialization_values(), **seed}
    server, nodes, endpoint = _start_server(values)
    adapter = OpcUaVariableAdapter(endpoint, "ns=4;s=上位机通讯|")
    simulator = WorkflowHandshakeSimulator(
        adapter,
        workflow="szlab_magnetic_stirring_workflow",
        package_mode=True,
        process_delay=0.0,
    )
    try:
        adapter.connect()
        simulator.initialize()
        nodes[s04_process(2)].set_value(ua.Variant(3, ua.VariantType.Int32))
        nodes[s04_params_written(2)].set_value(
            ua.Variant(True, ua.VariantType.Boolean)
        )
        nodes[S06_PROCESS].set_value(ua.Variant(1, ua.VariantType.Int32))
        nodes[S06_PARAMS_WRITTEN].set_value(
            ua.Variant(True, ua.VariantType.Boolean)
        )

        events = simulator.step(now=10.0) + simulator.step(now=10.0)

        assert [(event.phase, event.detail.get("position")) for event in events] == [
            ("accepted", 2),
            ("accepted", None),
            ("completed", 2),
            ("completed", None),
        ]
        assert nodes[s04_done(2)].get_value() is True
        assert nodes["S06加工完成"].get_value() is True
        assert simulator.enabled_components
        assert simulator.protocol_snapshot()["mode"] == "package"
    finally:
        adapter.disconnect()
        server.stop()
