from pymodbus import FramerType

from modbus_sim.config import TransportMode, load_config
from modbus_sim.model import build_sim_devices
from modbus_sim.server import build_server_plan
from modbus_sim.runtime import _packet_metadata


def test_server_plans_resolve_all_four_transports():
    config = load_config()

    tcp = build_server_plan(config, TransportMode.TCP)
    rs485 = build_server_plan(config, TransportMode.RTU_RS485)
    rs232 = build_server_plan(config, TransportMode.RTU_RS232)
    ascii_plan = build_server_plan(config, TransportMode.ASCII)

    assert (tcp.server_kind, tcp.framer, tcp.endpoint) == ("tcp", FramerType.SOCKET, "tcp://0.0.0.0:5020")
    assert rs485.framer is FramerType.RTU and rs485.kwargs["allow_multiple_devices"] is True
    assert rs232.framer is FramerType.RTU and "allow_multiple_devices" not in rs232.kwargs
    assert ascii_plan.framer is FramerType.ASCII and "allow_multiple_devices" not in ascii_plan.kwargs


def test_simulator_device_model_keeps_unit_ids_and_permissions():
    devices = build_sim_devices(load_config())

    assert [device.id for device in devices] == [1, 2]
    assert devices[0].simdata[0][0].readonly is False
    assert devices[0].simdata[1][0].readonly is True
    assert devices[0].simdata[2][0].readonly is False
    assert devices[0].simdata[3][0].readonly is True


def test_tcp_packet_metadata_distinguishes_request_and_response_fields():
    request = bytes.fromhex("00 01 00 00 00 06 01 03 00 04 00 02")
    response = bytes.fromhex("00 01 00 00 00 07 01 03 04 00 0A 00 0B")

    assert _packet_metadata(TransportMode.TCP, request, sending=False) == {
        "unit_id": 1, "function_code": 3, "address": 4, "count": 2,
    }
    assert _packet_metadata(TransportMode.TCP, response, sending=True) == {
        "unit_id": 1, "function_code": 3, "address": None, "count": 2,
    }
