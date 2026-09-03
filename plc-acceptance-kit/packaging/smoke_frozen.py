"""验证冻结 GUI 能完成正式跨进程 SZLab L1 验收。"""

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
    """向操作系统申请一个本机空闲端口。

    参数：无。
    返回：当前可绑定的 TCP 端口。
    """

    with socket.socket() as stream:
        stream.bind(("127.0.0.1", 0))
        return int(stream.getsockname()[1])


def _json_request(url: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """发送 GUI JSON 请求并解析响应。

    参数：``url`` 是接口地址，``payload`` 是可选 POST 数据。
    返回：接口返回的 JSON 映射。
    """

    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="GET" if data is None else "POST",
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


def _wait_for_health(url: str, process: subprocess.Popen[Any]) -> None:
    """等待冻结 GUI 健康接口可用。

    参数：``url`` 是 GUI 根地址，``process`` 是冻结应用进程。
    返回：无；提前退出或超时抛出 ``RuntimeError``。
    """

    deadline = time.monotonic() + 60
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"GUI 提前退出，代码 {process.returncode}")
        try:
            if _json_request(f"{url}/api/health").get("ok") is True:
                return
        except (OSError, ValueError, urllib.error.URLError) as exc:
            last_error = exc
        time.sleep(0.5)
    raise RuntimeError(f"等待 GUI 健康检查超时: {last_error}")


def _wait_for_acceptance(url: str, process: subprocess.Popen[Any]) -> dict[str, Any]:
    """轮询冻结应用直到完整验收结束。

    参数：``url`` 是 GUI 根地址，``process`` 是冻结应用进程。
    返回：最终验收状态映射。
    """

    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"GUI 在验收期间退出，代码 {process.returncode}")
        snapshot = _json_request(f"{url}/api/run")
        if snapshot.get("state") != "RUNNING":
            return snapshot
        time.sleep(0.75)
    raise RuntimeError("冻结应用完整 L1 验收在 180 秒内未结束")


def _dump_failure_diagnostics(data_root: Path) -> None:
    """把冻结 GUI、Server 和 Agent 的诊断尾部写入 CI 日志。

    参数：``data_root`` 是本次冒烟运行的临时数据目录。
    返回：无；缺失或不可读的日志会被跳过。
    """

    paths = (
        data_root / "frozen.log",
        data_root / "reports" / "latest-simulator-server.log",
        data_root / "reports" / "latest-simulator-agent.log",
        data_root / "reports" / "latest-simulator-server-state.json",
        data_root / "reports" / "latest-simulator-state.json",
    )
    for path in paths:
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        tail = lines[-300:]
        sys.stderr.write(f"\n--- {path.relative_to(data_root)} (last 300 lines) ---\n")
        if tail:
            sys.stderr.write("\n".join(tail))
            sys.stderr.write("\n")


def main() -> int:
    """启动冻结程序并证明 GUI、Server、Agent 和报告链路。

    参数：无；从命令行读取冻结可执行文件。
    返回：全部检查通过返回 ``0``。
    """

    parser = argparse.ArgumentParser()
    parser.add_argument("executable", type=Path)
    arguments = parser.parse_args()
    executable = arguments.executable.resolve()
    if not executable.is_file():
        raise FileNotFoundError(executable)

    gui_port = _free_port()
    base_url = f"http://127.0.0.1:{gui_port}"
    with tempfile.TemporaryDirectory(prefix="szlab-plc-acceptance-smoke-") as data_dir:
        log_path = Path(data_dir) / "frozen.log"
        environment = os.environ.copy()
        environment["PLC_ACCEPTANCE_DATA_DIR"] = data_dir
        with log_path.open("wb") as log_file:
            process = subprocess.Popen(
                [str(executable), "--port", str(gui_port), "--no-open"],
                stdout=log_file,
                stderr=subprocess.STDOUT,
                env=environment,
            )
            try:
                _wait_for_health(base_url, process)
                baseline = _json_request(f"{base_url}/api/bootstrap")
                if baseline.get("l0_status") != "PASSED":
                    raise RuntimeError(f"冻结包 L0 未通过: {baseline}")
                started = _json_request(
                    f"{base_url}/api/run",
                    {"mode": "simulator"},
                )
                if started.get("state") != "RUNNING":
                    raise RuntimeError(f"GUI 未启动验收: {started}")
                result = _wait_for_acceptance(base_url, process)
                if result.get("state") != "PASSED":
                    raise RuntimeError(f"冻结包 L1 未通过: {result}")
                report = result.get("report") or {}
                if report.get("case_summary") != {"PASSED": 105}:
                    raise RuntimeError(f"冻结包用例数量不完整: {report}")
                run_id = report["run_id"]
                with urllib.request.urlopen(
                    f"{base_url}/api/reports/{run_id}/download",
                    timeout=30,
                ) as response:
                    if response.read(2) != b"PK":
                        raise RuntimeError("冻结包没有生成 ZIP 证据")
            except Exception:
                log_file.flush()
                _dump_failure_diagnostics(Path(data_dir))
                raise
            finally:
                process.terminate()
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)

    print("Frozen GUI and complete SZLab L1 acceptance smoke test passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
