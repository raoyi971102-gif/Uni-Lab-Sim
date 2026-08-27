"""GUI 托管子进程的统一生命周期实现。"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

try:
    from ..common import connection_state_path
except ImportError:  # Source checkout: ``import gui.backend``.
    from common import connection_state_path

from .backend_state import STATE

ROOT = Path(__file__).resolve().parent.parent
_STOP_LOCKS = {
    "server_proc": asyncio.Lock(),
    "agent_proc": asyncio.Lock(),
}


def find_python_exe() -> str:
    """探测真实 Python 可执行文件，并跳过 WindowsApps 存根。"""

    env_python = os.environ.get("PYTHON")
    if env_python and Path(env_python).exists():
        return env_python
    for candidate in (
        r"D:\miniforge3\envs\unilab\python.exe",
        r"D:\miniforge3\python.exe",
    ):
        if Path(candidate).exists():
            return candidate
    if sys.executable and "WindowsApps" not in sys.executable:
        return sys.executable
    return "python"


def python_subprocess_env() -> dict[str, str]:
    """构造统一使用 UTF-8 的子进程环境。"""

    environment = os.environ.copy()
    environment["PYTHONIOENCODING"] = "utf-8"
    environment["PYTHONUTF8"] = "1"
    return environment


def pipe_to_logger(process: subprocess.Popen, logger_name: str) -> None:
    """把子进程 stdout/stderr 持续转发到 GUI 日志。"""

    target = logging.getLogger(logger_name)

    def read_stream(stream: Any, level: int) -> None:
        if stream is None:
            return
        for raw in iter(stream.readline, b""):
            try:
                text = raw.decode("utf-8", errors="replace").rstrip()
            except Exception:  # noqa: BLE001
                text = repr(raw)
            if text:
                target.log(level, text)

    threading.Thread(
        target=read_stream,
        args=(process.stdout, logging.INFO),
        name=f"{logger_name}-out",
        daemon=True,
    ).start()
    threading.Thread(
        target=read_stream,
        args=(process.stderr, logging.WARNING),
        name=f"{logger_name}-err",
        daemon=True,
    ).start()


def clear_server_metadata(*, remove_connection_state: bool = False) -> None:
    """清除仅在 Server 运行期间有效的连接和节点元数据。"""

    STATE.server_client_url = None
    STATE.server_csv_paths = []
    STATE.server_node_defs = []
    STATE.server_csv_id = None
    if remove_connection_state:
        with contextlib.suppress(OSError):
            connection_state_path().unlink(missing_ok=True)


def terminate_and_wait(process: subprocess.Popen) -> dict[str, Any]:
    """终止进程并回收句柄；超时后升级为强制终止。"""

    forced = False
    try:
        process.terminate()
        try:
            exit_code = process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            forced = True
            process.kill()
            exit_code = process.wait(timeout=2)
        return {
            "ok": True,
            "message": "已停止",
            "pid": process.pid,
            "exit_code": exit_code,
            "forced": forced,
        }
    except Exception as exc:  # noqa: BLE001
        if process.poll() is not None:
            return {
                "ok": True,
                "message": "已停止",
                "pid": process.pid,
                "exit_code": process.returncode,
                "forced": forced,
            }
        return {
            "ok": False,
            "message": str(exc),
            "pid": process.pid,
            "forced": forced,
        }


async def stop_subprocess(field_name: str) -> dict[str, Any]:
    """停止一个由 GUI 托管的进程，且不阻塞 FastAPI 事件循环。"""

    lock = _STOP_LOCKS[field_name]
    async with lock:
        process: subprocess.Popen | None = getattr(STATE, field_name)
        if process is None or process.poll() is not None:
            setattr(STATE, field_name, None)
            if field_name == "server_proc":
                clear_server_metadata(remove_connection_state=not STATE.attached)
            return {"ok": True, "message": "已经停止或未运行"}

        logging.getLogger("gui.processes").info(
            "终止子进程 %s pid=%d", field_name, process.pid
        )
        STATE.stopping.add(field_name)
        try:
            result = await asyncio.to_thread(terminate_and_wait, process)
            if result["ok"] and getattr(STATE, field_name) is process:
                setattr(STATE, field_name, None)
                if field_name == "server_proc":
                    clear_server_metadata(remove_connection_state=True)
            if result.get("forced"):
                logging.getLogger("gui.processes").warning(
                    "子进程 %s pid=%d 未及时退出，已强制终止",
                    field_name,
                    process.pid,
                )
            return result
        finally:
            STATE.stopping.discard(field_name)
