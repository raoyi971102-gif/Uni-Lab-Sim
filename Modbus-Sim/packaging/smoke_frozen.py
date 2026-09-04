"""Smoke-test the frozen GUI and a real Modbus TCP request."""

from __future__ import annotations

import argparse
import json
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from pymodbus.client import ModbusTcpClient


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _request(
    url: str, *, method: str = "GET", payload: dict[str, Any] | None = None
) -> dict[str, Any]:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method=method
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def _wait_for_health(url: str, process: subprocess.Popen[Any]) -> None:
    deadline = time.monotonic() + 60
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"GUI exited early with code {process.returncode}")
        try:
            if _request(f"{url}/api/health").get("ok") is True:
                return
        except (OSError, ValueError, urllib.error.URLError) as exc:
            last_error = exc
        time.sleep(0.5)
    raise RuntimeError(f"GUI health check timed out: {last_error}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("executable", type=Path)
    args = parser.parse_args()
    executable = args.executable.resolve()
    gui_port, modbus_port = _free_port(), _free_port()
    base_url = f"http://127.0.0.1:{gui_port}"

    with tempfile.TemporaryDirectory(prefix="modbus-sim-smoke-") as temp_dir:
        log_path = Path(temp_dir) / "frozen.log"
        with log_path.open("wb") as log_file:
            process = subprocess.Popen(
                [
                    str(executable),
                    "gui",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(gui_port),
                    "--no-open",
                ],
                stdout=log_file,
                stderr=subprocess.STDOUT,
            )
            try:
                _wait_for_health(base_url, process)
                config = _request(f"{base_url}/api/config")
                config["active_transport"] = "tcp"
                config["transports"]["tcp"] = {"host": "127.0.0.1", "port": modbus_port}
                _request(f"{base_url}/api/config", method="PUT", payload=config)
                _request(f"{base_url}/api/start", method="POST", payload={})
                client = ModbusTcpClient("127.0.0.1", port=modbus_port, timeout=3)
                try:
                    if not client.connect():
                        raise RuntimeError(
                            "Frozen Modbus TCP server refused connection"
                        )
                    response = client.read_holding_registers(0, count=2, device_id=1)
                    if response.isError() or response.registers != [1200, 850]:
                        raise RuntimeError(f"Unexpected Modbus response: {response}")
                finally:
                    client.close()
                _request(f"{base_url}/api/stop", method="POST", payload={})
            except Exception:
                log_file.flush()
                sys.stderr.write(log_path.read_text(encoding="utf-8", errors="replace"))
                raise
            finally:
                process.terminate()
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)

    print("Frozen GUI and Modbus TCP round trip passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
