"""GUI 后端共享运行状态。

这个模块是各路由模块共享的状态 seam。它只保存控制面板会话、托管进程和
OPC UA 节点元数据，不依赖 FastAPI 路由，因此工程、Server 和 Agent 模块可以
独立演进而不互相导入。
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    from ..common import NodeDef, connection_state_path
    from ..ino_mcp.client import McpClient
    from ..ino_mcp.project_versions import ProjectVersionRepo
    from ..ino_mcp.toolkit import InoToolkit
except ImportError:  # Source checkout: ``import gui.backend``.
    from common import NodeDef, connection_state_path
    from ino_mcp.client import McpClient
    from ino_mcp.project_versions import ProjectVersionRepo
    from ino_mcp.toolkit import InoToolkit


def read_json_file(path: str | None) -> Any:
    """读取运行期 JSON；文件缺失或损坏时返回 ``None``。"""

    if not path:
        return None
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def write_json_file(path: Path, payload: Any) -> None:
    """通过同目录临时文件原子替换运行期 JSON。"""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(temporary, path)


def empty_connection_state(
    *, available: bool = False, stale: bool = False
) -> dict[str, Any]:
    """构造统一的空连接遥测响应。"""

    return {
        "available": available,
        "stale": stale,
        "generated_at": None,
        "tcp_connection_count": 0,
        "session_count": 0,
        "clients": [],
    }


def read_server_connection_state(
    *, expected_pid: int | None, running: bool
) -> dict[str, Any]:
    """读取 Server 连接快照，并过滤过期或其他进程的数据。"""

    if not running:
        return empty_connection_state()
    try:
        payload = json.loads(connection_state_path().read_text(encoding="utf-8"))
        generated_at = float(payload["generated_at"])
        server_pid = int(payload["server_pid"])
        if time.time() - generated_at > 3.0:
            return empty_connection_state(stale=True)
        if expected_pid is not None and server_pid != expected_pid:
            return empty_connection_state(stale=True)
        clients = payload.get("clients")
        if not isinstance(clients, list):
            raise TypeError("clients 不是列表")
        return {
            "available": True,
            "stale": False,
            "generated_at": generated_at,
            "tcp_connection_count": int(
                payload.get("tcp_connection_count", len(clients))
            ),
            "session_count": int(payload.get("session_count", 0)),
            "clients": clients[:200],
        }
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return empty_connection_state()


@dataclass
class AppState:
    """保存 GUI 生命周期内共享、但不持久化的运行状态。"""

    mcp: McpClient | None = None
    toolkit: InoToolkit | None = None
    current_project: str | None = None
    version_repo: ProjectVersionRepo | None = None
    busy: str | None = None
    server_proc: subprocess.Popen | None = None
    agent_proc: subprocess.Popen | None = None
    agent_profile: str | None = None
    agent_state_file: str | None = None
    ptlc_fault_file: str | None = None
    ptlc_state_file: str | None = None
    ptlc_world_file: str | None = None
    attached: bool = False
    server_client_url: str | None = None
    server_csv_paths: list[str] = field(default_factory=list)
    server_node_defs: list[NodeDef] = field(default_factory=list)
    server_csv_id: str | None = None
    last_extract_csv: str | None = None
    last_extract_count: int = 0
    stopping: set[str] = field(default_factory=set)
    log_queues: set[asyncio.Queue] = field(default_factory=set)
    mcp_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    server_io_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    loop: asyncio.AbstractEventLoop | None = None
    last_error: str | None = None
    declarations_dump: str | None = None
    editables_cache: list[dict[str, Any]] | None = None
    warm_raw: str | None = None

    def snapshot(self) -> dict[str, Any]:
        """生成可以直接返回给前端的不可变状态快照。"""

        def alive(process: subprocess.Popen | None) -> int | None:
            return process.pid if process and process.poll() is None else None

        server_pid = alive(self.server_proc)
        server_running = self.attached or (
            server_pid is not None and bool(self.server_client_url)
        )
        return {
            "project": self.current_project,
            "mcp_connected": self.mcp is not None,
            "mcp_session": {
                "persistent": bool(self.mcp and self.mcp.persistent_session),
                "host_pid": self.mcp.host_pid if self.mcp else None,
            },
            "busy": self.busy,
            "server": {
                "pid": server_pid,
                "running": server_running,
                "stopping": "server_proc" in self.stopping,
                "attached": self.attached,
                "endpoint": self.server_client_url,
                "variable_count": len(self.server_node_defs),
                "csv": list(self.server_csv_paths),
                "csv_id": self.server_csv_id,
                "connections": read_server_connection_state(
                    expected_pid=server_pid,
                    running=server_running,
                ),
            },
            "agent": {
                "pid": alive(self.agent_proc),
                "running": self.attached or alive(self.agent_proc) is not None,
                "stopping": "agent_proc" in self.stopping,
                "attached": self.attached,
                "profile": self.agent_profile,
                "state": read_json_file(self.agent_state_file),
                "ptlc_state": read_json_file(self.ptlc_state_file),
            },
            "last_extract_csv": self.last_extract_csv,
            "last_extract_count": self.last_extract_count,
            "last_error": self.last_error,
        }


STATE = AppState()
