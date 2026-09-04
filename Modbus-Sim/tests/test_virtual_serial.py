import hashlib
import os
import subprocess
from pathlib import Path

import pytest
import serial
from modbus_sim.config import ConfigError
from modbus_sim.virtual_serial import VirtualSerialManager


@pytest.mark.skipif(os.name != "posix", reason="PTY virtual ports require POSIX")
def test_pty_pair_moves_bytes_in_both_directions():
    manager = VirtualSerialManager()
    pair = manager.create()
    try:
        assert pair.backend == "pty"
        assert pair.simulator_port != pair.client_port
        with (
            serial.Serial(pair.simulator_port, 9600, timeout=1) as simulator,
            serial.Serial(pair.client_port, 9600, timeout=1) as client,
        ):
            simulator.write(b"request")
            assert client.read(7) == b"request"
            client.write(b"response")
            assert simulator.read(8) == b"response"
    finally:
        manager.close()

    assert manager.active_pair is None


def test_windows_com0com_pair_uses_argument_list_and_can_be_removed():
    calls = []
    list_count = 0

    def runner(command, **kwargs):
        nonlocal list_count
        calls.append((command, kwargs))
        if command[1] == "list":
            list_count += 1
            output = (
                "" if list_count == 1 else "CNCA7 PortName=COM10\nCNCB7 PortName=COM11"
            )
        else:
            output = "ok"
        return subprocess.CompletedProcess(command, 0, stdout=output, stderr="")

    manager = VirtualSerialManager(
        platform_name="win32",
        setupc_path="C:/Program Files/com0com/setupc.exe",
        runner=runner,
        admin_check=lambda: True,
    )
    pair = manager.create("com10", "COM11")

    assert pair.simulator_port == "COM10"
    assert pair.client_port == "COM11"
    assert pair.pair_id == "7"
    assert calls[1][0] == [
        "C:/Program Files/com0com/setupc.exe",
        "install",
        "PortName=COM10",
        "PortName=COM11",
    ]
    assert calls[1][1]["check"] is False

    manager.remove()
    assert calls[-1][0][-2:] == ["remove", "7"]
    assert manager.active_pair is None


def test_windows_non_admin_uses_uac_only_for_mutating_commands():
    normal_calls = []
    elevated_calls = []
    installed = False

    def runner(command, **kwargs):
        normal_calls.append(command)
        output = "CNCA9 PortName=COM20\nCNCB9 PortName=COM21" if installed else ""
        return subprocess.CompletedProcess(command, 0, stdout=output, stderr="")

    def elevated(executable: Path, arguments):
        nonlocal installed
        elevated_calls.append((executable, tuple(arguments)))
        installed = arguments[0] == "install"
        return 0

    manager = VirtualSerialManager(
        platform_name="win32",
        setupc_path="C:/com0com/setupc.exe",
        runner=runner,
        elevated_runner=elevated,
        admin_check=lambda: False,
    )
    assert manager.status()["can_create"] is True
    pair = manager.create("COM20", "COM21")

    assert pair.pair_id == "9"
    assert [call[1] for call in elevated_calls] == [
        ("install", "PortName=COM20", "PortName=COM21")
    ]
    assert all(call[1] == "list" for call in normal_calls)

    manager.remove()
    assert elevated_calls[-1][1] == ("remove", "9")


@pytest.mark.parametrize(
    ("port_a", "port_b", "occupied", "message"),
    [
        ("tty1", "COM11", (), "COM1..COM256"),
        ("COM10", "COM10", (), "不能相同"),
        ("COM10", "COM11", ("com11",), "已被占用"),
    ],
)
def test_windows_virtual_port_names_are_validated(port_a, port_b, occupied, message):
    manager = VirtualSerialManager(
        platform_name="win32",
        setupc_path="C:/com0com/setupc.exe",
        admin_check=lambda: True,
    )
    with pytest.raises(ConfigError, match=message):
        manager.create(port_a, port_b, occupied_ports=occupied)


def test_windows_missing_driver_failure_is_explicit():
    missing = VirtualSerialManager(platform_name="win32", admin_check=lambda: False)
    assert missing.status()["driver_installed"] is False
    with pytest.raises(ConfigError, match="未检测到 com0com"):
        missing.create()


def test_bundled_driver_is_hash_checked_and_installed_with_uac(tmp_path):
    installer = tmp_path / "com0com.exe"
    installer.write_bytes(b"signed driver fixture")
    digest = hashlib.sha256(installer.read_bytes()).hexdigest()
    setupc = tmp_path / "setupc.exe"
    elevated_calls = []
    driver_installed = False

    def elevated(executable: Path, arguments):
        nonlocal driver_installed
        elevated_calls.append((executable, tuple(arguments)))
        driver_installed = True
        return 0

    manager = VirtualSerialManager(
        platform_name="win32",
        installer_path=installer,
        installer_sha256=digest,
        setupc_finder=lambda _platform: setupc if driver_installed else None,
        elevated_runner=elevated,
        admin_check=lambda: False,
        driver_wait_seconds=0,
    )
    assert manager.status()["can_install_driver"] is True
    manager.install_driver()

    assert elevated_calls == [(installer, ("/S",))]
    assert manager.status()["driver_installed"] is True


def test_bundled_driver_rejects_tampering_and_uac_cancellation(tmp_path):
    installer = tmp_path / "com0com.exe"
    installer.write_bytes(b"tampered")
    invalid = VirtualSerialManager(
        platform_name="win32",
        installer_path=installer,
        installer_sha256="0" * 64,
        admin_check=lambda: False,
    )
    with pytest.raises(ConfigError, match="校验失败"):
        invalid.install_driver()

    digest = hashlib.sha256(installer.read_bytes()).hexdigest()

    def cancelled(_executable, _arguments):
        raise ConfigError("用户取消了 Windows UAC 授权")

    cancelled_manager = VirtualSerialManager(
        platform_name="win32",
        installer_path=installer,
        installer_sha256=digest,
        elevated_runner=cancelled,
        admin_check=lambda: False,
    )
    with pytest.raises(ConfigError, match="取消了 Windows UAC"):
        cancelled_manager.install_driver()
