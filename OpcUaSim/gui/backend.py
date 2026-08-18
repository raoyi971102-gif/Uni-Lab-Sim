"""
gui.backend —— OpcUaSim 一体化 Web 控制面板
============================================================================
一进程内集成：
  * InoProShop MCP 桥（打开/编辑/编译/下载/结构探查/GVL 提取 → CSV）
  * OPC UA Server 子进程管理（server.py）
  * 握手代理子进程管理（szlab_handshake_agent.py）
  * 全局实时日志（SSE 推送到前端）

进程模型：
  - 一个 FastAPI 应用常驻，UI 通过 HTTP+SSE 交互
  - 长阻塞任务（open/compile/download/extract）用 asyncio.to_thread 卸到线程池
  - 一次只允许一个 MCP 长任务；用 asyncio.Lock 串行化
  - Server / Agent 是独立子进程；stdout/stderr 由后台线程转发到主 logger
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import logging
import math
import os
import queue
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, StreamingResponse, JSONResponse, HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from opcua import Client, ua
from pydantic import BaseModel, Field

# 允许直接 `python -m gui.backend` 时找到根包
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

try:
    from ..cli import runtime_command
    from ..common import (
        NodeDef,
        VTYPE_MAP,
        connection_state_path,
        default_csv_path,
        load_csvs,
        node_defs_fingerprint,
        runtime_data_dir,
    )
    from ..ino_mcp.client import McpClient, McpError
    from ..ino_mcp.config import resolve_mcp_config
    from ..ino_mcp.toolkit import InoToolkit, DownloadStrategy
    from ..ino_mcp.extractor import (
        extract_gvl_variables,
        parse_gvl_declaration,
        write_csv,
        _to_csv_rows,
        list_editables_from_dump,
        build_dut_registry_from_dump,
        build_dut_registry_from_warm,
        parse_warm_dump,
    )
except ImportError:  # Direct `python -m gui.backend` compatibility.
    from cli import runtime_command
    from common import (
        NodeDef,
        VTYPE_MAP,
        connection_state_path,
        default_csv_path,
        load_csvs,
        node_defs_fingerprint,
        runtime_data_dir,
    )
    from ino_mcp.client import McpClient, McpError
    from ino_mcp.config import resolve_mcp_config
    from ino_mcp.toolkit import InoToolkit, DownloadStrategy
    from ino_mcp.extractor import (
        extract_gvl_variables,
        parse_gvl_declaration,
        write_csv,
        _to_csv_rows,
        list_editables_from_dump,
        build_dut_registry_from_dump,
        build_dut_registry_from_warm,
        parse_warm_dump,
    )


# 保持 GUI 后端启动契约不依赖可执行 Agent 模块，避免导入时初始化 Agent logger。
# tests/test_gui_agent_config.py 会校验这里、Agent 和 HTML 三处目录完全一致。
SZLAB_WORKFLOW_IDS = (
    "szlab_magnetic_stirring_workflow",
    "szlab_photoshotting_workflow",
    "szlab_robot_action_workflow",
    "s04_robot_stirring_workflow",
    "s06_robot_workflow",
    "s07_robot_workflow",
    "szlab_s07_solid_addition_workflow",
    "s08_cap_workflow",
    "s09_移液调试",
    "szlab_stack_s05_s06_workflow",
    "szlab_mixer_workflow",
    "szlab_mixer_pump_production",
    "szlab_material_s06_workflow",
    "s07_粉桶与烧杯搬运后固体称量",
    "s_z_lab_标准物料转运",
    "s_z_lab_单样品全流程_物料感知",
    "s_z_lab_单样品原子流程_无_s07_扫码",
    "s_z_lab_单样品原子流程_机器人原子动作",
    "s_z_lab_双任务单样品原子流程_无_s07_扫码",
    "s_z_lab_双任务单样品原子流程_机器人原子动作",
    "s_z_lab_烧杯五工位搬运",
)
SZLAB_WORKFLOW_ALIASES = (
    "s07_material_dosing",
    "szlab_s09_pipetting_workflow",
)


# ---------------------------------------------------------------------------
# 全局日志 → SSE 桥
# ---------------------------------------------------------------------------
class _SseLogHandler(logging.Handler):
    """跨线程安全的 SSE 日志广播。
    子进程读线程 / McpClient 读线程都会走到 emit()，
    必须用 loop.call_soon_threadsafe 把 put_nowait 调回主事件循环，
    否则会破坏 asyncio.Queue 内部状态导致 uvicorn 静默崩溃。
    """
    def emit(self, record: logging.LogRecord) -> None:
        entry = {
            "ts": record.created,
            "level": record.levelname,
            "source": record.name,
            "msg": record.getMessage(),
        }
        loop = _STATE.loop
        if loop is None or not loop.is_running():
            return
        for q in list(_STATE.log_queues):
            try:
                loop.call_soon_threadsafe(_safe_put, q, entry)
            except RuntimeError:
                # loop closed
                pass
            except Exception:  # noqa: BLE001
                pass


def _safe_put(q: "asyncio.Queue", entry: dict) -> None:
    try:
        q.put_nowait(entry)
    except asyncio.QueueFull:
        # 丢弃最老的一条，塞进新的
        try:
            q.get_nowait()
            q.put_nowait(entry)
        except Exception:  # noqa: BLE001
            pass
    except Exception:  # noqa: BLE001
        pass


def _install_root_logger() -> None:
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    # 控制台
    if not any(isinstance(h, logging.StreamHandler) and not isinstance(h, _SseLogHandler) for h in root.handlers):
        sh = logging.StreamHandler()
        sh.setFormatter(logging.Formatter(
            "%(asctime)s.%(msecs)03d [%(levelname)s] %(name)s: %(message)s",
            datefmt="%H:%M:%S"))
        root.addHandler(sh)
    # SSE
    if not any(isinstance(h, _SseLogHandler) for h in root.handlers):
        root.addHandler(_SseLogHandler())
    # 降噪
    logging.getLogger("opcua").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("watchfiles").setLevel(logging.WARNING)


# ---------------------------------------------------------------------------
# 全局状态
# ---------------------------------------------------------------------------
@dataclass
class AppState:
    mcp: Optional[McpClient] = None
    toolkit: Optional[InoToolkit] = None
    current_project: Optional[str] = None
    busy: Optional[str] = None
    server_proc: Optional[subprocess.Popen] = None
    agent_proc: Optional[subprocess.Popen] = None
    # Server/Agent 由外部进程管理器（Supervisor、systemd）托管，不由本进程 spawn
    attached: bool = False
    server_client_url: Optional[str] = None
    server_csv_paths: List[str] = field(default_factory=list)
    server_node_defs: List[NodeDef] = field(default_factory=list)
    server_csv_id: Optional[str] = None
    last_extract_csv: Optional[str] = None
    last_extract_count: int = 0
    stopping: Set[str] = field(default_factory=set)
    log_queues: Set[asyncio.Queue] = field(default_factory=set)
    mcp_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    server_io_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    loop: Optional[asyncio.AbstractEventLoop] = None
    last_error: Optional[str] = None
    # dump_all_declarations 的缓存 (供 editables + extract 复用, 避免 20s 探针跑两次)
    declarations_dump: Optional[str] = None
    editables_cache: Optional[List[Dict[str, Any]]] = None
    warm_raw: Optional[str] = None            # 上次 warm_all_code 的原始输出 (供 /api/project/warm/raw 排障)

    def snapshot(self) -> Dict[str, Any]:
        def _alive(p: Optional[subprocess.Popen]) -> Optional[int]:
            return p.pid if p and p.poll() is None else None

        server_pid = _alive(self.server_proc)
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
                # endpoint 就绪前不报 running：否则前端会在 _wait_for_opc_server
                # 的窗口里抢跑去拉变量，吃一个 409 后就不会再自动重试
                "running": server_running,
                "stopping": "server_proc" in self.stopping,
                "attached": self.attached,
                "endpoint": self.server_client_url,
                "variable_count": len(self.server_node_defs),
                "csv": list(self.server_csv_paths),
                "csv_id": self.server_csv_id,
                "connections": _read_server_connection_state(
                    expected_pid=server_pid,
                    running=server_running,
                ),
            },
            "agent": {
                "pid": _alive(self.agent_proc),
                "running": self.attached or _alive(self.agent_proc) is not None,
                "stopping": "agent_proc" in self.stopping,
                "attached": self.attached,
            },
            "last_extract_csv": self.last_extract_csv,
            "last_extract_count": self.last_extract_count,
            "last_error": self.last_error,
        }


_STATE = AppState()
log = logging.getLogger("gui")


def _empty_connection_state(*, available: bool = False, stale: bool = False) -> Dict[str, Any]:
    return {
        "available": available,
        "stale": stale,
        "generated_at": None,
        "tcp_connection_count": 0,
        "session_count": 0,
        "clients": [],
    }


def _read_server_connection_state(
    *,
    expected_pid: Optional[int],
    running: bool,
) -> Dict[str, Any]:
    """读取 Server 写入的连接快照并过滤过期或其他进程的数据。"""
    if not running:
        return _empty_connection_state()
    try:
        payload = json.loads(connection_state_path().read_text(encoding="utf-8"))
        generated_at = float(payload["generated_at"])
        server_pid = int(payload["server_pid"])
        if time.time() - generated_at > 3.0:
            return _empty_connection_state(stale=True)
        if expected_pid is not None and server_pid != expected_pid:
            return _empty_connection_state(stale=True)
        clients = payload.get("clients")
        if not isinstance(clients, list):
            raise ValueError("clients 不是列表")
        return {
            "available": True,
            "stale": False,
            "generated_at": generated_at,
            "tcp_connection_count": int(payload.get("tcp_connection_count", len(clients))),
            "session_count": int(payload.get("session_count", 0)),
            "clients": clients[:200],
        }
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return _empty_connection_state()


# ---------------------------------------------------------------------------
# MCP 配置解析
# ---------------------------------------------------------------------------
def _load_mcp_defaults(server_name: str = "codesys_local") -> Dict[str, Any]:
    return resolve_mcp_config(server_name=server_name)


# ---------------------------------------------------------------------------
# 子进程 stdout 转发到 logger
# ---------------------------------------------------------------------------
def _pipe_to_logger(proc: subprocess.Popen, logger_name: str) -> None:
    lg = logging.getLogger(logger_name)

    def _reader(stream, level: int):
        for raw in iter(stream.readline, b""):
            try:
                text = raw.decode("utf-8", errors="replace").rstrip()
            except Exception:  # noqa: BLE001
                text = repr(raw)
            if text:
                lg.log(level, text)

    threading.Thread(target=_reader, args=(proc.stdout, logging.INFO),
                     name=f"{logger_name}-out", daemon=True).start()
    threading.Thread(target=_reader, args=(proc.stderr, logging.WARNING),
                     name=f"{logger_name}-err", daemon=True).start()


# ---------------------------------------------------------------------------
# FastAPI 应用
# ---------------------------------------------------------------------------
from contextlib import asynccontextmanager


def _attach_external_server() -> None:
    """挂接由 Supervisor/systemd 托管的 Server 与 Agent，让在线变量面板可用。

    只记录 endpoint 和 CSV 定义，不做健康检查 —— 外部 Server 掉线时读写会以
    502 暴露出来，比在这里维护一份可能过期的存活状态更诚实。
    """
    url = os.environ.get("OPCUASIM_ATTACH_URL")
    if not url:
        return
    csv_path = Path(os.environ.get("OPCUASIM_ATTACH_CSV")
                    or os.environ.get("OPCUASIM_CSV")
                    or default_csv_path())
    try:
        node_defs = load_csvs([csv_path])
    except Exception as exc:  # noqa: BLE001
        log.error("挂接外部 Server 失败，CSV 无法解析 (%s): %s", csv_path, exc)
        return
    if not node_defs:
        log.error("挂接外部 Server 失败，CSV 中没有 VARIABLE 节点: %s", csv_path)
        return
    _STATE.attached = True
    _STATE.server_client_url = url
    _STATE.server_csv_paths = [str(csv_path.resolve())]
    _STATE.server_node_defs = node_defs
    _STATE.server_csv_id = node_defs_fingerprint(node_defs)
    log.info("已挂接外部 Server: %s (%d 个变量, CSV=%s)", url, len(node_defs), csv_path)


@asynccontextmanager
async def _lifespan(app):
    _install_root_logger()
    _attach_external_server()
    _STATE.loop = asyncio.get_running_loop()
    _STATIC_DIR_LOCAL = Path(__file__).resolve().parent / "static"
    log.info("OpcUaSim GUI started, static=%s", _STATIC_DIR_LOCAL)
    try:
        yield
    finally:
        log.info("shutting down…")
        await _stop_subprocess("server_proc")
        await _stop_subprocess("agent_proc")
        if _STATE.mcp is not None:
            try:
                _STATE.mcp.close()
            except Exception:  # noqa: BLE001
                pass


app = FastAPI(title="OpcUaSim Control Panel", version="1.0.0", lifespan=_lifespan)

_STATIC_DIR = Path(__file__).resolve().parent / "static"
app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")


# 关闭浏览器缓存 —— 开发阶段不容许浏览器复用旧 CSS/JS
# (曾经出现: 改了 CSS/JS 用户看不到; 因为 Ctrl+F5 也未必绕过 disk cache)
@app.middleware("http")
async def _no_cache_for_static(request, call_next):
    resp = await call_next(request)
    p = request.url.path
    if p == "/" or p.startswith("/static/"):
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        resp.headers["Pragma"] = "no-cache"
        resp.headers["Expires"] = "0"
    return resp


# -- 首页 ------------------------------------------------------------------
# 用 mtime 作 cache-busting: <link href="style.css?v=1234567"> —— 文件一改, URL 就变,
# 无论浏览器如何积极缓存都会重新拉。
def _bust(fname: str) -> str:
    try:
        return str(int((_STATIC_DIR / fname).stat().st_mtime))
    except FileNotFoundError:
        return "0"


@app.get("/")
async def _root() -> HTMLResponse:
    html = (_STATIC_DIR / "index.html").read_text(encoding="utf-8")
    html = html.replace('href="/static/style.css"',
                        f'href="/static/style.css?v={_bust("style.css")}"')
    html = html.replace('src="/static/app.js"',
                        f'src="/static/app.js?v={_bust("app.js")}"')
    return HTMLResponse(html)


# -- 状态 ------------------------------------------------------------------
@app.get("/api/state")
async def api_state() -> Dict[str, Any]:
    return _STATE.snapshot()


@app.get("/api/health")
async def api_health() -> Dict[str, Any]:
    return {"ok": True, "ts": time.time()}


# 后端启动的墙钟时间, 用于判定"你现在跑的是不是老 backend"
_BACKEND_START_TS = time.time()


def _read_release() -> str:
    """VERSION 由 CI 在 rsync 前写入（短 SHA + 构建时间）。本地开发没有这个文件。"""
    try:
        return (_ROOT / "VERSION").read_text(encoding="utf-8").strip() or "dev"
    except OSError:
        try:
            from importlib.metadata import version

            return version("unilab-opcua-sim")
        except Exception:  # Package metadata is absent during source execution.
            return "dev"


_RELEASE = _read_release()


@app.get("/api/version")
async def api_version() -> Dict[str, Any]:
    """诊断: 报告后端启动时间 + 静态资源 mtime + 是否有新增端点。
    页面右上角 buildBadge 会显示 backend_started 的 mm:ss, 一眼判断是不是老 backend。
    """
    def _mtime(name: str) -> Optional[float]:
        p = _STATIC_DIR / name
        return p.stat().st_mtime if p.exists() else None

    return {
        "release": _RELEASE,
        "backend_started": _BACKEND_START_TS,
        "backend_pid": os.getpid(),
        "static_mtime": {
            "index.html": _mtime("index.html"),
            "style.css":  _mtime("style.css"),
            "app.js":     _mtime("app.js"),
        },
        # 一个方案 A 版本才有的 endpoint 列表 —— 老 backend 不会有这些
        "has_endpoints": {
            "/api/project/editables": True,
            "/api/project/warm":      True,
            "/api/project/cache":     True,
        },
    }


# -- 项目 ------------------------------------------------------------------
class OpenReq(BaseModel):
    path: str
    bundle: Optional[str] = None
    codesys_path: Optional[str] = None
    codesys_profile: Optional[str] = None
    workspace: Optional[str] = None
    node: Optional[str] = None


@app.post("/api/project/open")
async def api_project_open(req: OpenReq) -> Dict[str, Any]:
    proj = str(Path(req.path).resolve())
    if not Path(proj).exists():
        raise HTTPException(400, f".project 不存在: {proj}")

    async with _STATE.mcp_lock:
        # 若已经连着别的项目, 先关掉
        if _STATE.mcp is not None:
            log.info("先关闭旧 MCP: %s", _STATE.current_project)
            await asyncio.to_thread(_STATE.mcp.close)
            _STATE.mcp = None
            _STATE.toolkit = None
            _STATE.current_project = None
        # 新项目, 清缓存
        _STATE.declarations_dump = None
        _STATE.editables_cache = None

        cfg = _load_mcp_defaults()
        if req.bundle:
            cfg["bundle_js"] = req.bundle
        if req.codesys_path:
            cfg["codesys_path"] = req.codesys_path
        if req.codesys_profile:
            cfg["codesys_profile"] = req.codesys_profile
        cfg["workspace"] = req.workspace or str(Path(proj).parent)
        if req.node:
            cfg["node_cmd"] = req.node

        if not cfg["bundle_js"] or not Path(cfg["bundle_js"]).exists():
            raise HTTPException(
                400,
                "找不到 MCP bundle.min.js；请设置 OPCUASIM_MCP_BUNDLE、"
                "配置 MCP JSON，或在请求中指定 bundle",
            )
        if not cfg["codesys_path"] or not Path(cfg["codesys_path"]).exists():
            raise HTTPException(
                400,
                "找不到 InoProShop.exe；请设置 OPCUASIM_INOPROSHOP_EXE "
                "或在请求中指定 codesys_path",
            )

        _STATE.busy = "opening"
        try:
            mcp = McpClient(bundle_js=cfg["bundle_js"], codesys_path=cfg["codesys_path"],
                            codesys_profile=cfg["codesys_profile"], workspace=cfg["workspace"],
                            node_cmd=cfg["node_cmd"])
            await asyncio.to_thread(mcp.start)
            tk = InoToolkit(mcp, proj)
            out = await asyncio.to_thread(tk.open_project)
            _STATE.mcp = mcp
            _STATE.toolkit = tk
            _STATE.current_project = proj
            _STATE.last_error = None
            log.info("项目已打开: %s", proj)
            return {"ok": True, "message": out.strip(), "state": _STATE.snapshot()}
        except Exception as exc:  # noqa: BLE001
            err = f"{type(exc).__name__}: {exc}"
            log.exception("open_project 失败: %s", err)
            _STATE.last_error = err
            with contextlib.suppress(Exception, NameError):
                mcp.close()  # type: ignore[has-type,used-before-def]
            _STATE.mcp = None
            _STATE.toolkit = None
            _STATE.current_project = None
            raise HTTPException(500, err)
        finally:
            _STATE.busy = None


@app.post("/api/project/close")
async def api_project_close() -> Dict[str, Any]:
    async with _STATE.mcp_lock:
        if _STATE.mcp is not None:
            await asyncio.to_thread(_STATE.mcp.close)
        _STATE.mcp = None
        _STATE.toolkit = None
        _STATE.current_project = None
        _STATE.declarations_dump = None
        _STATE.editables_cache = None
        log.info("已断开 MCP")
        return {"ok": True, "state": _STATE.snapshot()}


async def _ensure_declarations_dump(force: bool = False) -> str:
    """拿 dump_all_declarations 的结果 (带缓存, 供 editables + extract 复用)。"""
    if not force and _STATE.declarations_dump:
        return _STATE.declarations_dump
    tk = _require_tk()
    _STATE.busy = "scanning"
    try:
        dump = await asyncio.to_thread(tk.dump_all_declarations)
    finally:
        _STATE.busy = None
    _STATE.declarations_dump = dump
    _STATE.editables_cache = None   # 让 editables 端点重算
    return dump


def _synth_dump_from_warm_entries(entries) -> str:
    """把 warm_all_code 的解析结果反过来合成一份跟 dump_all_declarations 输出格式一致的字符串。
    这样 extract 端点里现有的 build_dut_registry_from_dump 逻辑无需改就能复用。
    """
    parts = []
    for e in entries:
        parts.append("===DECL_BEGIN===")
        parts.append("PATH: " + e.path)
        parts.append("IMPL: " + ("1" if e.has_impl else "0"))
        parts.append("MIXIN: <from-warm>")
        parts.append("---BODY---")
        parts.append(e.declaration)
        parts.append("===DECL_END===")
    return "\n".join(parts)


@app.get("/api/project/editables")
async def api_project_editables(refresh: bool = False) -> Dict[str, Any]:
    """列出项目里所有可编辑对象 (POU / GVL / DUT)。带缓存 —— 首次约 20s (跑 IronPython 探针),
    后续瞬时；refresh=true 强制重跑。
    """
    _require_tk()
    if not refresh and _STATE.editables_cache is not None:
        return {"ok": True, "cached": True, "items": _STATE.editables_cache}
    async with _STATE.mcp_lock:
        dump = await _ensure_declarations_dump(force=refresh)
        items_dc = list_editables_from_dump(dump)
        items = [
            {"name": e.name, "path": e.path, "kind": e.kind,
             "has_impl": e.has_implementation, "lang": e.lang}
            for e in items_dc
        ]
        _STATE.editables_cache = items
        return {"ok": True, "cached": False, "items": items}


@app.post("/api/project/warm")
async def api_project_warm() -> Dict[str, Any]:
    """项目预热: 一次探针把所有 POU/GVL/DUT 的声明 + 实现全部拉回来并塞满缓存。

    项目打开成功后前端 fire-and-forget 调这个 —— 之后:
      - 单独读任何 POU/GVL 都 <50ms 命中 pou_code cache
      - editables 列表已经在 _STATE.editables_cache 里
      - extract 时的 DUT registry 也已经建好, 秒出

    ~20s (跟 dump_all_declarations 同数量级, 因为都是 walk 一遍 Application)。
    """
    tk = _require_tk()
    async with _STATE.mcp_lock:
        _STATE.busy = "warming"
        try:
            warm_text = await asyncio.to_thread(tk.warm_all_code)
            _STATE.warm_raw = warm_text
            entries = parse_warm_dump(warm_text)
            paths = [e.path for e in entries]

            # 排障: 从 raw 里 grep 出 WALK 到的对象, 对比 emit 出来的对象, 找出差集
            import re as _re
            walked = _re.findall(r"^WALK\s+(.+)$", warm_text, _re.MULTILINE)
            not_emitted = _re.findall(r"^NOT_EMITTED\s+(.+)$", warm_text, _re.MULTILINE)
            skipped = _re.findall(r"^(SKIP_[A-Z_]+|OBJ_ERR|DEC_TEXT_ERR)\s+(.+?)(?::|$)",
                                  warm_text, _re.MULTILINE)
            if len(walked) != len(entries):
                log.warning("[warm] walk=%d 个对象 → emit=%d 个. NOT_EMITTED=%s SKIP=%s",
                            len(walked), len(entries), not_emitted, skipped)
                log.warning("[warm] 缺失: %s", set(walked) - set(paths))

            # 1) editables cache
            _STATE.editables_cache = [
                {"name": e.path.rsplit("/", 1)[-1], "path": e.path, "kind": e.kind,
                 "has_impl": e.has_impl, "lang": e.lang}
                for e in entries
            ]
            # 2) pou_code cache
            tk.prefill_pou_code_cache(
                [(e.path, e.declaration, e.implementation) for e in entries]
            )
            # 3) declarations_dump cache
            _STATE.declarations_dump = _synth_dump_from_warm_entries(entries)
            log.info("[warm] 项目预热完成: %d 对象 (walk=%d), cache=%s",
                     len(entries), len(walked), tk.cache.stats())
            return {"ok": True, "warmed": len(entries), "walked": len(walked),
                    "not_emitted": not_emitted,
                    "cache": tk.cache.stats(),
                    "kinds": {
                        "POU": sum(1 for e in entries if e.kind == "POU"),
                        "GVL": sum(1 for e in entries if e.kind == "GVL"),
                        "DUT": sum(1 for e in entries if e.kind == "DUT"),
                    }}
        finally:
            _STATE.busy = None


@app.get("/api/project/warm/raw", response_class=PlainTextResponse)
async def api_project_warm_raw() -> str:
    """诊断: 返回上次 warm_all_code 的原始输出 (供人肉排查为什么某些对象没被识别)。"""
    if _STATE.warm_raw is None:
        return "(还未跑过 warm — 先 POST /api/project/warm)"
    return _STATE.warm_raw


@app.get("/api/project/cache")
async def api_project_cache() -> Dict[str, Any]:
    """报告当前 toolkit + backend 的缓存命中情况 (调试 / GUI 显示用)。"""
    tk = _STATE.toolkit
    return {
        "toolkit": tk.cache.stats() if tk else None,
        "backend": {
            "declarations_dump": _STATE.declarations_dump is not None,
            "editables": len(_STATE.editables_cache) if _STATE.editables_cache else 0,
        },
    }


def _require_tk() -> InoToolkit:
    if _STATE.toolkit is None:
        raise HTTPException(400, "请先打开一个 .project 项目")
    return _STATE.toolkit


@app.post("/api/project/save")
async def api_project_save() -> Dict[str, Any]:
    tk = _require_tk()
    async with _STATE.mcp_lock:
        _STATE.busy = "saving"
        try:
            out = await asyncio.to_thread(tk.save_project)
            return {"ok": True, "message": out.strip()}
        finally:
            _STATE.busy = None


@app.post("/api/project/compile")
async def api_project_compile() -> Dict[str, Any]:
    tk = _require_tk()
    async with _STATE.mcp_lock:
        _STATE.busy = "compiling"
        try:
            cr = await asyncio.to_thread(tk.compile_project)
            return {"ok": cr.ok, "summary": cr.summary, "raw": cr.raw[-2000:] if cr.raw else ""}
        finally:
            _STATE.busy = None


class DownloadReq(BaseModel):
    strategy: str = "save_compile"    # 或 "online"


@app.post("/api/project/download")
async def api_project_download(req: DownloadReq) -> Dict[str, Any]:
    tk = _require_tk()
    try:
        strat = DownloadStrategy(req.strategy)
    except ValueError:
        raise HTTPException(400, f"未知 strategy: {req.strategy}")
    async with _STATE.mcp_lock:
        _STATE.busy = "downloading"
        try:
            report = await asyncio.to_thread(tk.download_program, strat)
            return {"ok": "error" not in report, "report": report}
        finally:
            _STATE.busy = None


@app.get("/api/project/structure")
async def api_project_structure() -> Dict[str, Any]:
    tk = _require_tk()
    async with _STATE.mcp_lock:
        _STATE.busy = "structure"
        try:
            text = await asyncio.to_thread(tk.get_project_structure)
            return {"ok": True, "text": text}
        finally:
            _STATE.busy = None


@app.get("/api/project/gvls")
async def api_project_gvls() -> Dict[str, Any]:
    _require_tk()
    # Structure text does not expose a reliable object type. The old heuristic
    # therefore found only objects whose *name* contained "GVL" and missed
    # perfectly valid tables such as IO, HMI_Date and Host_Computer. Reuse the
    # declaration scan, which classifies objects by VAR_GLOBAL content.
    editables = await api_project_editables(refresh=False)
    gvls = [item["path"] for item in editables["items"] if item["kind"] == "GVL"]
    return {"ok": True, "gvls": gvls, "source": "declarations"}


class ExtractReq(BaseModel):
    gvls: Optional[List[str]] = None
    include_all: bool = False
    ns_index: int = 4
    ns_prefix: str = "uniab|"
    node_language: str = "Chinese"      # CSV NodeLanguage 列的固定值
    out_path: Optional[str] = None      # 默认 extracted/<projectname>.csv
    preview_only: bool = False          # True 时只返回 rows, 不写盘
    expand_structs: bool = True         # False 时不自动拉 DUT registry (只展开 ARRAY)


@app.post("/api/project/extract")
async def api_project_extract(req: ExtractReq) -> Dict[str, Any]:
    tk = _require_tk()
    proj = Path(_STATE.current_project or "extracted")
    default_out = runtime_data_dir() / "extracted" / (proj.stem + ".csv")
    out_path = Path(req.out_path).resolve() if req.out_path else default_out

    async with _STATE.mcp_lock:
        _STATE.busy = "extracting"
        try:
            # 如果本次会话已经跑过 dump (比如用户先点了 '发现对象'), 直接复用它构造 registry;
            # 省一次 20s 探针
            dut_registry = None
            if req.expand_structs and _STATE.declarations_dump:
                dut_registry = build_dut_registry_from_dump(_STATE.declarations_dump)
                auto_build = False
            else:
                auto_build = req.expand_structs

            leaves = await asyncio.to_thread(
                extract_gvl_variables, tk,
                gvl_paths=req.gvls, include_all=req.include_all,
                dut_registry=dut_registry,
                auto_build_dut_registry=auto_build,
            )
            rows = _to_csv_rows(leaves, ns_index=req.ns_index,
                                ns_prefix=req.ns_prefix,
                                node_language=req.node_language)
            if not req.preview_only:
                out_path.parent.mkdir(parents=True, exist_ok=True)
                await asyncio.to_thread(write_csv, leaves, out_path,
                                        ns_index=req.ns_index,
                                        ns_prefix=req.ns_prefix,
                                        node_language=req.node_language)
                _STATE.last_extract_csv = str(out_path)
                _STATE.last_extract_count = len(rows)
            return {
                "ok": True,
                "count": len(rows),
                "out_path": str(out_path) if not req.preview_only else None,
                "rows": rows[:500],           # 前 500 行预览
                "truncated": len(rows) > 500,
            }
        finally:
            _STATE.busy = None


# -- POU 编辑 --------------------------------------------------------------
@app.get("/api/pou")
async def api_pou_get(path: str = Query(...)) -> Dict[str, Any]:
    tk = _require_tk()
    async with _STATE.mcp_lock:
        _STATE.busy = "reading_pou"
        try:
            raw = await asyncio.to_thread(tk.get_pou_code, path)
            decl, impl = _split_pou_output(raw)
            return {"ok": True, "path": path, "declaration": decl, "implementation": impl, "raw": raw}
        finally:
            _STATE.busy = None


def _split_pou_output(text: str) -> tuple[str, str]:
    if not text:
        return "", ""
    lo = text.lower()
    d = lo.find("declaration:")
    i = lo.find("implementation:")
    if d < 0 and i < 0:
        return text.strip(), ""
    decl_start = d + len("declaration:") if d >= 0 else 0
    decl_end = i if (i > d and i >= 0) else len(text)
    decl = text[decl_start:decl_end].strip()
    impl = text[i + len("implementation:"):].strip() if i >= 0 else ""
    return decl, impl


class PouSetReq(BaseModel):
    path: str
    declaration: Optional[str] = None
    implementation: Optional[str] = None
    save: bool = False
    compile: bool = False


@app.post("/api/pou")
async def api_pou_set(req: PouSetReq) -> Dict[str, Any]:
    tk = _require_tk()
    if req.declaration is None and req.implementation is None:
        raise HTTPException(400, "declaration 与 implementation 至少给一个")
    async with _STATE.mcp_lock:
        _STATE.busy = "writing_pou"
        result: Dict[str, Any] = {}
        try:
            out = await asyncio.to_thread(tk.set_pou_code, req.path,
                                          declaration=req.declaration,
                                          implementation=req.implementation)
            result["set"] = out.strip()
            if req.save:
                result["save"] = (await asyncio.to_thread(tk.save_project)).strip()
            if req.compile:
                cr = await asyncio.to_thread(tk.compile_project)
                result["compile"] = {"ok": cr.ok, "summary": cr.summary}
            return {"ok": True, **result}
        finally:
            _STATE.busy = None


# -- Server / Agent 子进程 -------------------------------------------------
def _find_python_exe() -> str:
    """选择 GUI 当前环境的真 Python，跳过 WindowsApps 存根。"""
    env_py = os.environ.get("PYTHON")
    if env_py and Path(env_py).exists():
        return env_py
    # Workbench 会用用户选定的 Conda 环境启动 GUI。子进程必须继承同一
    # 解释器，不能再落回另一套硬编码环境，否则依赖和本地源码会串线。
    if (
        sys.executable
        and "WindowsApps" not in sys.executable
        and Path(sys.executable).exists()
    ):
        return sys.executable
    for cand in (
        r"D:\miniforge3\envs\szlab-unilab\python.exe",
        r"D:\miniforge3\python.exe",
    ):
        if Path(cand).exists():
            return cand
    return "python"


def _python_subprocess_env() -> Dict[str, str]:
    """确保 Windows 子进程日志统一为 UTF-8，避免 GUI 中中文乱码。"""
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    return env


def _clear_server_metadata(*, remove_connection_state: bool = False) -> None:
    _STATE.server_client_url = None
    _STATE.server_csv_paths = []
    _STATE.server_node_defs = []
    _STATE.server_csv_id = None
    if remove_connection_state:
        with contextlib.suppress(OSError):
            connection_state_path().unlink(missing_ok=True)


def _server_client_host(host: str) -> str:
    normalized = host.strip()
    if normalized in {"", "0.0.0.0", "::", "[::]"}:
        return "127.0.0.1"
    return normalized


def _wait_for_opc_server(
    url: str,
    proc: subprocess.Popen,
    timeout: float = 5.0,
) -> None:
    """等 endpoint 真正可连接，避免子进程尚未 bind 就向前端报告成功。"""
    deadline = time.monotonic() + timeout
    last_error: Optional[Exception] = None
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"Server 进程已退出，退出码: {proc.returncode}")
        client = Client(url, timeout=0.8)
        try:
            client.connect()
            client.disconnect()
            return
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            with contextlib.suppress(Exception):
                client.disconnect()
            time.sleep(0.1)
    raise TimeoutError(f"等待 OPC UA endpoint 超时: {last_error}")


_PROCESS_STOP_LOCKS = {
    "server_proc": asyncio.Lock(),
    "agent_proc": asyncio.Lock(),
}


def _terminate_and_wait(proc: subprocess.Popen) -> Dict[str, Any]:
    """在线程中执行阻塞式进程回收，保证最终 wait，避免 Windows 残留句柄。"""
    forced = False
    try:
        proc.terminate()
        try:
            exit_code = proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            forced = True
            proc.kill()
            exit_code = proc.wait(timeout=2)
        return {
            "ok": True,
            "message": "已停止",
            "pid": proc.pid,
            "exit_code": exit_code,
            "forced": forced,
        }
    except Exception as exc:  # noqa: BLE001
        if proc.poll() is not None:
            return {
                "ok": True,
                "message": "已停止",
                "pid": proc.pid,
                "exit_code": proc.returncode,
                "forced": forced,
            }
        return {"ok": False, "message": str(exc), "pid": proc.pid, "forced": forced}


async def _stop_subprocess(field_name: str) -> Dict[str, Any]:
    """异步停止托管子进程，不阻塞 FastAPI 的状态接口和 SSE。"""
    lock = _PROCESS_STOP_LOCKS[field_name]
    async with lock:
        proc: Optional[subprocess.Popen] = getattr(_STATE, field_name)
        if proc is None or proc.poll() is not None:
            setattr(_STATE, field_name, None)
            if field_name == "server_proc":
                _clear_server_metadata(remove_connection_state=not _STATE.attached)
            return {"ok": True, "message": "已经停止或未运行"}

        log.info("终止子进程 %s pid=%d", field_name, proc.pid)
        _STATE.stopping.add(field_name)
        try:
            result = await asyncio.to_thread(_terminate_and_wait, proc)
            if result["ok"] and getattr(_STATE, field_name) is proc:
                setattr(_STATE, field_name, None)
                if field_name == "server_proc":
                    _clear_server_metadata(remove_connection_state=True)
            if result.get("forced"):
                log.warning("子进程 %s pid=%d 未及时退出，已强制终止", field_name, proc.pid)
            return result
        finally:
            _STATE.stopping.discard(field_name)


class CsvUploadReq(BaseModel):
    filename: str
    content_b64: str


# ponytail: 走 JSON + base64 而不是 multipart，省掉 python-multipart 依赖。
# 变量表是几百 KB 量级的文本，撑得住；真要传几十 MB 时再换 UploadFile。
_CSV_UPLOAD_MAX = 20 * 1024 * 1024


@app.post("/api/csv/upload")
async def api_csv_upload(req: CsvUploadReq) -> Dict[str, Any]:
    """上传变量表并落盘，返回服务器端路径供启动流程使用。

    远程部署时用户手上只有本地文件，填不出服务器路径。原样保存字节而不是
    先解码成文本，是为了让 load_csvs 照旧去嗅探编码 —— 中文 CSV 常见 GBK。
    """
    # Windows 传来的 filename 可能是整条 C:\... 路径，POSIX 不把反斜杠当分隔符
    name = Path(req.filename.replace("\\", "/")).name
    if not name.lower().endswith(".csv"):
        raise HTTPException(400, "只接受 .csv 文件")
    if name.startswith("."):
        raise HTTPException(400, "文件名不合法")
    try:
        raw = base64.b64decode(req.content_b64, validate=True)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"内容不是合法的 base64: {exc}") from exc
    if not raw:
        raise HTTPException(400, "文件是空的")
    if len(raw) > _CSV_UPLOAD_MAX:
        raise HTTPException(400, f"CSV 超过 {_CSV_UPLOAD_MAX // 1024 // 1024}MB")

    dest_dir = runtime_data_dir() / "uploads"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / name
    dest.write_bytes(raw)
    try:
        node_defs = await asyncio.to_thread(load_csvs, [dest])
    except Exception as exc:  # noqa: BLE001
        dest.unlink(missing_ok=True)
        raise HTTPException(400, f"CSV 解析失败: {exc}") from exc
    if not node_defs:
        dest.unlink(missing_ok=True)
        raise HTTPException(400, "CSV 中没有可用的 VARIABLE 节点")

    log.info("已上传变量表 %s（%d 个节点）", dest, len(node_defs))
    return {"ok": True, "path": str(dest), "count": len(node_defs)}


class ServerStartReq(BaseModel):
    csv: Optional[str] = None     # 不给则用上次提取结果或内置演示表
    host: str = "0.0.0.0"
    port: int = 4855
    ns_index: int = 4
    ns_uri: str = "urn:xuse:sim"
    occupancy_true: bool = True


@app.post("/api/server/start")
async def api_server_start(req: ServerStartReq) -> Dict[str, Any]:
    if _STATE.attached:
        raise HTTPException(400, "已挂接外部 Server，由进程管理器托管；"
                                 "如需由 GUI 启动请去掉 --attach-url")
    if _STATE.server_proc is not None and _STATE.server_proc.poll() is None:
        raise HTTPException(400, "Server 已在运行；请先停止")
    _STATE.server_proc = None
    _clear_server_metadata(remove_connection_state=True)
    csv_path = req.csv or _STATE.last_extract_csv or str(default_csv_path())
    if not Path(csv_path).exists():
        raise HTTPException(400, f"CSV 不存在: {csv_path}")
    resolved_csv = str(Path(csv_path).resolve())
    try:
        node_defs = await asyncio.to_thread(load_csvs, [Path(resolved_csv)])
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"CSV 解析失败: {exc}") from exc
    if not node_defs:
        raise HTTPException(400, "CSV 中没有可用的 VARIABLE 节点")

    cmd = runtime_command(
        "server",
        _ROOT / "server.py",
        [
            "--host", req.host, "--port", str(req.port),
            "--csv", resolved_csv,
            "--ns-index", str(req.ns_index), "--ns-uri", req.ns_uri,
            "--connection-state", str(connection_state_path()),
        ],
        python_executable=_find_python_exe(),
    )
    if not req.occupancy_true:
        cmd.append("--no-occupancy-true")

    log.info("启动 Server: %s", " ".join(cmd))
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            cwd=str(_ROOT), bufsize=0, env=_python_subprocess_env())
    _pipe_to_logger(proc, "server")
    _STATE.server_proc = proc
    client_host = _server_client_host(req.host)
    client_url = f"opc.tcp://{client_host}:{req.port}/xuse_sim/"
    try:
        await asyncio.to_thread(_wait_for_opc_server, client_url, proc)
    except Exception as exc:  # noqa: BLE001
        await asyncio.to_thread(_terminate_and_wait, proc)
        _STATE.server_proc = None
        _clear_server_metadata(remove_connection_state=True)
        raise HTTPException(500, f"Server 启动失败: {exc}") from exc
    _STATE.server_client_url = client_url
    _STATE.server_csv_paths = [resolved_csv]
    _STATE.server_node_defs = node_defs
    _STATE.server_csv_id = node_defs_fingerprint(node_defs)
    return {"ok": True, "pid": proc.pid}


@app.post("/api/server/stop")
async def api_server_stop() -> Dict[str, Any]:
    if _STATE.attached:
        raise HTTPException(400, "外部 Server 由进程管理器托管，请在 Supervisor 侧停止")
    return await _stop_subprocess("server_proc")


def _require_running_server() -> None:
    if not _STATE.server_client_url:
        raise HTTPException(409, "OPC UA Server 未运行")
    proc = _STATE.server_proc
    if not _STATE.attached and (proc is None or proc.poll() is not None):
        raise HTTPException(409, "OPC UA Server 未运行")


def _read_node_values(url: str, node_defs: List[NodeDef]) -> List[Any]:
    client = Client(url, timeout=4)
    try:
        client.connect()
        nodes = [client.get_node(item.node_id) for item in node_defs]
        return list(client.get_values(nodes))
    finally:
        with contextlib.suppress(Exception):
            client.disconnect()


def _coerce_node_value(data_type: str, raw: Any) -> Any:
    if data_type == "BOOLEAN":
        if isinstance(raw, bool):
            return raw
        normalized = str(raw).strip().lower()
        if normalized in {"true", "1", "yes", "on"}:
            return True
        if normalized in {"false", "0", "no", "off"}:
            return False
        raise ValueError("BOOLEAN 仅接受 true/false 或 1/0")
    if data_type == "INT16":
        value = int(raw)
        if not -32768 <= value <= 32767:
            raise ValueError("INT16 超出范围 -32768..32767")
        return value
    if data_type == "INT32":
        value = int(raw)
        if not -2147483648 <= value <= 2147483647:
            raise ValueError("INT32 超出范围")
        return value
    if data_type == "FLOAT":
        value = float(raw)
        if not math.isfinite(value):
            raise ValueError("FLOAT 必须是有限数值")
        return value
    if data_type == "STRING":
        return str(raw)
    raise ValueError(f"不支持的数据类型: {data_type}")


def _write_node_value(url: str, node_def: NodeDef, value: Any) -> Any:
    client = Client(url, timeout=4)
    try:
        client.connect()
        node = client.get_node(node_def.node_id)
        node.set_value(ua.Variant(value, VTYPE_MAP[node_def.data_type]))
        return node.get_value()
    finally:
        with contextlib.suppress(Exception):
            client.disconnect()


@app.get("/api/server/variables")
async def api_server_variables(
    query: str = Query("", max_length=200),
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
) -> Dict[str, Any]:
    """读取当前托管 OPC UA Server 的在线变量值。"""
    _require_running_server()
    needle = query.strip().casefold()
    node_defs = _STATE.server_node_defs
    if needle:
        node_defs = [
            item for item in node_defs
            if needle in item.name_cn.casefold()
            or needle in item.name_en.casefold()
            or needle in item.node_id.casefold()
            or needle in item.data_type.casefold()
        ]
    total = len(node_defs)
    page = node_defs[offset:offset + limit]
    if not page:
        return {"ok": True, "total": total, "offset": offset, "limit": limit, "items": []}

    assert _STATE.server_client_url is not None
    try:
        async with _STATE.server_io_lock:
            values = await asyncio.to_thread(
                _read_node_values, _STATE.server_client_url, page
            )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"读取 OPC UA 变量失败: {exc}") from exc

    items = [
        {
            "name": node_def.name_cn,
            "english_name": node_def.name_en,
            "data_type": node_def.data_type,
            "node_id": node_def.node_id,
            "value": value,
        }
        for node_def, value in zip(page, values)
    ]
    return {
        "ok": True,
        "total": total,
        "offset": offset,
        "limit": limit,
        "items": items,
    }


class ServerVariablesReadReq(BaseModel):
    node_ids: List[str]


@app.post("/api/server/variables/read")
async def api_server_variables_read(req: ServerVariablesReadReq) -> Dict[str, Any]:
    """批量读取监控栏中的变量，保持请求顺序并报告已失效的 NodeId。"""
    _require_running_server()
    node_ids = list(dict.fromkeys(item.strip() for item in req.node_ids if item.strip()))
    if len(node_ids) > 200:
        raise HTTPException(400, "监控栏一次最多读取 200 个变量")

    definitions = {item.node_id: item for item in _STATE.server_node_defs}
    selected = [definitions[node_id] for node_id in node_ids if node_id in definitions]
    missing = [node_id for node_id in node_ids if node_id not in definitions]
    if not selected:
        return {"ok": True, "items": [], "missing": missing}

    assert _STATE.server_client_url is not None
    try:
        async with _STATE.server_io_lock:
            values = await asyncio.to_thread(
                _read_node_values, _STATE.server_client_url, selected
            )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"读取监控变量失败: {exc}") from exc

    items = [
        {
            "name": node_def.name_cn,
            "english_name": node_def.name_en,
            "data_type": node_def.data_type,
            "node_id": node_def.node_id,
            "value": value,
        }
        for node_def, value in zip(selected, values)
    ]
    return {"ok": True, "items": items, "missing": missing}


class ServerVariableWriteReq(BaseModel):
    node_id: str
    value: Any


@app.post("/api/server/variable")
async def api_server_variable_write(req: ServerVariableWriteReq) -> Dict[str, Any]:
    """按 CSV 声明的数据类型写入一个在线变量并回读确认。"""
    _require_running_server()
    node_def = next(
        (item for item in _STATE.server_node_defs if item.node_id == req.node_id),
        None,
    )
    if node_def is None:
        raise HTTPException(404, "变量不在当前 Server 的 CSV 定义中")
    try:
        typed_value = _coerce_node_value(node_def.data_type, req.value)
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc

    assert _STATE.server_client_url is not None
    try:
        async with _STATE.server_io_lock:
            confirmed = await asyncio.to_thread(
                _write_node_value,
                _STATE.server_client_url,
                node_def,
                typed_value,
            )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"写入 OPC UA 变量失败: {exc}") from exc

    log.info("在线写变量 %s (%s) = %r", node_def.name_cn, node_def.node_id, confirmed)
    return {
        "ok": True,
        "node_id": node_def.node_id,
        "name": node_def.name_cn,
        "data_type": node_def.data_type,
        "value": confirmed,
    }


class AgentStartReq(BaseModel):
    host: str = "127.0.0.1"
    port: int = 4855
    config: Optional[str] = None      # 可选 yaml
    csv: Optional[str] = None         # 兼容旧 GUI 字段；SZLab 代理不读 CSV
    profile: str = "szlab"
    workflow: Optional[str] = None
    position: Optional[int] = Field(default=None, ge=1, le=6)
    pump: Optional[int] = Field(default=None, ge=1, le=3)
    delay_ms: Optional[int] = Field(default=None, ge=0, le=3_600_000)
    poll_ms: Optional[int] = Field(default=None, ge=5, le=60_000)
    s09_remaining_volume_ml: Optional[float] = Field(default=None, gt=0)
    s07_balance_reading: Optional[float] = None
    s09_balance_reading: Optional[float] = None


def _extend_szlab_command(cmd: List[str], req: AgentStartReq) -> Dict[str, Any]:
    """校验并附加 SZLab 工作流调试参数，返回实际生效的显式覆盖项。"""
    options: Dict[str, Any] = {}
    if req.workflow:
        if req.workflow not in ("all", *SZLAB_WORKFLOW_IDS, *SZLAB_WORKFLOW_ALIASES):
            raise HTTPException(400, f"未知 SZLab 工作流: {req.workflow}")
        options["workflow"] = req.workflow
        cmd.extend(["--workflow", req.workflow])

    option_specs = (
        ("position", "--position"),
        ("pump", "--pump"),
        ("delay_ms", "--delay-ms"),
        ("poll_ms", "--poll-ms"),
        ("s09_remaining_volume_ml", "--s09-remaining-volume-ml"),
        ("s07_balance_reading", "--s07-balance-reading"),
        ("s09_balance_reading", "--s09-balance-reading"),
    )
    for field_name, flag in option_specs:
        value = getattr(req, field_name)
        if value is not None:
            options[field_name] = value
            cmd.extend([flag, str(value)])
    return options


@app.post("/api/agent/start")
async def api_agent_start(req: AgentStartReq) -> Dict[str, Any]:
    if _STATE.attached:
        raise HTTPException(400, "已挂接外部 Agent，由进程管理器托管")
    if _STATE.agent_proc is not None and _STATE.agent_proc.poll() is None:
        raise HTTPException(400, "Handshake Agent 已在运行")
    url = f"opc.tcp://{req.host}:{req.port}/xuse_sim/"
    profile = (req.profile or "szlab").strip().lower()
    if profile != "szlab":
        raise HTTPException(400, "未知握手仿真类型，仅支持 szlab")

    cmd = runtime_command(
        "szlab-handshake",
        _ROOT / "szlab_handshake_agent.py",
        ["--url", url],
        python_executable=_find_python_exe(),
    )
    if req.config:
        cmd.extend(["--config", req.config])
    options = _extend_szlab_command(cmd, req)

    log.info("启动 Handshake Agent: %s", " ".join(cmd))
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            cwd=str(_ROOT), bufsize=0, env=_python_subprocess_env())
    _pipe_to_logger(proc, "agent")
    _STATE.agent_proc = proc
    await asyncio.sleep(0.3)
    return {
        "ok": True,
        "pid": proc.pid,
        "profile": profile,
        "options": options,
    }


@app.post("/api/agent/stop")
async def api_agent_stop() -> Dict[str, Any]:
    if _STATE.attached:
        raise HTTPException(400, "外部 Agent 由进程管理器托管，请在 Supervisor 侧停止")
    return await _stop_subprocess("agent_proc")


# -- SSE 日志流 ------------------------------------------------------------
@app.get("/api/logs/stream")
async def api_logs_stream(request: Request) -> StreamingResponse:
    q: asyncio.Queue = asyncio.Queue(maxsize=1000)
    _STATE.log_queues.add(q)

    async def _gen():
        # 首帧: 心跳 + 当前状态
        yield f"event: state\ndata: {json.dumps(_STATE.snapshot())}\n\n"
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    entry = await asyncio.wait_for(q.get(), timeout=15.0)
                    yield f"event: log\ndata: {json.dumps(entry, ensure_ascii=False)}\n\n"
                except asyncio.TimeoutError:
                    # 心跳，保持连接活着
                    yield ": ping\n\n"
                # 每次推日志后附带一次 state, 前端能低延迟看到 busy/pid 变化
                yield f"event: state\ndata: {json.dumps(_STATE.snapshot())}\n\n"
        finally:
            _STATE.log_queues.discard(q)

    return StreamingResponse(_gen(), media_type="text/event-stream")


# -- 一键流水线 -------------------------------------------------------------
class PipelineReq(BaseModel):
    include_all: bool = False
    ns_index: int = 4
    ns_prefix: str = "uniab|"
    node_language: str = "Chinese"
    expand_structs: bool = True
    host: str = "0.0.0.0"
    port: int = 4855
    also_start_agent: bool = False
    agent_config: Optional[str] = None


@app.post("/api/pipeline")
async def api_pipeline(req: PipelineReq) -> Dict[str, Any]:
    """extract → 启动 Server → (可选) 启动 Agent。"""
    # 1) extract (走已有 handler 的逻辑)
    ex_req = ExtractReq(include_all=req.include_all,
                        ns_index=req.ns_index, ns_prefix=req.ns_prefix,
                        node_language=req.node_language,
                        expand_structs=req.expand_structs,
                        preview_only=False)
    ex_result = await api_project_extract(ex_req)
    csv_path = ex_result["out_path"]
    # 2) server
    await _stop_subprocess("server_proc")
    sv_req = ServerStartReq(csv=csv_path, host=req.host, port=req.port,
                            ns_index=req.ns_index)
    await api_server_start(sv_req)
    # 3) agent
    if req.also_start_agent:
        await _stop_subprocess("agent_proc")
        ag_req = AgentStartReq(host="127.0.0.1", port=req.port,
                               config=req.agent_config, csv=csv_path)
        await api_agent_start(ag_req)
    return {"ok": True, "csv": csv_path, "state": _STATE.snapshot()}


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------
def main() -> int:
    import argparse
    import uvicorn
    ap = argparse.ArgumentParser(description="OpcUaSim Web GUI 后端")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=18765)
    ap.add_argument("--no-open", action="store_true", help="启动后不自动打开浏览器")
    ap.add_argument("--attach-url", default=None,
                    help="挂接已由 Supervisor/systemd 托管的 Server，"
                         "例如 opc.tcp://127.0.0.1:4855/xuse_sim/")
    ap.add_argument("--attach-csv", default=None,
                    help="挂接时使用的变量表，须与外部 Server 加载的是同一份")
    args = ap.parse_args()

    # 转成环境变量, 让 `uvicorn gui.backend:app` 这类直接拉 app 的入口同样生效
    if args.attach_url:
        os.environ["OPCUASIM_ATTACH_URL"] = args.attach_url
    if args.attach_csv:
        os.environ["OPCUASIM_ATTACH_CSV"] = args.attach_csv

    if not args.no_open:
        import webbrowser
        threading.Timer(1.2, lambda: webbrowser.open(f"http://{args.host}:{args.port}/")).start()

    # PyInstaller ``--windowed`` applications may expose no stdout/stderr.
    # Disable Uvicorn's terminal color probe, which otherwise calls
    # ``sys.stdout.isatty()`` and crashes before the GUI server starts.
    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        log_level="warning",
        use_colors=False,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
