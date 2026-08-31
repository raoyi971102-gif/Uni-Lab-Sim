"""OPC UA Server lifecycle and online-variable routes."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import logging
import math
import os
import subprocess
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from opcua import Client, ua
from pydantic import BaseModel, Field

try:
    from ..cli import runtime_command
    from ..common import (
        VTYPE_MAP,
        NodeDef,
        connection_state_path,
        default_csv_path,
        load_csvs,
        load_ptlc_nodes,
        node_defs_fingerprint,
        runtime_data_dir,
    )
except ImportError:  # Source checkout: ``import gui.backend``.
    from cli import runtime_command
    from common import (
        VTYPE_MAP,
        NodeDef,
        connection_state_path,
        default_csv_path,
        load_csvs,
        load_ptlc_nodes,
        node_defs_fingerprint,
        runtime_data_dir,
    )

from .backend_state import STATE
from .processes import (
    ROOT,
    clear_server_metadata,
    find_python_exe,
    pipe_to_logger,
    python_subprocess_env,
    stop_subprocess,
    terminate_and_wait,
)

router = APIRouter()
log = logging.getLogger("gui.server")


def attach_external_server() -> None:
    """Attach a Supervisor/systemd-managed Server to the GUI state."""

    url = os.environ.get("PLCSIM_ATTACH_URL")
    if not url:
        return
    csv_path = Path(
        os.environ.get("PLCSIM_ATTACH_CSV")
        or os.environ.get("PLCSIM_CSV")
        or default_csv_path()
    )
    try:
        node_defs = load_csvs([csv_path])
    except Exception as exc:  # noqa: BLE001
        log.error("挂接外部 Server 失败，CSV 无法解析 (%s): %s", csv_path, exc)
        return
    if not node_defs:
        log.error("挂接外部 Server 失败，CSV 中没有 VARIABLE 节点: %s", csv_path)
        return
    STATE.attached = True
    STATE.server_client_url = url
    STATE.server_csv_paths = [str(csv_path.resolve())]
    STATE.server_node_defs = node_defs
    STATE.server_csv_id = node_defs_fingerprint(node_defs)
    log.info("已挂接外部 Server: %s (%d 个变量, CSV=%s)", url, len(node_defs), csv_path)


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
    """Wait until the endpoint is connectable before publishing it as running."""

    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
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


class CsvUploadReq(BaseModel):
    filename: str
    content_b64: str


# ponytail: 走 JSON + base64 而不是 multipart，省掉 python-multipart 依赖。
# 变量表是几百 KB 量级的文本，撑得住；真要传几十 MB 时再换 UploadFile。
_CSV_UPLOAD_MAX = 20 * 1024 * 1024


@router.post("/api/csv/upload")
async def api_csv_upload(req: CsvUploadReq) -> dict[str, Any]:
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
    except Exception as exc:
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
    except Exception as exc:
        dest.unlink(missing_ok=True)
        raise HTTPException(400, f"CSV 解析失败: {exc}") from exc
    if not node_defs:
        dest.unlink(missing_ok=True)
        raise HTTPException(400, "CSV 中没有可用的 VARIABLE 节点")

    log.info("已上传变量表 %s（%d 个节点）", dest, len(node_defs))
    return {"ok": True, "path": str(dest), "count": len(node_defs)}


class ServerStartReq(BaseModel):
    csv: str | None = None  # 不给则用上次提取结果或内置演示表
    csvs: list[str] | None = Field(default=None, max_length=50)
    profile: str = "csv"
    host: str = "0.0.0.0"
    port: int = 4855
    ns_index: int = 4
    ns_uri: str = "urn:xuse:sim"
    occupancy_true: bool = True


def _resolve_server_node_paths(req: ServerStartReq, profile: str) -> list[Path]:
    """解析并去重节点表路径；CSV profile 可合并多份，PTLC 只允许一份。"""

    default_path = (
        ROOT / "config" / "ptlc_nodes.yaml"
        if profile == "ptlc"
        else Path(STATE.last_extract_csv or default_csv_path())
    )
    if req.csvs is not None:
        requested = [str(item).strip() for item in req.csvs]
        if not requested or any(not item for item in requested):
            raise HTTPException(400, "节点表列表不能包含空路径")
    elif req.csv is not None:
        requested = [req.csv.strip()]
        if not requested[0]:
            raise HTTPException(400, "节点表路径不能为空")
    else:
        requested = [str(default_path)]

    resolved: list[Path] = []
    seen: set[str] = set()
    for item in requested:
        path = Path(item).resolve()
        if not path.is_file():
            raise HTTPException(400, f"节点表不存在: {item}")
        key = os.path.normcase(str(path))
        if key in seen:
            continue
        seen.add(key)
        resolved.append(path)

    if profile == "ptlc" and len(resolved) != 1:
        raise HTTPException(400, "PTLC profile 只接受一份节点 YAML")
    return resolved


@router.post("/api/server/start")
async def api_server_start(req: ServerStartReq) -> dict[str, Any]:
    if STATE.attached:
        raise HTTPException(
            400,
            "已挂接外部 Server，由进程管理器托管；如需由 GUI 启动请去掉 --attach-url",
        )
    if STATE.server_proc is not None and STATE.server_proc.poll() is None:
        raise HTTPException(400, "Server 已在运行；请先停止")
    STATE.server_proc = None
    clear_server_metadata(remove_connection_state=True)
    profile = (req.profile or "csv").strip().lower()
    if profile not in {"csv", "ptlc"}:
        raise HTTPException(400, f"未知 Server profile: {profile}")
    node_paths = _resolve_server_node_paths(req, profile)
    try:
        if profile == "ptlc":
            node_defs = await asyncio.to_thread(
                load_ptlc_nodes, node_paths[0], req.ns_index
            )
        else:
            node_defs = await asyncio.to_thread(load_csvs, node_paths)
    except Exception as exc:
        raise HTTPException(400, f"节点表解析失败: {exc}") from exc
    if not node_defs:
        raise HTTPException(400, f"{profile} 节点表中没有可用的 VARIABLE 节点")

    server_args = [
        "--host",
        req.host,
        "--port",
        str(req.port),
        "--profile",
        profile,
    ]
    for path in node_paths:
        server_args.extend(["--csv", str(path)])
    server_args.extend(
        [
            "--ns-index",
            str(req.ns_index),
            "--ns-uri",
            req.ns_uri,
            "--connection-state",
            str(connection_state_path()),
        ]
    )
    cmd = runtime_command(
        "server",
        ROOT / "server.py",
        server_args,
        python_executable=find_python_exe(),
    )
    if not req.occupancy_true:
        cmd.append("--no-occupancy-true")

    log.info("启动 Server: %s", " ".join(cmd))
    proc = await asyncio.to_thread(
        subprocess.Popen,
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(ROOT),
        bufsize=0,
        env=python_subprocess_env(),
    )
    pipe_to_logger(proc, "server")
    STATE.server_proc = proc
    client_host = _server_client_host(req.host)
    client_url = f"opc.tcp://{client_host}:{req.port}/xuse_sim/"
    try:
        await asyncio.to_thread(_wait_for_opc_server, client_url, proc)
    except Exception as exc:
        await asyncio.to_thread(terminate_and_wait, proc)
        STATE.server_proc = None
        clear_server_metadata(remove_connection_state=True)
        raise HTTPException(500, f"Server 启动失败: {exc}") from exc
    STATE.server_client_url = client_url
    STATE.server_csv_paths = [str(path) for path in node_paths]
    STATE.server_node_defs = node_defs
    STATE.server_csv_id = node_defs_fingerprint(node_defs)
    return {
        "ok": True,
        "pid": proc.pid,
        "csvs": list(STATE.server_csv_paths),
        "count": len(node_defs),
    }


@router.post("/api/server/stop")
async def api_server_stop() -> dict[str, Any]:
    if STATE.attached:
        raise HTTPException(400, "外部 Server 由进程管理器托管，请在 Supervisor 侧停止")
    return await stop_subprocess("server_proc")


def _require_running_server() -> None:
    if not STATE.server_client_url:
        raise HTTPException(409, "OPC UA Server 未运行")
    proc = STATE.server_proc
    if not STATE.attached and (proc is None or proc.poll() is not None):
        raise HTTPException(409, "OPC UA Server 未运行")


def _read_node_values(url: str, node_defs: list[NodeDef]) -> list[Any]:
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
    if data_type == "BYTE":
        value = int(raw)
        if not 0 <= value <= 255:
            raise ValueError("BYTE 超出范围 0..255")
        return value
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
    if data_type in {"FLOAT", "DOUBLE"}:
        value = float(raw)
        if not math.isfinite(value):
            raise ValueError(f"{data_type} 必须是有限数值")
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


def _replace_array_element(
    node_def: NodeDef,
    current: Any,
    index: int,
    raw_value: Any,
) -> list[Any]:
    """校验并生成单元素更新后的完整数组；不改变调用方传入的当前值。"""
    if node_def.array_len <= 0:
        raise ValueError(f"{node_def.name_cn} 不是数组节点")
    if not isinstance(current, (list, tuple)) or len(current) != node_def.array_len:
        actual = len(current) if isinstance(current, (list, tuple)) else "非数组"
        raise ValueError(
            f"{node_def.name_cn} 在线数组长度异常：期望 {node_def.array_len}，实际 {actual}"
        )
    if not 0 <= index < node_def.array_len:
        raise ValueError(f"数组下标必须在 0..{node_def.array_len - 1} 之间")
    updated = list(current)
    updated[index] = _coerce_node_value(node_def.data_type, raw_value)
    return updated


def _write_node_element(
    url: str,
    node_def: NodeDef,
    index: int,
    raw_value: Any,
) -> tuple[Any, Any]:
    """单连接内读整组、改一个元素、写整组并回读确认。"""
    client = Client(url, timeout=4)
    try:
        client.connect()
        node = client.get_node(node_def.node_id)
        updated = _replace_array_element(node_def, node.get_value(), index, raw_value)
        node.set_value(ua.Variant(updated, VTYPE_MAP[node_def.data_type]))
        confirmed = node.get_value()
        if (
            not isinstance(confirmed, (list, tuple))
            or len(confirmed) != node_def.array_len
        ):
            raise RuntimeError(
                f"{node_def.name_cn} 写后回读不是长度 {node_def.array_len} 的数组"
            )
        return confirmed, confirmed[index]
    finally:
        with contextlib.suppress(Exception):
            client.disconnect()


@router.get("/api/server/variables")
async def api_server_variables(
    query: str = Query("", max_length=200),
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
) -> dict[str, Any]:
    """读取当前托管 OPC UA Server 的在线变量值。"""
    _require_running_server()
    needle = query.strip().casefold()
    node_defs = STATE.server_node_defs
    if needle:
        node_defs = [
            item
            for item in node_defs
            if needle in item.name_cn.casefold()
            or needle in item.name_en.casefold()
            or needle in item.node_id.casefold()
            or needle in item.data_type.casefold()
        ]
    total = len(node_defs)
    page = node_defs[offset : offset + limit]
    if not page:
        return {
            "ok": True,
            "total": total,
            "offset": offset,
            "limit": limit,
            "items": [],
        }

    assert STATE.server_client_url is not None
    try:
        async with STATE.server_io_lock:
            values = await asyncio.to_thread(
                _read_node_values, STATE.server_client_url, page
            )
    except Exception as exc:
        raise HTTPException(502, f"读取 OPC UA 变量失败: {exc}") from exc

    items = [
        {
            "name": node_def.name_cn,
            "english_name": node_def.name_en,
            "data_type": node_def.data_type,
            "array_len": node_def.array_len,
            "write_owner": node_def.write_owner,
            "writable": node_def.write_owner != "plc",
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
    node_ids: list[str]


@router.post("/api/server/variables/read")
async def api_server_variables_read(req: ServerVariablesReadReq) -> dict[str, Any]:
    """批量读取监控栏中的变量，保持请求顺序并报告已失效的 NodeId。"""
    _require_running_server()
    node_ids = list(
        dict.fromkeys(item.strip() for item in req.node_ids if item.strip())
    )
    if len(node_ids) > 200:
        raise HTTPException(400, "监控栏一次最多读取 200 个变量")

    definitions = {item.node_id: item for item in STATE.server_node_defs}
    selected = [definitions[node_id] for node_id in node_ids if node_id in definitions]
    missing = [node_id for node_id in node_ids if node_id not in definitions]
    if not selected:
        return {"ok": True, "items": [], "missing": missing}

    assert STATE.server_client_url is not None
    try:
        async with STATE.server_io_lock:
            values = await asyncio.to_thread(
                _read_node_values, STATE.server_client_url, selected
            )
    except Exception as exc:
        raise HTTPException(502, f"读取监控变量失败: {exc}") from exc

    items = [
        {
            "name": node_def.name_cn,
            "english_name": node_def.name_en,
            "data_type": node_def.data_type,
            "array_len": node_def.array_len,
            "write_owner": node_def.write_owner,
            "writable": node_def.write_owner != "plc",
            "node_id": node_def.node_id,
            "value": value,
        }
        for node_def, value in zip(selected, values)
    ]
    return {"ok": True, "items": items, "missing": missing}


class ServerVariableWriteReq(BaseModel):
    node_id: str
    value: Any
    index: int | None = Field(default=None, ge=0)
    maintenance_override: bool = False


@router.post("/api/server/variable")
async def api_server_variable_write(req: ServerVariableWriteReq) -> dict[str, Any]:
    """按 CSV 声明的数据类型写入一个在线变量并回读确认。"""
    _require_running_server()
    node_def = next(
        (item for item in STATE.server_node_defs if item.node_id == req.node_id),
        None,
    )
    if node_def is None:
        raise HTTPException(404, "变量不在当前 Server 的 CSV 定义中")
    if node_def.write_owner == "plc" and not req.maintenance_override:
        raise HTTPException(
            409,
            f"{node_def.name_cn} 是 PLC 输出，只能由握手/行为代理写入；"
            "如确需人工诊断，请显式启用维护写入",
        )
    try:
        if req.index is not None:
            if not node_def.array_len:
                raise ValueError(f"{node_def.name_cn} 不是数组节点，不能指定 index")
            if req.index >= node_def.array_len:
                raise ValueError(f"数组下标必须在 0..{node_def.array_len - 1} 之间")
            typed_value = None
        elif node_def.array_len:
            raw_values = req.value
            if isinstance(raw_values, str):
                raw_values = json.loads(raw_values)
            if (
                not isinstance(raw_values, list)
                or len(raw_values) != node_def.array_len
            ):
                raise ValueError(f"数组长度必须为 {node_def.array_len}")
            typed_value = [
                _coerce_node_value(node_def.data_type, item) for item in raw_values
            ]
        else:
            typed_value = _coerce_node_value(node_def.data_type, req.value)
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc

    assert STATE.server_client_url is not None
    try:
        async with STATE.server_io_lock:
            if req.index is None:
                confirmed = await asyncio.to_thread(
                    _write_node_value,
                    STATE.server_client_url,
                    node_def,
                    typed_value,
                )
                element_value = None
            else:
                confirmed, element_value = await asyncio.to_thread(
                    _write_node_element,
                    STATE.server_client_url,
                    node_def,
                    req.index,
                    req.value,
                )
    except Exception as exc:
        raise HTTPException(502, f"写入 OPC UA 变量失败: {exc}") from exc

    log.info("在线写变量 %s (%s) = %r", node_def.name_cn, node_def.node_id, confirmed)
    return {
        "ok": True,
        "node_id": node_def.node_id,
        "name": node_def.name_cn,
        "data_type": node_def.data_type,
        "array_len": node_def.array_len,
        "write_owner": node_def.write_owner,
        "writable": node_def.write_owner != "plc",
        "index": req.index,
        "element_value": element_value,
        "value": confirmed,
    }
