from __future__ import annotations

import socket
from pathlib import Path

from opcua import ua

from common import load_ptlc_nodes, load_yaml
from ptlc_handshake_agent import OpcUaVariableAdapter, PtlcHandshakeSimulator
from server import add_nodes, build_server, register_ns_padding

ROOT = Path(__file__).parents[1]


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def test_ptlc_nested_gvl_array_and_l2_cycle_through_real_opcua() -> None:
    """验证真实 OPC UA 连接下的嵌套 GVL、数组和连续轴运动握手。"""

    port = _free_port()
    endpoint = f"opc.tcp://127.0.0.1:{port}/xuse_sim/"
    defs = load_ptlc_nodes(ROOT / "config" / "ptlc_nodes.yaml")
    server = build_server(endpoint)
    ns_index = register_ns_padding(server, 4, "urn:ptlc:test")
    nodes = add_nodes(server, ns_index, defs)
    server.start()

    config = load_yaml(str(ROOT / "config" / "ptlc_handshake.yaml"))
    adapter = OpcUaVariableAdapter(endpoint, tuple(config["gvl_path"]))
    sim = PtlcHandshakeSimulator(adapter, config=config, delay_s=0)
    try:
        adapter.connect()
        sim.initialize()
        assert adapter.read("PLC_Axis_CommOperational") == [True] * 11
        adapter.write("Rail_Pos_Target", [1.0, 12.5, 20.0, 30.0, 40.0, 50.0])
        adapter.write("Rail_Target_Position", 2)
        adapter.write("Rail_L2_ActionCode", 10)
        adapter.write("Rail_L2_RequestSeq", 99)
        adapter.write("Rail_L2_Start", True)

        assert [event.phase for event in sim.step(now=1.0)] == ["accepted"]
        assert [event.phase for event in sim.step(now=1.2)] == ["completed"]
        assert adapter.read("Rail_L2_State") == 20
        assert adapter.read("Rail_L2_CompletedSeq") == 99
        assert adapter.read("Rail_ActPos") == 12.5
        assert nodes["Tank_Drain_S"].get_data_type_as_variant_type() == ua.VariantType.Double
        assert nodes["PLC_Axis_CommOperational"].get_value_rank() == 1
    finally:
        adapter.disconnect()
        server.stop()
