"""通过 PLC-Sim 正式命令启动跨进程 SZLab L1 验收。"""

from __future__ import annotations

import socket
import subprocess
import time
from pathlib import Path
from typing import Any

from plc_sim.cli import runtime_command

from .catalog import load_catalog
from .config import load_bundle, resolve_resource_path
from .models import AcceptanceBundle, CatalogNode, RunResult
from .opcua_session import OpcUaSession
from .reporting import write_reports
from .runner import run_acceptance

READY_VALUES: dict[str, Any] = {
    "robot.home": True,
    "robot.write_allowed": True,
    "s041.allow": True,
    "s041.status": 1,
}


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


def _wait_handshake_ready(
    agent: subprocess.Popen[str],
    bundle: AcceptanceBundle,
    catalog: dict[str, CatalogNode],
    *,
    endpoint: str,
    initial_delay_seconds: float,
    timeout_seconds: float = 30.0,
    poll_interval_seconds: float = 0.1,
    log_paths: tuple[Path, Path] | None = None,
) -> None:
    """等待 Handshake Agent 完成初始化并发布可验收的初始值。

    参数：``agent`` 是已启动的 Handshake Agent；``bundle`` 和 ``catalog``
    提供 OPC UA 逻辑变量及点表映射；``endpoint`` 是运行时 OPC UA 地址；
    ``initial_delay_seconds`` 保留配置的启动缓冲；其余参数控制就绪轮询。
    返回：无；所有关键初始值满足契约时返回。
    异常：Agent 提前退出或在超时前未达到就绪值时抛出异常。
    """

    timeout_seconds = max(timeout_seconds, 0.0)
    poll_interval_seconds = max(poll_interval_seconds, 0.01)
    deadline = time.monotonic() + timeout_seconds
    if initial_delay_seconds > 0:
        time.sleep(min(initial_delay_seconds, timeout_seconds))
    connect_timeout_seconds = bundle.environment.connect_timeout_ms / 1000
    last_values: dict[str, Any] = {}
    last_error: Exception | None = None

    while time.monotonic() < deadline:
        return_code = agent.poll()
        if return_code is not None:
            raise RuntimeError(f"SZLab Handshake Agent 提前退出: {return_code}")

        session: OpcUaSession | None = None
        connected = False
        try:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            session = OpcUaSession(
                endpoint,
                bundle.nodes,
                catalog,
                namespace_uri=bundle.namespace_uri,
                timeout_seconds=min(
                    connect_timeout_seconds,
                    max(remaining, 0.2),
                ),
                poll_interval_seconds=poll_interval_seconds,
            )
            session.connect()
            connected = True
            last_values = {}
            for logical_id in READY_VALUES:
                last_values[logical_id] = session.read(logical_id)
            if all(
                last_values.get(logical_id) == expected
                for logical_id, expected in READY_VALUES.items()
            ):
                return
        except Exception as exc:  # noqa: BLE001 - 启动期连接失败需继续重试
            last_error = exc
        finally:
            if connected and session is not None:
                try:
                    session.disconnect()
                except Exception as exc:  # noqa: BLE001 - 保留主轮询错误
                    last_error = exc
        time.sleep(poll_interval_seconds)

    return_code = agent.poll()
    if return_code is not None:
        raise RuntimeError(f"SZLab Handshake Agent 提前退出: {return_code}")
    details = ", ".join(
        f"{logical_id}={last_values.get(logical_id)!r}"
        for logical_id in READY_VALUES
    )
    error_detail = f"；最后连接错误 {last_error}" if last_error else ""
    log_detail = (
        f"；进程日志 server={log_paths[0]} agent={log_paths[1]}"
        if log_paths
        else ""
    )
    raise TimeoutError(
        "等待 SZLab Handshake Agent 就绪超时（"
        f"{timeout_seconds:.1f}s）：{details}{error_detail}{log_detail}"
    )


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
    handshake_config = resolve_resource_path(
        simulator_profile,
        str(profile["handshake_config"]),
    )
    catalog = load_catalog(bundle.csv_path, node_id_prefix=bundle.node_id_prefix)
    # 点表由已安装的 PLC-Sim 分发包持有。源码运行使用脚本入口；冻结运行通过
    # PLC-Sim 的公开命令调度重新进入当前可执行文件，不依赖本机 Python 环境。
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
            runtime_command(
                "server",
                server_entry,
                (
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(port),
                    "--csv",
                    str(bundle.csv_path),
                ),
            ),
            stdout=server_log,
            stderr=subprocess.STDOUT,
            text=True,
        )
        agent: subprocess.Popen[str] | None = None
        try:
            _wait_port(port)
            agent = subprocess.Popen(
                runtime_command(
                    "szlab-handshake",
                    agent_entry,
                    (
                        "--url",
                        endpoint,
                        "--config",
                        str(handshake_config),
                    ),
                ),
                stdout=agent_log,
                stderr=subprocess.STDOUT,
                text=True,
            )
            _wait_handshake_ready(
                agent,
                bundle,
                catalog,
                endpoint=endpoint,
                initial_delay_seconds=float(
                    profile.get("agent_ready_delay_seconds", 1.0)
                ),
                timeout_seconds=30.0,
                poll_interval_seconds=max(
                    bundle.environment.poll_interval_ms / 1000,
                    0.05,
                ),
                log_paths=(server_log_path, agent_log_path),
            )
            result = run_acceptance(bundle, selected_case_ids=selected_case_ids)
            report_dir = write_reports(result, output_root)
            return result, report_dir
        finally:
            if agent is not None:
                _stop_process(agent)
            _stop_process(server)
