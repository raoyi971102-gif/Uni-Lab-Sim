import asyncio
import os
import socket
from dataclasses import replace

import pytest
from modbus_sim.config import (
    TcpTransportSpec,
    TransportMode,
    load_config,
    replace_transport,
    select_transport,
)
from modbus_sim.server import create_server
from modbus_sim.virtual_serial import VirtualSerialManager
from pymodbus import FramerType
from pymodbus.client import AsyncModbusSerialClient, AsyncModbusTcpClient


def free_tcp_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def test_tcp_read_write_round_trip():
    async def scenario():
        port = free_tcp_port()
        config = load_config()
        config = replace_transport(
            config, TransportMode.TCP, TcpTransportSpec("127.0.0.1", port)
        )
        config = select_transport(config, TransportMode.TCP)
        server = create_server(config)
        await server.serve_forever(background=True)
        client = AsyncModbusTcpClient("127.0.0.1", port=port, timeout=1)
        try:
            assert await client.connect()
            response = await client.read_holding_registers(0, count=4, device_id=1)
            assert not response.isError()
            assert response.registers == [1200, 850, 12, 0]
            assert not (await client.write_register(0, 777, device_id=1)).isError()
            assert (
                await client.read_holding_registers(0, count=1, device_id=1)
            ).registers == [777]
        finally:
            client.close()
            await server.shutdown()

    asyncio.run(scenario())


@pytest.mark.skipif(os.name == "nt", reason="内置 PTY 串口对需要 POSIX")
@pytest.mark.parametrize(
    "mode", [TransportMode.RTU_RS485, TransportMode.RTU_RS232, TransportMode.ASCII]
)
def test_serial_protocol_round_trip_over_managed_virtual_pair(mode):
    async def scenario():
        virtual_serial = VirtualSerialManager()
        pair = virtual_serial.create()
        server = None
        client = None
        try:
            config = load_config()
            spec = config.transport(mode)
            # 部分 Linux PTY 不接受 7E1；ASCII 的帧协议仍由真实 ASCII framer 验证。
            spec = replace(
                spec,
                device=pair.simulator_port,
                bytesize=8 if mode is TransportMode.ASCII else spec.bytesize,
                parity="N" if mode is TransportMode.ASCII else spec.parity,
                timeout=0.3,
                reconnect_delay=0.1,
            )
            config = replace_transport(config, mode, spec)
            config = select_transport(config, mode)
            server = create_server(config)
            await server.serve_forever(background=True)
            client = AsyncModbusSerialClient(
                pair.client_port,
                framer=FramerType.ASCII
                if mode is TransportMode.ASCII
                else FramerType.RTU,
                baudrate=spec.baudrate,
                bytesize=spec.bytesize,
                parity=spec.parity,
                stopbits=spec.stopbits,
                timeout=1,
                retries=1,
            )
            assert await client.connect()
            response = await client.read_holding_registers(0, count=2, device_id=1)
            assert not response.isError()
            assert response.registers == [1200, 850]
        finally:
            if client is not None:
                client.close()
            if server is not None:
                await server.shutdown()
            virtual_serial.close()

    asyncio.run(scenario())
