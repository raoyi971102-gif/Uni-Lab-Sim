import asyncio
import os
import shutil
import socket
import subprocess
from dataclasses import replace

import pytest
from pymodbus import FramerType
from pymodbus.client import AsyncModbusSerialClient, AsyncModbusTcpClient

from modbus_sim.config import (
    TcpTransportSpec,
    TransportMode,
    load_config,
    replace_transport,
    select_transport,
)
from modbus_sim.server import create_server


def free_tcp_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def test_tcp_read_write_round_trip():
    async def scenario():
        port = free_tcp_port()
        config = load_config()
        config = replace_transport(config, TransportMode.TCP, TcpTransportSpec("127.0.0.1", port))
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
            assert (await client.read_holding_registers(0, count=1, device_id=1)).registers == [777]
        finally:
            client.close()
            await server.shutdown()

    asyncio.run(scenario())


@pytest.mark.skipif(shutil.which("socat") is None or os.name == "nt", reason="需要 Unix socat 伪终端")
@pytest.mark.parametrize("mode", [TransportMode.RTU_RS485, TransportMode.RTU_RS232, TransportMode.ASCII])
def test_serial_protocol_round_trip(mode, tmp_path):
    async def scenario():
        server_port = tmp_path / "server-pty"
        client_port = tmp_path / "client-pty"
        process = subprocess.Popen(
            ["socat", f"pty,raw,echo=0,link={server_port}", f"pty,raw,echo=0,link={client_port}"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        server = None
        client = None
        try:
            for _ in range(50):
                if server_port.exists() and client_port.exists():
                    break
                await asyncio.sleep(0.02)
            else:
                pytest.fail("socat 没有创建伪终端")

            config = load_config()
            spec = config.transport(mode)
            # 部分 Linux PTY 不接受 7E1；ASCII 的帧协议仍由真实 ASCII framer 验证。
            spec = replace(
                spec,
                device=str(server_port),
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
                str(client_port),
                framer=FramerType.ASCII if mode is TransportMode.ASCII else FramerType.RTU,
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
            process.terminate()
            process.wait(timeout=2)

    asyncio.run(scenario())
