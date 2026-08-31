import os
import socket

import pytest
from fastapi.testclient import TestClient
from modbus_sim.gui.backend import create_app
from pymodbus.client import ModbusTcpClient


def free_tcp_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def test_gui_assets_and_configuration_api():
    with TestClient(create_app()) as client:
        assert client.get("/").status_code == 200
        assert "以方案 B 为主、融合方案 C 设备树" in client.get("/").text
        assert "导入寄存器 CSV" in client.get("/").text
        assert "虚拟串口" in client.get("/").text
        assert client.get("/static/app.js").status_code == 200

        config = client.get("/api/config").json()
        assert config["active_transport"] == "tcp"
        changed = client.put(
            "/api/devices/1/areas/holding_registers/points/0",
            json={
                "value": 4321,
                "alias": "Setpoint",
                "description": "测试",
                "format": "uint16",
            },
        )
        assert changed.status_code == 200
        assert changed.json()["rows"][0]["initial_value"] == 4321

        edited = client.put(
            "/api/devices/1",
            json={
                "unit_id": 11,
                "name": "Renamed PLC",
                "sizes": {
                    "coils": 24,
                    "discrete_inputs": 20,
                    "holding_registers": 64,
                    "input_registers": 48,
                },
            },
        )
        assert edited.status_code == 200
        assert edited.json()["config"]["devices"][0]["unit_id"] == 2
        assert (
            edited.json()["config"]["devices"][1]["areas"]["holding_registers"]["size"]
            == 64
        )

        yaml_response = client.get("/api/config/yaml")
        assert yaml_response.headers["content-type"].startswith("application/yaml")
        imported = client.put(
            "/api/config/yaml",
            content=yaml_response.text,
            headers={"content-type": "application/yaml"},
        )
        assert imported.status_code == 200

        csv_response = client.get("/api/registers/csv")
        assert csv_response.headers["content-type"].startswith("text/csv")
        assert "modbus-registers.csv" in csv_response.headers["content-disposition"]
        imported_csv = client.put(
            "/api/registers/csv",
            content=csv_response.content,
            headers={"content-type": "text/csv"},
        )
        assert imported_csv.status_code == 200
        assert imported_csv.json()["config"]["transports"] == config["transports"]


def test_gui_installs_bundled_virtual_serial_driver_while_stopped():
    class FakeVirtualSerialManager:
        def __init__(self):
            self.installed = False
            self.active_pair = None

        def status(self):
            return {
                "platform": "win32",
                "backend": "com0com",
                "supported": self.installed,
                "driver_installed": self.installed,
                "installer_available": True,
                "administrator": False,
                "can_create": self.installed,
                "can_install_driver": not self.installed,
                "active_pair": None,
                "driver_url": "https://sourceforge.net/projects/com0com/",
                "message": "ready" if self.installed else "missing",
                "electrical_layer_emulated": False,
            }

        def install_driver(self):
            self.installed = True

        def close(self):
            pass

    manager = FakeVirtualSerialManager()
    with TestClient(create_app(virtual_serial_manager=manager)) as client:
        response = client.post("/api/virtual-serial/driver")
        assert response.status_code == 200
        assert response.json()["driver_installed"] is True
        assert response.json()["can_create"] is True


def test_gui_starts_real_tcp_server_tracks_traffic_and_enforces_runtime_locks():
    port = free_tcp_port()
    with TestClient(create_app()) as api_client:
        config = api_client.get("/api/config").json()
        config["transports"]["tcp"] = {"host": "127.0.0.1", "port": port}
        assert api_client.put("/api/config", json=config).status_code == 200
        assert api_client.post("/api/start", json={}).status_code == 200

        protocol_client = ModbusTcpClient("127.0.0.1", port=port, timeout=1)
        try:
            assert protocol_client.connect()
            response = protocol_client.read_holding_registers(0, count=2, device_id=1)
            assert response.registers == [1200, 850]
            assert not protocol_client.write_register(0, 999, device_id=1).isError()
        finally:
            protocol_client.close()

        state = api_client.get("/api/state").json()
        assert state["running"] is True
        assert state["rx"] >= 2 and state["tx"] >= 2
        assert api_client.get("/api/traffic").json()["entries"]
        assert (
            api_client.get(
                "/api/registers", params={"unit_id": 1, "area": "holding_registers"}
            ).json()["rows"][0]["live_value"]
            == 999
        )
        locked = api_client.post(
            "/api/devices", json={"unit_id": 10, "name": "Blocked"}
        )
        assert locked.status_code == 400
        assert "请先停止服务" in locked.json()["detail"]
        virtual_locked = api_client.post("/api/virtual-serial", json={})
        assert virtual_locked.status_code == 400
        assert "请先停止服务" in virtual_locked.json()["detail"]
        driver_locked = api_client.post("/api/virtual-serial/driver")
        assert driver_locked.status_code == 400
        assert "请先停止服务" in driver_locked.json()["detail"]
        csv_locked = api_client.put(
            "/api/registers/csv",
            content=api_client.get("/api/registers/csv").content,
            headers={"content-type": "text/csv"},
        )
        assert csv_locked.status_code == 400
        assert "请先停止服务" in csv_locked.json()["detail"]
        assert api_client.post("/api/stop").json()["running"] is False


@pytest.mark.skipif(os.name != "posix", reason="PTY virtual ports require POSIX")
def test_gui_manages_virtual_serial_pair_and_lists_both_endpoints():
    with TestClient(create_app()) as client:
        initial = client.get("/api/virtual-serial").json()
        assert initial["backend"] == "pty"
        assert initial["active_pair"] is None
        assert initial["electrical_layer_emulated"] is False

        created = client.post("/api/virtual-serial", json={}).json()
        pair = created["active_pair"]
        assert pair["simulator_port"].startswith("/dev/")
        assert pair["client_port"].startswith("/dev/")
        listed = client.get("/api/serial-ports").json()["ports"]
        assert {item["device"] for item in listed if item["virtual"]} == {
            pair["simulator_port"],
            pair["client_port"],
        }

        removed = client.delete("/api/virtual-serial").json()
        assert removed["active_pair"] is None
