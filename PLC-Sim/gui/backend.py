# ruff: noqa: F401
"""PLC-Sim Web GUI application composition and diagnostics.

Feature behavior lives in the project, Server, and Agent route modules.  This module
keeps the stable ``gui.backend:app`` and ``gui.backend:main`` interfaces used by
Uvicorn, the installed CLI, and frozen installers.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import sys
import threading
import time
from contextlib import asynccontextmanager
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .agent_routes import (
    SZLAB_WORKFLOW_ALIASES,
    SZLAB_WORKFLOW_IDS,
    AgentStartReq,
    PtlcFaultReq,
    PtlcWorldReq,
    _extend_ptlc_command,
    _extend_szlab_command,
    api_agent_start,
    api_agent_stop,
    api_ptlc_agent_fault,
    api_ptlc_agent_state,
    api_ptlc_agent_world,
    api_szlab_agent_state,
)
from .agent_routes import (
    router as agent_router,
)
from .backend_state import (
    STATE,
    AppState,
    empty_connection_state,
    read_json_file,
    read_server_connection_state,
    write_json_file,
)
from .processes import (
    ROOT,
    find_python_exe,
    pipe_to_logger,
    python_subprocess_env,
    stop_subprocess,
    terminate_and_wait,
)
from .project_routes import (
    DownloadReq,
    ExtractReq,
    OpenReq,
    PouSetReq,
    SymbolSetReq,
    VersionRestoreReq,
    _online_deploy_allowed,
    _require_tk,
    _split_pou_output,
    api_pou_get,
    api_pou_set,
    api_project_cache,
    api_project_close,
    api_project_compile,
    api_project_deploy_preflight,
    api_project_download,
    api_project_editables,
    api_project_extract,
    api_project_gvls,
    api_project_open,
    api_project_save,
    api_project_structure,
    api_project_symbol_set,
    api_project_symbols,
    api_project_version_download,
    api_project_version_restore,
    api_project_versions,
    api_project_warm,
    api_project_warm_raw,
)
from .project_routes import (
    router as project_router,
)
from .server_routes import (
    CsvUploadReq,
    ServerStartReq,
    ServerVariablesReadReq,
    ServerVariableWriteReq,
    _coerce_node_value,
    _read_node_values,
    _replace_array_element,
    _require_running_server,
    _server_client_host,
    _wait_for_opc_server,
    _write_node_element,
    _write_node_value,
    api_csv_upload,
    api_server_start,
    api_server_stop,
    api_server_variable_write,
    api_server_variables,
    api_server_variables_read,
    attach_external_server,
)
from .server_routes import (
    router as server_router,
)

# Backward-compatible aliases for callers that imported helpers from gui.backend.
_ROOT = ROOT
_STATE = STATE
_empty_connection_state = empty_connection_state
_read_json_file = read_json_file
_read_server_connection_state = read_server_connection_state
_write_json_file = write_json_file
_find_python_exe = find_python_exe
_pipe_to_logger = pipe_to_logger
_python_subprocess_env = python_subprocess_env
_stop_subprocess = stop_subprocess
_terminate_and_wait = terminate_and_wait

log = logging.getLogger("gui")
_STATIC_DIR = Path(__file__).resolve().parent / "static"
_BACKEND_START_TS = time.time()
_BACKEND_CAPABILITIES = {
    "szlab_package_runtime": True,
    "ptlc_server_profile": True,
    "ptlc_handshake_agent": True,
    "ptlc_write_ownership": True,
    "ptlc_behavior_contract": True,
    "project_version_history": True,
    "safe_online_deploy": False,
}
_STATIC_REFERENCE = re.compile(
    r'(?P<attribute>href|src)="(?P<url>/static/(?P<name>[^"?]+))"'
)


class _SseLogHandler(logging.Handler):
    """Broadcast log records to connected SSE queues from any thread."""

    def emit(self, record: logging.LogRecord) -> None:
        entry = {
            "ts": record.created,
            "level": record.levelname,
            "source": record.name,
            "msg": record.getMessage(),
        }
        loop = STATE.loop
        if loop is None or not loop.is_running():
            return
        for target in list(STATE.log_queues):
            try:
                loop.call_soon_threadsafe(_safe_put, target, entry)
            except RuntimeError:
                continue
            except Exception:
                log.debug("无法向 SSE 队列投递日志", exc_info=True)


def _safe_put(target: asyncio.Queue, entry: dict[str, Any]) -> None:
    """Append a log entry while dropping the oldest item on backpressure."""

    try:
        target.put_nowait(entry)
    except asyncio.QueueFull:
        try:
            target.get_nowait()
            target.put_nowait(entry)
        except (asyncio.QueueEmpty, asyncio.QueueFull):
            return


def _install_root_logger() -> None:
    """Install console and SSE handlers once per process."""

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    if not any(
        isinstance(handler, logging.StreamHandler)
        and not isinstance(handler, _SseLogHandler)
        for handler in root_logger.handlers
    ):
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(
            logging.Formatter(
                "%(asctime)s.%(msecs)03d [%(levelname)s] %(name)s: %(message)s",
                datefmt="%H:%M:%S",
            )
        )
        root_logger.addHandler(stream_handler)
    if not any(isinstance(handler, _SseLogHandler) for handler in root_logger.handlers):
        root_logger.addHandler(_SseLogHandler())
    logging.getLogger("opcua").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("watchfiles").setLevel(logging.WARNING)


@asynccontextmanager
async def _lifespan(_application: FastAPI):
    _install_root_logger()
    attach_external_server()
    STATE.loop = asyncio.get_running_loop()
    log.info("PLC-Sim GUI started, static=%s", _STATIC_DIR)
    try:
        yield
    finally:
        log.info("shutting down…")
        await stop_subprocess("server_proc")
        await stop_subprocess("agent_proc")
        if STATE.mcp is not None:
            try:
                STATE.mcp.close()
            except Exception:
                log.warning("关闭 MCP 会话失败", exc_info=True)


app = FastAPI(title="PLC-Sim Control Panel", version="1.0.0", lifespan=_lifespan)
app.include_router(project_router)
app.include_router(server_router)
app.include_router(agent_router)
app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")


@app.middleware("http")
async def _no_cache_for_static(request: Request, call_next):
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = (
            "no-store, no-cache, must-revalidate, max-age=0"
        )
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


def _bust(filename: str) -> str:
    """Return a stable cache-busting token for one static asset."""

    try:
        return str(int((_STATIC_DIR / filename).stat().st_mtime))
    except FileNotFoundError:
        return "0"


def _version_static_references(html: str) -> str:
    """Append each referenced static asset's own modification timestamp."""

    def replace(match: re.Match[str]) -> str:
        attribute = match.group("attribute")
        url = match.group("url")
        name = match.group("name")
        return f'{attribute}="{url}?v={_bust(name)}"'

    return _STATIC_REFERENCE.sub(replace, html)


@app.get("/")
async def _root() -> HTMLResponse:
    html = (_STATIC_DIR / "index.html").read_text(encoding="utf-8")
    return HTMLResponse(_version_static_references(html))


@app.get("/api/state")
async def api_state() -> dict[str, Any]:
    return STATE.snapshot()


@app.get("/api/health")
async def api_health() -> dict[str, Any]:
    return {"ok": True, "ts": time.time()}


def _read_release() -> str:
    """Read the CI release marker or installed package version."""

    try:
        return (ROOT / "VERSION").read_text(encoding="utf-8").strip() or "dev"
    except OSError:
        try:
            return version("unilab-plc-sim")
        except PackageNotFoundError:
            return "dev"


_RELEASE = _read_release()


@app.get("/api/version")
async def api_version() -> dict[str, Any]:
    """Report backend identity, capabilities, and every static asset version."""

    static_mtime = {
        path.name: path.stat().st_mtime
        for path in sorted(_STATIC_DIR.iterdir())
        if path.is_file()
    }
    return {
        "release": _RELEASE,
        "backend_started": _BACKEND_START_TS,
        "backend_pid": os.getpid(),
        "capabilities": _BACKEND_CAPABILITIES,
        "static_mtime": static_mtime,
        "has_endpoints": {
            "/api/project/editables": True,
            "/api/project/warm": True,
            "/api/project/cache": True,
            "/api/project/versions": True,
            "/api/project/symbols": True,
            "/api/project/deploy/preflight": True,
        },
    }


@app.get("/api/logs/stream")
async def api_logs_stream(request: Request) -> StreamingResponse:
    """Stream logs and fresh application snapshots over SSE."""

    target: asyncio.Queue = asyncio.Queue(maxsize=1000)
    STATE.log_queues.add(target)

    async def generate():
        yield f"event: state\ndata: {json.dumps(STATE.snapshot())}\n\n"
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    entry = await asyncio.wait_for(target.get(), timeout=15.0)
                    yield (
                        f"event: log\ndata: {json.dumps(entry, ensure_ascii=False)}\n\n"
                    )
                except TimeoutError:
                    yield ": ping\n\n"
                yield f"event: state\ndata: {json.dumps(STATE.snapshot())}\n\n"
        finally:
            STATE.log_queues.discard(target)

    return StreamingResponse(generate(), media_type="text/event-stream")


class PipelineReq(ExtractReq):
    """One-click extract, Server start, and optional Agent start request."""

    host: str = "0.0.0.0"
    port: int = 4855
    also_start_agent: bool = False
    agent_config: str | None = None


@app.post("/api/pipeline")
async def api_pipeline(request: PipelineReq) -> dict[str, Any]:
    """Extract variables, start the Server, and optionally start the Agent."""

    extract_request = ExtractReq(
        include_all=request.include_all,
        ns_index=request.ns_index,
        ns_prefix=request.ns_prefix,
        node_language=request.node_language,
        expand_structs=request.expand_structs,
        preview_only=False,
    )
    extract_result = await api_project_extract(extract_request)
    csv_path = extract_result["out_path"]
    await stop_subprocess("server_proc")
    await api_server_start(
        ServerStartReq(
            csv=csv_path,
            host=request.host,
            port=request.port,
            ns_index=request.ns_index,
        )
    )
    if request.also_start_agent:
        await stop_subprocess("agent_proc")
        await api_agent_start(
            AgentStartReq(
                host="127.0.0.1",
                port=request.port,
                config=request.agent_config,
                csv=csv_path,
            )
        )
    return {"ok": True, "csv": csv_path, "state": STATE.snapshot()}


def main() -> int:
    """Run the GUI with options shared by source, wheel, and frozen builds."""

    parser = argparse.ArgumentParser(description="PLC-Sim Web GUI 后端")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18765)
    parser.add_argument("--no-open", action="store_true", help="启动后不自动打开浏览器")
    parser.add_argument(
        "--attach-url",
        default=None,
        help=(
            "挂接已由 Supervisor/systemd 托管的 Server，"
            "例如 opc.tcp://127.0.0.1:4855/xuse_sim/"
        ),
    )
    parser.add_argument(
        "--attach-csv",
        default=None,
        help="挂接时使用的变量表，须与外部 Server 加载的是同一份",
    )
    arguments = parser.parse_args()

    if arguments.attach_url:
        os.environ["PLCSIM_ATTACH_URL"] = arguments.attach_url
    if arguments.attach_csv:
        os.environ["PLCSIM_ATTACH_CSV"] = arguments.attach_csv

    if not arguments.no_open:
        import webbrowser

        threading.Timer(
            1.2,
            lambda: webbrowser.open(f"http://{arguments.host}:{arguments.port}/"),
        ).start()

    uvicorn.run(
        app,
        host=arguments.host,
        port=arguments.port,
        log_level="warning",
        use_colors=False,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
