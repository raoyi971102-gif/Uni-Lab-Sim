"""通过 PLC-Sim 正式命令启动跨进程 SZLab L1 验收。"""

from __future__ import annotations

import json
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

SERVER_READY_TIMEOUT_SECONDS = 30.0
AGENT_READY_TIMEOUT_SECONDS = 60.0


def _free_port(exclude: set[int] | None = None) -> int:
    """向操作系统申请一个当前空闲的本机 TCP 端口。

    参数：``exclude`` 是本次运行中已经占用或预留、不可重复选择的端口。
    返回：本机空闲端口号。
    """

    excluded = exclude or set()
    for _ in range(20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as stream:
            stream.bind(("127.0.0.1", 0))
            port = int(stream.getsockname()[1])
        if port not in excluded:
            return port
    raise RuntimeError("无法分配不冲突的本机临时端口")


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


def _disconnect_session(session: OpcUaSession | None) -> Exception | None:
    """关闭就绪检查会话并返回关闭异常，不掩盖主错误。"""

    if session is None:
        return None
    try:
        session.disconnect()
    except Exception as exc:  # noqa: BLE001 - 诊断阶段不能掩盖主错误
        return exc
    return None


def _wait_server_ready(
    server: subprocess.Popen[str],
    state_path: Path,
    endpoint: str,
    *,
    timeout_seconds: float = SERVER_READY_TIMEOUT_SECONDS,
    poll_interval_seconds: float = 0.1,
) -> None:
    """等待 Server 完成节点创建并写出与当前进程匹配的状态快照。"""

    deadline = time.monotonic() + max(timeout_seconds, 0.0)
    last_state: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        return_code = server.poll()
        if return_code is not None:
            raise RuntimeError(f"PLC-Sim Server 提前退出: {return_code}")
        try:
            payload = json.loads(state_path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                last_state = payload
                if (
                    payload.get("server_pid") == server.pid
                    and payload.get("endpoint") == endpoint
                ):
                    return
        except (OSError, ValueError, TypeError):
            pass
        time.sleep(poll_interval_seconds)
    raise TimeoutError(
        "等待 PLC-Sim Server 完成节点初始化超时："
        f"state={last_state!r}，日志={state_path}"
    )


def _read_agent_state(path: Path) -> tuple[bool, dict[str, Any] | None]:
    """读取 Agent 原子状态文件，判断初始化写入是否已全部结束。"""

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return False, None
    if not isinstance(payload, dict):
        return False, None
    world = payload.get("world")
    devices = world.get("devices") if isinstance(world, dict) else None
    plc_state = devices.get("szlab_poly_plc") if isinstance(devices, dict) else None
    initialized_nodes = (
        plc_state.get("initialized_nodes") if isinstance(plc_state, dict) else None
    )
    initialized = (
        payload.get("state") == "running"
        and isinstance(plc_state, dict)
        and plc_state.get("state") == "ready"
        and type(initialized_nodes) is int
        and initialized_nodes > 0
    )
    return initialized, payload


def _agent_initialized_nodes(state: dict[str, Any] | None) -> int | None:
    """从 Agent 状态快照中提取已完成的 OPC UA 初始化数量。"""

    if not isinstance(state, dict):
        return None
    world = state.get("world")
    devices = world.get("devices") if isinstance(world, dict) else None
    plc_state = devices.get("szlab_poly_plc") if isinstance(devices, dict) else None
    value = plc_state.get("initialized_nodes") if isinstance(plc_state, dict) else None
    return value if type(value) is int else None


def _wait_handshake_ready(
    agent: subprocess.Popen[str],
    bundle: AcceptanceBundle,
    catalog: dict[str, CatalogNode],
    *,
    endpoint: str,
    initial_delay_seconds: float,
    timeout_seconds: float = AGENT_READY_TIMEOUT_SECONDS,
    poll_interval_seconds: float = 0.1,
    log_paths: tuple[Path, Path] | None = None,
    state_path: Path | None = None,
) -> None:
    """等待 Handshake Agent 完成初始化并发布可验收的初始值。

    参数：``agent`` 是已启动的 Handshake Agent；``bundle`` 和 ``catalog``
    提供 OPC UA 逻辑变量及点表映射；``endpoint`` 是运行时 OPC UA 地址；
    ``initial_delay_seconds`` 保留配置的启动缓冲；``log_paths`` 和 ``state_path``
    用于失败诊断；其余参数控制就绪轮询。
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
    last_state: dict[str, Any] | None = None
    session: OpcUaSession | None = None
    reconnect_delay_seconds = max(poll_interval_seconds, 0.1)
    next_connect_at = time.monotonic()

    try:
        while time.monotonic() < deadline:
            return_code = agent.poll()
            if return_code is not None:
                raise RuntimeError(f"SZLab Handshake Agent 提前退出: {return_code}")

            if state_path is not None:
                initialized, last_state = _read_agent_state(state_path)
                if not initialized:
                    time.sleep(
                        min(poll_interval_seconds, max(deadline - time.monotonic(), 0))
                    )
                    continue

            now = time.monotonic()
            if session is None:
                if now < next_connect_at:
                    time.sleep(
                        min(poll_interval_seconds, next_connect_at - now)
                    )
                    continue
                remaining = deadline - now
                if remaining <= 0:
                    break
                candidate = OpcUaSession(
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
                try:
                    candidate.connect()
                except Exception as exc:  # noqa: BLE001 - 启动期连接失败需重试
                    last_error = exc
                    _disconnect_session(candidate)
                    next_connect_at = time.monotonic() + reconnect_delay_seconds
                    reconnect_delay_seconds = min(
                        reconnect_delay_seconds * 2,
                        2.0,
                    )
                    continue
                session = candidate
                last_error = None
                reconnect_delay_seconds = max(poll_interval_seconds, 0.1)

            ready = False
            try:
                last_values = {}
                for logical_id in READY_VALUES:
                    last_values[logical_id] = session.read(logical_id)
                ready = all(
                    last_values.get(logical_id) == expected
                    for logical_id, expected in READY_VALUES.items()
                )
            except Exception as exc:  # noqa: BLE001 - 通信异常触发有限重连
                last_error = exc
                _disconnect_session(session)
                session = None
                next_connect_at = time.monotonic() + reconnect_delay_seconds
                reconnect_delay_seconds = min(
                    reconnect_delay_seconds * 2,
                    2.0,
                )
                continue

            if ready:
                if state_path is not None:
                    # 在读取关键值后再次读取快照，排除 Agent 刚完成初始化检查
                    # 就进入停止/失败分支的窗口。
                    initialized, last_state = _read_agent_state(state_path)
                    if not initialized:
                        time.sleep(
                            min(
                                poll_interval_seconds,
                                max(deadline - time.monotonic(), 0),
                            )
                        )
                        continue
                # Agent 在写入状态快照后还会执行一次完整的先决条件回读；
                # 再次检查进程，避免它刚报告失败就被父进程误判为就绪。
                return_code = agent.poll()
                if return_code is not None:
                    raise RuntimeError(
                        f"SZLab Handshake Agent 在就绪确认后退出: {return_code}"
                    )
                return

            time.sleep(
                min(poll_interval_seconds, max(deadline - time.monotonic(), 0))
            )
    finally:
        disconnect_error = _disconnect_session(session)
        if last_error is None and disconnect_error is not None:
            last_error = disconnect_error

    return_code = agent.poll()
    if return_code is not None:
        raise RuntimeError(f"SZLab Handshake Agent 提前退出: {return_code}")
    details = ", ".join(
        f"{logical_id}={last_values.get(logical_id)!r}"
        for logical_id in READY_VALUES
    )
    error_detail = f"；最后连接错误 {last_error}" if last_error else ""
    state_detail = ""
    if state_path:
        initialized_nodes = _agent_initialized_nodes(last_state)
        state_detail = f"；初始化状态 initialized_nodes={initialized_nodes!r}"
    log_detail = (
        f"；进程日志 server={log_paths[0]} agent={log_paths[1]}"
        if log_paths
        else ""
    )
    raise TimeoutError(
        "等待 SZLab Handshake Agent 就绪超时（"
        f"{timeout_seconds:.1f}s）：{details}{state_detail}"
        f"{error_detail}{log_detail}"
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
    server_state_path = log_root / "latest-simulator-server-state.json"
    agent_state_path = log_root / "latest-simulator-state.json"
    s1_port = _free_port({port})

    with (
        server_log_path.open("w", encoding="utf-8") as server_log,
        agent_log_path.open("w", encoding="utf-8") as agent_log,
    ):
        server_state_path.unlink(missing_ok=True)
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
                    "--connection-state",
                    str(server_state_path),
                ),
            ),
            stdout=server_log,
            stderr=subprocess.STDOUT,
            text=True,
        )
        agent: subprocess.Popen[str] | None = None
        try:
            _wait_port(port)
            _wait_server_ready(
                server,
                server_state_path,
                endpoint,
                timeout_seconds=SERVER_READY_TIMEOUT_SECONDS,
                poll_interval_seconds=0.1,
            )
            agent_state_path.unlink(missing_ok=True)
            agent = subprocess.Popen(
                runtime_command(
                    "szlab-handshake",
                    agent_entry,
                    (
                        "--url",
                        endpoint,
                        "--config",
                        str(handshake_config),
                        "--state-file",
                        str(agent_state_path),
                        "--s1-port",
                        str(s1_port),
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
                timeout_seconds=AGENT_READY_TIMEOUT_SECONDS,
                poll_interval_seconds=max(
                    bundle.environment.poll_interval_ms / 1000,
                    0.05,
                ),
                log_paths=(server_log_path, agent_log_path),
                state_path=agent_state_path,
            )
            result = run_acceptance(bundle, selected_case_ids=selected_case_ids)
            report_dir = write_reports(result, output_root)
            return result, report_dir
        finally:
            if agent is not None:
                _stop_process(agent)
            _stop_process(server)
