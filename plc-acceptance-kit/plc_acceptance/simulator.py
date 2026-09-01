"""通过 PLC-Sim 正式命令启动跨进程 SZLab L1 验收。"""

from __future__ import annotations

import socket
import subprocess
import sys
import time
from pathlib import Path

from .config import load_bundle
from .models import RunResult
from .reporting import write_reports
from .runner import run_acceptance


def _free_port() -> int:
    """向操作系统申请一个当前空闲的本机 TCP 端口。

    返回：本机空闲端口号。
    """

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as stream:
        stream.bind(("127.0.0.1", 0))
        return int(stream.getsockname()[1])


def _wait_port(port: int, timeout_seconds: float = 10.0) -> None:
    """等待 PLC-Sim OPC UA TCP 端口开始接受连接。

    参数：``port`` 是本机端口，``timeout_seconds`` 是最长等待时间。
    返回：无；超时抛出 ``TimeoutError``。
    """

    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return
        except OSError:
            time.sleep(0.05)
    raise TimeoutError(f"等待 PLC-Sim 端口 {port} 超时")


def _stop_process(process: subprocess.Popen[str]) -> None:
    """优雅停止一个仿真子进程，必要时再终止。

    参数：``process`` 是 Server 或 Handshake Agent 子进程。
    返回：无。
    """

    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def run_simulator_acceptance(
    kit_root: str | Path,
    *,
    output_root: str | Path,
    selected_case_ids: set[str] | None = None,
) -> tuple[RunResult, Path]:
    """启动正式 PLC-Sim 双进程并运行 SZLab L1 门禁。

    参数：``kit_root`` 是验收包目录；``output_root`` 是报告目录；
    ``selected_case_ids`` 可缩小诊断范围。
    返回：运行结果和报告目录。
    """

    root = Path(kit_root).resolve()
    port = _free_port()
    endpoint = f"opc.tcp://127.0.0.1:{port}/xuse_sim/"
    bundle = load_bundle(root, endpoint_override=endpoint)
    simulator_profile = root / "simulator" / "simulation-profile.yaml"
    profile = __import__("yaml").safe_load(
        simulator_profile.read_text(encoding="utf-8")
    )
    handshake_config = (
        simulator_profile.parent / str(profile["handshake_config"])
    ).resolve()
    # 点表由 PLC-Sim 应用持有；从已解析的权威点表反查同一应用的公开脚本入口，
    # 不依赖调用方是否已经把 ``plc_sim`` 分发包安装进当前虚拟环境。
    plc_sim_root = bundle.csv_path.parent.parent
    server_entry = plc_sim_root / "server.py"
    agent_entry = plc_sim_root / "szlab_handshake_agent.py"
    log_root = Path(output_root).resolve()
    log_root.mkdir(parents=True, exist_ok=True)
    server_log_path = log_root / "latest-simulator-server.log"
    agent_log_path = log_root / "latest-simulator-agent.log"

    with (
        server_log_path.open("w", encoding="utf-8") as server_log,
        agent_log_path.open("w", encoding="utf-8") as agent_log,
    ):
        server = subprocess.Popen(
            [
                sys.executable,
                str(server_entry),
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--csv",
                str(bundle.csv_path),
            ],
            stdout=server_log,
            stderr=subprocess.STDOUT,
            text=True,
        )
        agent: subprocess.Popen[str] | None = None
        try:
            _wait_port(port)
            agent = subprocess.Popen(
                [
                    sys.executable,
                    str(agent_entry),
                    "--url",
                    endpoint,
                    "--config",
                    str(handshake_config),
                ],
                stdout=agent_log,
                stderr=subprocess.STDOUT,
                text=True,
            )
            time.sleep(float(profile.get("agent_ready_delay_seconds", 1.0)))
            if agent.poll() is not None:
                raise RuntimeError(
                    f"SZLab Handshake Agent 提前退出: {agent.returncode}"
                )
            result = run_acceptance(bundle, selected_case_ids=selected_case_ids)
            report_dir = write_reports(result, output_root)
            return result, report_dir
        finally:
            if agent is not None:
                _stop_process(agent)
            _stop_process(server)
