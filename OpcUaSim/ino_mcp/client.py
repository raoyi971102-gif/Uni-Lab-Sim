"""
ino_mcp.client —— MCP stdio JSON-RPC 客户端
============================================================================
职责：
    以子进程方式启动 InoProShop MCP（Node.js bundle.min.js），走 stdin/stdout
    通信收发 JSON-RPC 2.0 消息，并暴露同步的 `call_tool(name, args)` API。

设计要点：
    - 一个后台线程持续读取 MCP 子进程 stdout，按行拆分并投递到 pending 请求。
    - initialize → notifications/initialized → tools/list 全部在启动握手中完成。
    - 允许工具调用长时间阻塞（编译/打开项目可能几十秒），默认超时 300 s。
    - MCP 子进程往 stderr 输出的调试日志会转到本进程 logger（level=DEBUG）。
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional


log = logging.getLogger("ino_mcp.client")


class McpError(RuntimeError):
    """MCP 层的错误（协议错、工具报错、子进程崩溃）。"""


class McpClient:
    """MCP stdio JSON-RPC 客户端（阻塞式，线程安全）。"""

    DEFAULT_TIMEOUT = 300.0  # 秒；编译/打开项目可能非常慢

    def __init__(
        self,
        bundle_js: str | os.PathLike,
        codesys_path: str | os.PathLike,
        codesys_profile: str,
        workspace: str | os.PathLike,
        *,
        node_cmd: str = "node",
        extra_args: Optional[List[str]] = None,
    ) -> None:
        self.bundle_js = str(Path(bundle_js).resolve())
        self.codesys_path = str(Path(codesys_path).resolve())
        self.codesys_profile = codesys_profile
        self.workspace = str(Path(workspace).resolve())
        self.node_cmd = node_cmd
        self.extra_args = list(extra_args or [])

        self._proc: Optional[subprocess.Popen] = None
        self._reader_th: Optional[threading.Thread] = None
        self._err_th: Optional[threading.Thread] = None
        self._pending: Dict[str, threading.Event] = {}
        self._responses: Dict[str, Any] = {}
        self._lock = threading.Lock()
        self._closed = False
        self._session_dir: Optional[Path] = None

        self.server_info: Dict[str, Any] = {}
        self.tools: List[Dict[str, Any]] = []

    # -- 生命周期 ---------------------------------------------------------
    def start(self, *, list_tools: bool = True) -> None:
        """启动 MCP 子进程并完成 initialize 握手。"""
        if self._proc is not None:
            raise McpError("MCP already started")

        args = [
            self.node_cmd, self.bundle_js,
            "--codesys-path", self.codesys_path,
            "--codesys-profile", self.codesys_profile,
            "--workspace", self.workspace,
            *self.extra_args,
        ]
        log.info("spawn: %s", " ".join(args))
        self._session_dir = Path(tempfile.mkdtemp(prefix="opcuasim-ino-session-"))
        child_env = os.environ.copy()
        child_env["OPCUASIM_INO_SESSION_DIR"] = str(self._session_dir)
        self._proc = subprocess.Popen(
            args,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
            text=False,   # 二进制读写，避免 Windows 换行/编码问题
            env=child_env,
        )

        self._reader_th = threading.Thread(
            target=self._reader_loop, name="mcp-stdout", daemon=True)
        self._reader_th.start()

        self._err_th = threading.Thread(
            target=self._stderr_loop, name="mcp-stderr", daemon=True)
        self._err_th.start()

        # initialize 握手
        resp = self._request("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {"roots": {"listChanged": False}},
            "clientInfo": {"name": "OpcUaSim-InoBridge", "version": "1.0.0"},
        }, timeout=60.0)
        self.server_info = resp.get("serverInfo", {})
        log.info("MCP initialized: serverInfo=%s", self.server_info)
        self._notify("notifications/initialized", {})

        if list_tools:
            self.refresh_tools()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            # The repository launcher keeps one InoProShop process alive. Ask
            # its IronPython host to close the project before terminating the
            # Node MCP transport. Direct third-party bundles simply ignore this
            # private session directory.
            if self._session_dir is not None and self.persistent_session:
                try:
                    (self._session_dir / "stop").write_text("stop", encoding="utf-8")
                    deadline = time.monotonic() + 8.0
                    while (
                        time.monotonic() < deadline
                        and not (self._session_dir / "stopped").exists()
                    ):
                        time.sleep(0.1)
                except OSError:
                    log.debug("persistent session stop marker failed", exc_info=True)
            if self._proc is not None and self._proc.poll() is None:
                self._proc.terminate()
                try:
                    self._proc.wait(timeout=5.0)
                except subprocess.TimeoutExpired:
                    self._proc.kill()
        except Exception:  # noqa: BLE001
            log.debug("close error", exc_info=True)
        finally:
            if self._session_dir is not None:
                shutil.rmtree(self._session_dir, ignore_errors=True)
                self._session_dir = None
        log.info("MCP client closed")

    @property
    def persistent_session(self) -> bool:
        return Path(self.bundle_js).name.lower() == "persistent-launcher.js"

    @property
    def host_pid(self) -> Optional[int]:
        if self._session_dir is None:
            return None
        try:
            return int((self._session_dir / "ready").read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            return None

    def __enter__(self) -> "McpClient":
        self.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    # -- 公共 API ---------------------------------------------------------
    def refresh_tools(self) -> List[Dict[str, Any]]:
        resp = self._request("tools/list", {}, timeout=30.0)
        self.tools = resp.get("tools", [])
        log.info("MCP tools available: %d 个", len(self.tools))
        return self.tools

    def has_tool(self, name: str) -> bool:
        return any(t.get("name") == name for t in self.tools)

    def call_tool(self, name: str, arguments: Optional[Dict[str, Any]] = None,
                  *, timeout: Optional[float] = None) -> str:
        """调用一个 MCP 工具，返回 content[0].text（供上层解析）。"""
        resp = self._request(
            "tools/call",
            {"name": name, "arguments": arguments or {}},
            timeout=timeout if timeout is not None else self.DEFAULT_TIMEOUT,
        )
        content = resp.get("content") or []
        is_error = bool(resp.get("isError"))
        text = "".join(
            (c.get("text", "") if isinstance(c, dict) else "")
            for c in content
        )
        if is_error:
            raise McpError(f"tool '{name}' failed: {text.strip()[:1000]}")
        return text

    # -- 内部：JSON-RPC 收发 ---------------------------------------------
    def _next_id(self) -> str:
        return uuid.uuid4().hex

    def _send(self, obj: Dict[str, Any]) -> None:
        if self._proc is None or self._proc.stdin is None:
            raise McpError("MCP process not running")
        line = json.dumps(obj, ensure_ascii=False) + "\n"
        try:
            self._proc.stdin.write(line.encode("utf-8"))
            self._proc.stdin.flush()
        except (BrokenPipeError, OSError) as exc:  # 子进程崩了
            raise McpError(f"pipe error: {exc}") from exc

    def _request(self, method: str, params: Dict[str, Any],
                 *, timeout: float) -> Dict[str, Any]:
        rid = self._next_id()
        evt = threading.Event()
        with self._lock:
            self._pending[rid] = evt
        self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        if not evt.wait(timeout=timeout):
            with self._lock:
                self._pending.pop(rid, None)
            raise McpError(f"{method} timeout after {timeout:.1f}s")
        with self._lock:
            resp = self._responses.pop(rid, None)
            self._pending.pop(rid, None)
        if resp is None:
            raise McpError(f"{method}: missing response")
        if "error" in resp:
            raise McpError(f"{method}: {resp['error']}")
        return resp.get("result", {})

    def _notify(self, method: str, params: Dict[str, Any]) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def _reader_loop(self) -> None:
        assert self._proc is not None and self._proc.stdout is not None
        buf = b""
        while True:
            try:
                chunk = self._proc.stdout.read(4096)
            except Exception:  # noqa: BLE001
                log.debug("stdout read exception", exc_info=True)
                break
            if not chunk:
                log.info("MCP stdout closed (child exited?)")
                # 唤醒全部 pending 请求，避免主线程死锁
                with self._lock:
                    for rid, evt in list(self._pending.items()):
                        self._responses[rid] = {"error": {"code": -1, "message": "child exited"}}
                        evt.set()
                break
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                self._handle_line(line.rstrip(b"\r"))

    def _handle_line(self, raw: bytes) -> None:
        if not raw:
            return
        try:
            msg = json.loads(raw.decode("utf-8"))
        except Exception:  # 非 JSON 行（例如子进程 log 到 stdout）
            log.debug("mcp-stdout(raw): %s", raw[:200])
            return
        rid = msg.get("id")
        if rid is not None and rid in self._pending:
            with self._lock:
                self._responses[rid] = msg
                evt = self._pending.get(rid)
            if evt is not None:
                evt.set()
        else:
            # 服务端通知/日志
            log.debug("mcp-notify: %s", str(msg)[:400])

    def _stderr_loop(self) -> None:
        assert self._proc is not None and self._proc.stderr is not None
        while True:
            try:
                line = self._proc.stderr.readline()
            except Exception:  # noqa: BLE001
                break
            if not line:
                break
            try:
                text = line.decode("utf-8", errors="replace").rstrip()
            except Exception:  # noqa: BLE001
                text = repr(line)
            if text:
                log.debug("[mcp-stderr] %s", text)
