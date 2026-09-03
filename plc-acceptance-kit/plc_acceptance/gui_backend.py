"""提供无需开发环境的 SZLab PLC 自动验收 Web GUI。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import threading
import urllib.error
import urllib.request
import uuid
import webbrowser
import zipfile
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .config import load_bundle
from .resources import default_kit_root, runtime_data_dir
from .run_manager import AcceptanceRunManager
from .validator import validate_bundle

STATIC_DIR = Path(__file__).resolve().parent / "static"
ARTIFACT_DIR = runtime_data_dir() / "artifacts"
RUN_MANAGER = AcceptanceRunManager()
app = FastAPI(title="SZLab PLC 自动验收", version="0.3.0")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


class RunRequest(BaseModel):
    """描述 GUI 发起的一次验收运行。"""

    mode: Literal["simulator", "soft_plc", "bench", "fat_sat"]
    endpoint: str | None = Field(default=None, max_length=512)
    namespace_uri: str | None = Field(default=None, max_length=256)
    confirm_safe_test_mode: bool = False
    artifact_id: str | None = Field(default=None, max_length=200)
    supervisor: str | None = Field(default=None, max_length=120)
    test_location: str | None = Field(default=None, max_length=200)
    material_reference: str | None = Field(default=None, max_length=200)


EXTERNAL_MODES = {"soft_plc", "bench", "fat_sat"}
ON_SITE_MODES = {"bench", "fat_sat"}


def _package_version(distribution: str, fallback: str) -> str:
    """读取安装分发版本并为源码运行提供回退。

    参数：``distribution`` 是分发包名，``fallback`` 是源码回退版本。
    返回：可展示的版本文本。
    """

    try:
        return version(distribution)
    except PackageNotFoundError:
        return fallback


def _safe_artifact_name(filename: str | None) -> str:
    """把上传文件名约束为可移植的单层名称。

    参数：``filename`` 是浏览器上传的原始名称。
    返回：仅包含安全字符且保留常见扩展名的文件名。
    """

    raw = Path(filename or "plc-candidate.bin").name
    safe = re.sub(r"[^0-9A-Za-z._-]+", "-", raw).strip(".-")
    return safe[:120] or "plc-candidate.bin"


def _artifact_path(artifact_id: str | None) -> Path | None:
    """解析 GUI 已上传的候选包身份。

    参数：``artifact_id`` 是上传接口返回的单层文件名。
    返回：合法文件路径；未提供时返回 ``None``。
    异常：路径越界或文件不存在时抛出 ``HTTPException``。
    """

    if not artifact_id:
        return None
    candidate = (ARTIFACT_DIR / artifact_id).resolve()
    if candidate.parent != ARTIFACT_DIR.resolve() or not candidate.is_file():
        raise HTTPException(status_code=400, detail="PLC 候选包不存在，请重新选择文件")
    return candidate


def _valid_opcua_endpoint(value: str | None) -> bool:
    """判断输入是否是带主机和端口的 OPC UA TCP Endpoint。"""

    if not value or any(ord(character) < 32 for character in value):
        return False
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return False
    return bool(
        parsed.scheme == "opc.tcp"
        and parsed.hostname
        and port
        and not parsed.username
        and not parsed.password
        and not parsed.query
        and not parsed.fragment
    )


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    """返回自动验收单页界面。

    参数：无。
    返回：GUI 首页 HTML。
    """

    return (STATIC_DIR / "index.html").read_text(encoding="utf-8")


@app.get("/api/health")
def health() -> dict[str, Any]:
    """返回安装包 GUI 的健康状态。

    参数：无。
    返回：健康标志和验收包版本。
    """

    return {
        "ok": True,
        "version": _package_version("unilab-plc-acceptance", "0.3.0+source"),
    }


@app.get("/api/bootstrap")
def bootstrap() -> dict[str, Any]:
    """返回首页所需的协议、环境和历史证据。

    参数：无。
    返回：点表状态、必跑用例、阻塞覆盖项和最近报告。
    """

    kit_root = default_kit_root()
    simulator = load_bundle(kit_root)
    soft_plc = load_bundle(kit_root, environment_name="soft-plc")
    bench = load_bundle(kit_root, environment_name="bench")
    fat_sat = load_bundle(kit_root, environment_name="fat-sat")
    findings = validate_bundle(simulator)
    return {
        "product": "SZLab PLC 自动验收",
        "version": _package_version("unilab-plc-acceptance", "0.3.0+source"),
        "plc_sim_version": _package_version("unilab-plc-sim", "source"),
        "project_id": simulator.project_id,
        "protocol_version": simulator.protocol_version,
        "node_count": simulator.expected_scalar_nodes,
        "l0_status": (
            "FAILED" if any(item.severity == "error" for item in findings) else "PASSED"
        ),
        "cases": [
            {
                "id": entry.case_id,
                "safety_level": entry.safety_level,
                "required": entry.required,
            }
            for entry in simulator.manifest
        ],
        "coverage_gaps": [
            item
            for item in simulator.coverage
            if item.get("status") in {"blocked", "manual", "planned", "partial"}
        ],
        "soft_plc_endpoint": soft_plc.environment.endpoint,
        "environment_endpoints": {
            "soft_plc": soft_plc.environment.endpoint,
            "bench": bench.environment.endpoint,
            "fat_sat": fat_sat.environment.endpoint,
        },
        "namespace_uri": simulator.namespace_uri,
        "data_dir": str(runtime_data_dir()),
        "history": RUN_MANAGER.history(),
    }


@app.post("/api/artifacts")
async def upload_artifact(request: Request, filename: str = "") -> dict[str, Any]:
    """保存供应商候选包并返回内容身份。

    参数：``request`` 提供流式文件体，``filename`` 是原始文件名。
    返回：后续运行使用的 artifact ID、原始名称、大小和 SHA-256。
    """

    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = _safe_artifact_name(filename)
    temporary = ARTIFACT_DIR / (
        f"upload-{os.getpid()}-{threading.get_ident()}-{uuid.uuid4().hex}.tmp"
    )
    digest = hashlib.sha256()
    size = 0
    try:
        with temporary.open("wb") as output:
            async for chunk in request.stream():
                if not chunk:
                    continue
                size += len(chunk)
                if size > 1024 * 1024 * 1024:
                    raise HTTPException(
                        status_code=413,
                        detail="PLC 候选包不能超过 1 GiB",
                    )
                digest.update(chunk)
                output.write(chunk)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    if size == 0:
        temporary.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="PLC 候选包不能为空")
    sha256 = digest.hexdigest()
    artifact_id = f"{sha256[:16]}-{safe_name}"
    destination = ARTIFACT_DIR / artifact_id
    os.replace(temporary, destination)
    return {
        "artifact_id": artifact_id,
        "name": safe_name,
        "size": size,
        "sha256": sha256,
    }


@app.post("/api/run", status_code=202)
def start_run(request: RunRequest) -> dict[str, Any]:
    """校验输入并启动一次后台验收。

    参数：``request`` 是环境、Endpoint、安全确认和候选包身份。
    返回：进入 ``RUNNING`` 的任务快照。
    """

    if request.mode in EXTERNAL_MODES:
        if not _valid_opcua_endpoint(request.endpoint):
            raise HTTPException(
                status_code=400, detail="请输入有效的 opc.tcp:// Endpoint"
            )
        if not (request.namespace_uri or "").strip():
            raise HTTPException(status_code=400, detail="请输入 OPC UA Namespace URI")
    if request.mode in ON_SITE_MODES:
        if not (request.supervisor or "").strip():
            raise HTTPException(status_code=400, detail="请输入现场监护/见证人")
        if not (request.test_location or "").strip():
            raise HTTPException(status_code=400, detail="请输入台架/现场位置")
    if request.mode == "fat_sat" and not (request.material_reference or "").strip():
        raise HTTPException(status_code=400, detail="请输入物料或批次标识")
    artifact = _artifact_path(request.artifact_id)
    if request.mode in EXTERNAL_MODES and artifact is None:
        raise HTTPException(status_code=400, detail="请选择不可变 PLC 候选包")
    if request.mode in EXTERNAL_MODES and not request.confirm_safe_test_mode:
        mode_name = {
            "soft_plc": "软 PLC",
            "bench": "L3 真机台架",
            "fat_sat": "L4 FAT/SAT 现场",
        }[request.mode]
        raise HTTPException(
            status_code=400,
            detail=f"请先确认{mode_name}已进入受控测试模式且安全前置成立",
        )
    evidence_metadata = {
        key: value.strip()
        for key, value in {
            "supervisor": request.supervisor or "",
            "test_location": request.test_location or "",
            "material_reference": request.material_reference or "",
        }.items()
        if value.strip()
    }
    try:
        return RUN_MANAGER.start(
            mode=request.mode,
            endpoint=request.endpoint,
            namespace_uri=(request.namespace_uri or "").strip() or None,
            confirm_safe_test_mode=request.confirm_safe_test_mode,
            plc_artifact=artifact,
            evidence_metadata=evidence_metadata,
        )
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/api/run")
def run_status() -> dict[str, Any]:
    """返回当前或最近一次验收状态。

    参数：无。
    返回：可轮询的运行快照。
    """

    return RUN_MANAGER.snapshot()


@app.get("/api/history")
def history() -> dict[str, Any]:
    """返回最近二十次持久报告。

    参数：无。
    返回：报告摘要列表。
    """

    return {"reports": RUN_MANAGER.history()}


@app.get("/api/reports/{run_id}/report")
def report_html(run_id: str) -> FileResponse:
    """在浏览器中打开一次验收的 HTML 报告。

    参数：``run_id`` 是稳定运行 ID。
    返回：报告 HTML 文件响应。
    """

    try:
        report_dir = RUN_MANAGER.report_dir(run_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="报告不存在") from exc
    return FileResponse(report_dir / "report.html", media_type="text/html")


@app.get("/api/reports/{run_id}/download")
def report_download(run_id: str) -> FileResponse:
    """下载包含报告和时间线的完整 ZIP 证据包。

    参数：``run_id`` 是稳定运行 ID。
    返回：按需生成的 ZIP 文件响应。
    """

    try:
        report_dir = RUN_MANAGER.report_dir(run_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="报告不存在") from exc
    archive_root = runtime_data_dir() / "exports"
    archive_root.mkdir(parents=True, exist_ok=True)
    archive = archive_root / f"{run_id}.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as output:
        for path in sorted(report_dir.iterdir()):
            if path.is_file():
                output.write(path, arcname=f"{run_id}/{path.name}")
    return FileResponse(
        archive,
        media_type="application/zip",
        filename=f"{run_id}-evidence.zip",
    )


def _running_gui(url: str) -> bool:
    """检查固定端口是否已有同一 GUI 实例。

    参数：``url`` 是本地 GUI 根地址。
    返回：健康接口可访问且标记正常时为 ``True``。
    """

    try:
        with urllib.request.urlopen(f"{url}/api/health", timeout=1) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return payload.get("ok") is True
    except (OSError, ValueError, urllib.error.URLError):
        return False


def main() -> int:
    """启动本地自动验收 GUI，并在安装后自动打开浏览器。

    参数：无；从命令行读取监听地址、端口和浏览器开关。
    返回：正常退出返回 ``0``。
    """

    parser = argparse.ArgumentParser(description="SZLab PLC 自动验收 GUI")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18816)
    parser.add_argument("--no-open", action="store_true", help="启动后不自动打开浏览器")
    arguments = parser.parse_args()
    url = f"http://{arguments.host}:{arguments.port}"
    if _running_gui(url):
        if not arguments.no_open:
            webbrowser.open(url)
        return 0
    if not arguments.no_open:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
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
