"""PyModbus server construction for every supported transport."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from pymodbus import FramerType
from pymodbus.pdu.device import ModbusDeviceIdentification
from pymodbus.server import ModbusSerialServer, ModbusTcpServer

from .config import (
    AppConfig,
    SerialTransportSpec,
    TcpTransportSpec,
    TransportMode,
)
from .model import build_sim_devices


PacketTracer = Callable[[bool, bytes], bytes]
ConnectionTracer = Callable[[bool], None]


@dataclass(frozen=True)
class ServerPlan:
    """Resolved server class and arguments, suitable for validation and display."""

    mode: TransportMode
    server_kind: Literal["tcp", "serial"]
    framer: FramerType
    endpoint: str
    kwargs: dict[str, Any]


def build_server_plan(config: AppConfig, mode: TransportMode | str | None = None) -> ServerPlan:
    selected = config.active_transport if mode is None else TransportMode.parse(mode)
    transport = config.transport(selected)
    if isinstance(transport, TcpTransportSpec):
        return ServerPlan(
            mode=selected,
            server_kind="tcp",
            framer=FramerType.SOCKET,
            endpoint=f"tcp://{transport.host}:{transport.port}",
            kwargs={"address": (transport.host, transport.port)},
        )
    if not isinstance(transport, SerialTransportSpec):  # pragma: no cover - closed union.
        raise TypeError(f"不支持的传输配置: {type(transport)!r}")
    framer = FramerType.ASCII if selected is TransportMode.ASCII else FramerType.RTU
    framing = "ascii" if framer is FramerType.ASCII else "rtu"
    endpoint = f"serial://{transport.device}?mode={selected.value}&framer={framing}&baud={transport.baudrate}"
    kwargs = {
        "port": transport.device,
        "baudrate": transport.baudrate,
        "bytesize": transport.bytesize,
        "parity": transport.parity,
        "stopbits": transport.stopbits,
        "timeout": transport.timeout,
        "handle_local_echo": transport.handle_local_echo,
        "reconnect_delay": transport.reconnect_delay,
    }
    if selected is TransportMode.RTU_RS485:
        kwargs["allow_multiple_devices"] = True
    return ServerPlan(
        mode=selected,
        server_kind="serial",
        framer=framer,
        endpoint=endpoint,
        kwargs=kwargs,
    )


def create_server(
    config: AppConfig,
    mode: TransportMode | str | None = None,
    *,
    trace_packet: PacketTracer | None = None,
    trace_connect: ConnectionTracer | None = None,
) -> ModbusTcpServer | ModbusSerialServer:
    """Create, but do not start, a server for the selected transport."""
    plan = build_server_plan(config, mode)
    identity = ModbusDeviceIdentification(info_name={
        "VendorName": "Uni-Lab",
        "ProductCode": "MODBUS-SIM",
        "MajorMinorRevision": "0.1.0",
        "ProductName": "Modbus-Sim",
        "ModelName": "Uni-Lab Modbus simulator",
        "UserApplicationName": "Modbus-Sim",
    })
    common = {
        "identity": identity,
        "framer": plan.framer,
        "broadcast_enable": True,
        "ignore_missing_devices": False,
        "trace_packet": trace_packet,
        "trace_connect": trace_connect,
    }
    devices = build_sim_devices(config)
    if plan.server_kind == "tcp":
        return ModbusTcpServer(devices, **common, **plan.kwargs)
    return ModbusSerialServer(devices, **common, **plan.kwargs)


async def run_server(config: AppConfig, mode: TransportMode | str | None = None) -> None:
    """Run one selected transport until the process is interrupted."""
    server = create_server(config, mode)
    try:
        await server.serve_forever()
    finally:
        await server.shutdown()
