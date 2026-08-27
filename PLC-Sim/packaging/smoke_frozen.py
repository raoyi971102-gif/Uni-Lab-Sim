"""Smoke-test a frozen application, including its managed OPC UA child."""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _request(url: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="GET" if payload is None else "POST",
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
    if not executable.is_file():
        raise FileNotFoundError(executable)

    gui_port = _free_port()
    opc_port = _free_port()
    base_url = f"http://127.0.0.1:{gui_port}"

    with tempfile.TemporaryDirectory(prefix="plc-sim-smoke-") as data_dir:
        log_path = Path(data_dir) / "frozen.log"
        env = os.environ.copy()
        env["PLCSIM_DATA_DIR"] = data_dir
        with log_path.open("wb") as log_file:
            process = subprocess.Popen(
                [
                    str(executable),
                    "gui",
                    "--host", "127.0.0.1",
                    "--port", str(gui_port),
                    "--no-open",
                ],
                stdout=log_file,
                stderr=subprocess.STDOUT,
                env=env,
            )
            try:
                _wait_for_health(base_url, process)
                started = _request(
                    f"{base_url}/api/server/start",
                    {"host": "127.0.0.1", "port": opc_port},
                )
                if started.get("ok") is not True:
                    raise RuntimeError(f"OPC UA child failed to start: {started}")

                state = _request(f"{base_url}/api/state")
                if state.get("server", {}).get("running") is not True:
                    raise RuntimeError(f"OPC UA child is not running: {state}")

                agent = _request(
                    f"{base_url}/api/agent/start",
                    {"host": "127.0.0.1", "port": opc_port, "profile": "szlab"},
                )
                if agent.get("ok") is not True:
                    raise RuntimeError(f"Handshake child failed to start: {agent}")

                state = _request(f"{base_url}/api/state")
                if state.get("agent", {}).get("running") is not True:
                    raise RuntimeError(f"Handshake child is not running: {state}")

                agent_stopped = _request(f"{base_url}/api/agent/stop", {})
                if agent_stopped.get("ok") is not True:
                    raise RuntimeError(
                        f"Handshake child failed to stop: {agent_stopped}"
                    )

                stopped = _request(f"{base_url}/api/server/stop", {})
                if stopped.get("ok") is not True:
                    raise RuntimeError(f"OPC UA child failed to stop: {stopped}")
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

        if process.returncode not in {0, 1, -15, 15, 143}:
            sys.stderr.write(log_path.read_text(encoding="utf-8", errors="replace"))
            raise RuntimeError(f"GUI exited with unexpected code {process.returncode}")

    print("Frozen GUI, OPC UA server, and handshake agent smoke test passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
