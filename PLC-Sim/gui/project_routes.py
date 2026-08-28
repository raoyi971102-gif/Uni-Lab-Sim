"""InoProShop project, extraction, editing, and version-history routes."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel

try:
    from ..common import runtime_data_dir
    from ..ino_mcp.client import McpClient
    from ..ino_mcp.config import resolve_mcp_config
    from ..ino_mcp.extractor import (
        _to_csv_rows,
        build_dut_registry_from_dump,
        extract_gvl_variables,
        list_editables_from_dump,
        parse_warm_dump,
        write_csv,
    )
    from ..ino_mcp.project_versions import ProjectVersionRepo
    from ..ino_mcp.symbols import parse_symbols, set_symbol_pragma
    from ..ino_mcp.toolkit import DownloadStrategy, InoToolkit
except ImportError:  # Source checkout: ``import gui.backend``.
    from common import runtime_data_dir
    from ino_mcp.client import McpClient
    from ino_mcp.config import resolve_mcp_config
    from ino_mcp.extractor import (
        _to_csv_rows,
        build_dut_registry_from_dump,
        extract_gvl_variables,
        list_editables_from_dump,
        parse_warm_dump,
        write_csv,
    )
    from ino_mcp.project_versions import ProjectVersionRepo
    from ino_mcp.symbols import parse_symbols, set_symbol_pragma
    from ino_mcp.toolkit import DownloadStrategy, InoToolkit

from .backend_state import STATE

router = APIRouter()
log = logging.getLogger("gui.project")


def load_mcp_defaults(server_name: str = "codesys_local") -> dict[str, Any]:
    """Resolve the configured InoProShop MCP runtime."""

    return resolve_mcp_config(server_name=server_name)


class OpenReq(BaseModel):
    path: str
    bundle: str | None = None
    codesys_path: str | None = None
    codesys_profile: str | None = None
    workspace: str | None = None
    node: str | None = None


@router.post("/api/project/open")
async def api_project_open(req: OpenReq) -> dict[str, Any]:
    proj = str(Path(req.path).resolve())
    if not Path(proj).exists():
        raise HTTPException(400, f".project 不存在: {proj}")

    async with STATE.mcp_lock:
        # 若已经连着别的项目, 先关掉
        if STATE.mcp is not None:
            log.info("先关闭旧 MCP: %s", STATE.current_project)
            await asyncio.to_thread(STATE.mcp.close)
            STATE.mcp = None
            STATE.toolkit = None
            STATE.current_project = None
            STATE.version_repo = None
        # 新项目, 清缓存
        STATE.declarations_dump = None
        STATE.editables_cache = None

        cfg = load_mcp_defaults()
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
                "找不到 MCP bundle.min.js；请设置 PLCSIM_MCP_BUNDLE、"
                "配置 MCP JSON，或在请求中指定 bundle",
            )
        if not cfg["codesys_path"] or not Path(cfg["codesys_path"]).exists():
            raise HTTPException(
                400,
                "找不到 InoProShop.exe；请设置 PLCSIM_INOPROSHOP_EXE "
                "或在请求中指定 codesys_path",
            )

        STATE.busy = "opening"
        try:
            mcp = McpClient(
                bundle_js=cfg["bundle_js"],
                codesys_path=cfg["codesys_path"],
                codesys_profile=cfg["codesys_profile"],
                workspace=cfg["workspace"],
                node_cmd=cfg["node_cmd"],
            )
            await asyncio.to_thread(mcp.start)
            tk = InoToolkit(mcp, proj)
            out = await asyncio.to_thread(tk.open_project)
            STATE.mcp = mcp
            STATE.toolkit = tk
            STATE.current_project = proj
            STATE.version_repo = ProjectVersionRepo(
                Path(proj), runtime_data_dir() / "plc-history"
            )
            await asyncio.to_thread(
                STATE.version_repo.snapshot_if_changed, "首次由 PLC-Sim 打开工程"
            )
            STATE.last_error = None
            log.info("项目已打开: %s", proj)
            return {"ok": True, "message": out.strip(), "state": STATE.snapshot()}
        except Exception as exc:
            err = f"{type(exc).__name__}: {exc}"
            log.exception("open_project 失败: %s", err)
            STATE.last_error = err
            with contextlib.suppress(Exception, NameError):
                mcp.close()  # type: ignore[has-type,used-before-def]
            STATE.mcp = None
            STATE.toolkit = None
            STATE.current_project = None
            STATE.version_repo = None
            raise HTTPException(500, err)
        finally:
            STATE.busy = None


@router.post("/api/project/close")
async def api_project_close() -> dict[str, Any]:
    async with STATE.mcp_lock:
        if STATE.mcp is not None:
            await asyncio.to_thread(STATE.mcp.close)
        STATE.mcp = None
        STATE.toolkit = None
        STATE.current_project = None
        STATE.version_repo = None
        STATE.declarations_dump = None
        STATE.editables_cache = None
        log.info("已断开 MCP")
        return {"ok": True, "state": STATE.snapshot()}


async def _ensure_declarations_dump(force: bool = False) -> str:
    """拿 dump_all_declarations 的结果 (带缓存, 供 editables + extract 复用)。"""
    if not force and STATE.declarations_dump:
        return STATE.declarations_dump
    tk = _require_tk()
    STATE.busy = "scanning"
    try:
        dump = await asyncio.to_thread(tk.dump_all_declarations)
    finally:
        STATE.busy = None
    STATE.declarations_dump = dump
    STATE.editables_cache = None  # 让 editables 端点重算
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


@router.get("/api/project/editables")
async def api_project_editables(refresh: bool = False) -> dict[str, Any]:
    """列出项目里所有可编辑对象 (POU / GVL / DUT)。带缓存 —— 首次约 20s (跑 IronPython 探针),
    后续瞬时；refresh=true 强制重跑。
    """
    _require_tk()
    if not refresh and STATE.editables_cache is not None:
        return {"ok": True, "cached": True, "items": STATE.editables_cache}
    async with STATE.mcp_lock:
        dump = await _ensure_declarations_dump(force=refresh)
        items_dc = list_editables_from_dump(dump)
        items = [
            {
                "name": e.name,
                "path": e.path,
                "kind": e.kind,
                "has_impl": e.has_implementation,
                "lang": e.lang,
            }
            for e in items_dc
        ]
        STATE.editables_cache = items
        return {"ok": True, "cached": False, "items": items}


@router.post("/api/project/warm")
async def api_project_warm() -> dict[str, Any]:
    """项目预热: 一次探针把所有 POU/GVL/DUT 的声明 + 实现全部拉回来并塞满缓存。

    项目打开成功后前端 fire-and-forget 调这个 —— 之后:
      - 单独读任何 POU/GVL 都 <50ms 命中 pou_code cache
      - editables 列表已经在 STATE.editables_cache 里
      - extract 时的 DUT registry 也已经建好, 秒出

    ~20s (跟 dump_all_declarations 同数量级, 因为都是 walk 一遍 Application)。
    """
    tk = _require_tk()
    async with STATE.mcp_lock:
        STATE.busy = "warming"
        try:
            warm_text = await asyncio.to_thread(tk.warm_all_code)
            STATE.warm_raw = warm_text
            entries = parse_warm_dump(warm_text)
            paths = [e.path for e in entries]

            # 排障: 从 raw 里 grep 出 WALK 到的对象, 对比 emit 出来的对象, 找出差集
            import re as _re

            walked = _re.findall(r"^WALK\s+(.+)$", warm_text, _re.MULTILINE)
            not_emitted = _re.findall(r"^NOT_EMITTED\s+(.+)$", warm_text, _re.MULTILINE)
            skipped = _re.findall(
                r"^(SKIP_[A-Z_]+|OBJ_ERR|DEC_TEXT_ERR)\s+(.+?)(?::|$)",
                warm_text,
                _re.MULTILINE,
            )
            if len(walked) != len(entries):
                log.warning(
                    "[warm] walk=%d 个对象 → emit=%d 个. NOT_EMITTED=%s SKIP=%s",
                    len(walked),
                    len(entries),
                    not_emitted,
                    skipped,
                )
                log.warning("[warm] 缺失: %s", set(walked) - set(paths))

            # 1) editables cache
            STATE.editables_cache = [
                {
                    "name": e.path.rsplit("/", 1)[-1],
                    "path": e.path,
                    "kind": e.kind,
                    "has_impl": e.has_impl,
                    "lang": e.lang,
                }
                for e in entries
            ]
            # 2) pou_code cache
            tk.prefill_pou_code_cache(
                [(e.path, e.declaration, e.implementation) for e in entries]
            )
            # 3) declarations_dump cache
            STATE.declarations_dump = _synth_dump_from_warm_entries(entries)
            log.info(
                "[warm] 项目预热完成: %d 对象 (walk=%d), cache=%s",
                len(entries),
                len(walked),
                tk.cache.stats(),
            )
            return {
                "ok": True,
                "warmed": len(entries),
                "walked": len(walked),
                "not_emitted": not_emitted,
                "cache": tk.cache.stats(),
                "kinds": {
                    "POU": sum(1 for e in entries if e.kind == "POU"),
                    "GVL": sum(1 for e in entries if e.kind == "GVL"),
                    "DUT": sum(1 for e in entries if e.kind == "DUT"),
                },
            }
        finally:
            STATE.busy = None


@router.get("/api/project/warm/raw", response_class=PlainTextResponse)
async def api_project_warm_raw() -> str:
    """诊断: 返回上次 warm_all_code 的原始输出 (供人肉排查为什么某些对象没被识别)。"""
    if STATE.warm_raw is None:
        return "(还未跑过 warm — 先 POST /api/project/warm)"
    return STATE.warm_raw


@router.get("/api/project/cache")
async def api_project_cache() -> dict[str, Any]:
    """报告当前 toolkit + backend 的缓存命中情况 (调试 / GUI 显示用)。"""
    tk = STATE.toolkit
    return {
        "toolkit": tk.cache.stats() if tk else None,
        "backend": {
            "declarations_dump": STATE.declarations_dump is not None,
            "editables": len(STATE.editables_cache) if STATE.editables_cache else 0,
        },
    }


def _require_tk() -> InoToolkit:
    if STATE.toolkit is None:
        raise HTTPException(400, "请先打开一个 .project 项目")
    return STATE.toolkit


@router.post("/api/project/save")
async def api_project_save() -> dict[str, Any]:
    tk = _require_tk()
    async with STATE.mcp_lock:
        STATE.busy = "saving"
        try:
            out = await asyncio.to_thread(tk.save_project)
            version = None
            if STATE.version_repo is not None:
                version = await asyncio.to_thread(
                    STATE.version_repo.snapshot_if_changed, "GUI 保存工程"
                )
            return {"ok": True, "message": out.strip(), "version": version}
        finally:
            STATE.busy = None


@router.post("/api/project/compile")
async def api_project_compile() -> dict[str, Any]:
    tk = _require_tk()
    async with STATE.mcp_lock:
        STATE.busy = "compiling"
        try:
            cr = await asyncio.to_thread(tk.compile_project)
            return {
                "ok": cr.ok,
                "summary": cr.summary,
                "raw": cr.raw[-2000:] if cr.raw else "",
            }
        finally:
            STATE.busy = None


class DownloadReq(BaseModel):
    strategy: str = "save_compile"  # 或 "online"
    confirm_online: bool = False
    expected_project_sha256: str | None = None


def _online_deploy_allowed() -> bool:
    """读取旧版在线下载开关；它只用于迁移提示，不再代表下载授权。"""

    return os.environ.get("PLCSIM_ALLOW_ONLINE_DEPLOY", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


@router.get("/api/project/deploy/preflight")
async def api_project_deploy_preflight() -> dict[str, Any]:
    """返回项目摘要和在线部署能力；当前只允许保存与编译。"""

    _require_tk()
    repo = STATE.version_repo
    sha = await asyncio.to_thread(repo.current_sha256) if repo else None
    return {
        "ok": True,
        "online_allowed": False,
        "project_sha256": sha,
        "warning": (
            "GUI 在线下载已关闭：当前实现没有接入 PlcProgramService 的维护门、"
            "一次性授权和 PLC_Deploy_* 握手；save_compile 只保存和编译，不会下装"
        ),
    }


@router.post("/api/project/download")
async def api_project_download(req: DownloadReq) -> dict[str, Any]:
    """保存编译项目；真实 PLC 在线下载在安全服务接入前关闭失败。"""

    try:
        strat = DownloadStrategy(req.strategy)
    except ValueError:
        raise HTTPException(400, f"未知 strategy: {req.strategy}")
    if strat == DownloadStrategy.ONLINE_IRONPYTHON:
        if not _online_deploy_allowed():
            raise HTTPException(
                403,
                "在线下载默认关闭；确认现场安全后设置 PLCSIM_ALLOW_ONLINE_DEPLOY=true",
            )
        if not req.confirm_online:
            raise HTTPException(400, "在线下载必须显式 confirm_online=true")
        raise HTTPException(
            501,
            "GUI 在线下载已禁用：必须通过 pTLC PlcProgramService 完成维护门、"
            "目标绑定、一次性授权和 PLC_Deploy_* 握手后才能恢复",
        )
    tk = _require_tk()
    async with STATE.mcp_lock:
        STATE.busy = "downloading"
        try:
            repo = STATE.version_repo
            current_sha = await asyncio.to_thread(repo.current_sha256) if repo else None
            if (
                req.expected_project_sha256
                and req.expected_project_sha256 != current_sha
            ):
                raise HTTPException(409, "工程内容已变化，请重新执行部署预检")
            version = None
            if repo is not None:
                version = await asyncio.to_thread(
                    repo.snapshot_if_changed,
                    "在线下载前自动快照"
                    if strat == DownloadStrategy.ONLINE_IRONPYTHON
                    else "保存编译前自动快照",
                )
            report = await asyncio.to_thread(tk.download_program, strat)
            ok = "error" not in report
            if ok and strat == DownloadStrategy.ONLINE_IRONPYTHON and repo is not None:
                deployed_sha = await asyncio.to_thread(repo.current_sha256)
                await asyncio.to_thread(
                    repo.snapshot_if_changed, "在线下载后的工程版本"
                )
                report["version"] = await asyncio.to_thread(
                    repo.mark_deployed, deployed_sha
                )
            report["pre_download_version"] = version
            report["semantics"] = (
                "online_full_download"
                if strat == DownloadStrategy.ONLINE_IRONPYTHON
                else "save_and_compile_only"
            )
            return {"ok": ok, "report": report}
        finally:
            STATE.busy = None


@router.get("/api/project/structure")
async def api_project_structure() -> dict[str, Any]:
    tk = _require_tk()
    async with STATE.mcp_lock:
        STATE.busy = "structure"
        try:
            text = await asyncio.to_thread(tk.get_project_structure)
            return {"ok": True, "text": text}
        finally:
            STATE.busy = None


@router.get("/api/project/gvls")
async def api_project_gvls() -> dict[str, Any]:
    _require_tk()
    # Structure text does not expose a reliable object type. The old heuristic
    # therefore found only objects whose *name* contained "GVL" and missed
    # perfectly valid tables such as IO, HMI_Date and Host_Computer. Reuse the
    # declaration scan, which classifies objects by VAR_GLOBAL content.
    editables = await api_project_editables(refresh=False)
    gvls = [item["path"] for item in editables["items"] if item["kind"] == "GVL"]
    return {"ok": True, "gvls": gvls, "source": "declarations"}


class ExtractReq(BaseModel):
    gvls: list[str] | None = None
    include_all: bool = False
    ns_index: int = 4
    ns_prefix: str = "uniab|"
    node_language: str = "Chinese"  # CSV NodeLanguage 列的固定值
    out_path: str | None = None  # 默认 extracted/<projectname>.csv
    preview_only: bool = False  # True 时只返回 rows, 不写盘
    expand_structs: bool = True  # False 时不自动拉 DUT registry (只展开 ARRAY)


@router.post("/api/project/extract")
async def api_project_extract(req: ExtractReq) -> dict[str, Any]:
    tk = _require_tk()
    proj = Path(STATE.current_project or "extracted")
    default_out = runtime_data_dir() / "extracted" / (proj.stem + ".csv")
    out_path = Path(req.out_path).resolve() if req.out_path else default_out

    async with STATE.mcp_lock:
        STATE.busy = "extracting"
        try:
            # 如果本次会话已经跑过 dump (比如用户先点了 '发现对象'), 直接复用它构造 registry;
            # 省一次 20s 探针
            dut_registry = None
            if req.expand_structs and STATE.declarations_dump:
                dut_registry = build_dut_registry_from_dump(STATE.declarations_dump)
                auto_build = False
            else:
                auto_build = req.expand_structs

            leaves = await asyncio.to_thread(
                extract_gvl_variables,
                tk,
                gvl_paths=req.gvls,
                include_all=req.include_all,
                dut_registry=dut_registry,
                auto_build_dut_registry=auto_build,
            )
            rows = _to_csv_rows(
                leaves,
                ns_index=req.ns_index,
                ns_prefix=req.ns_prefix,
                node_language=req.node_language,
            )
            if not req.preview_only:
                out_path.parent.mkdir(parents=True, exist_ok=True)
                await asyncio.to_thread(
                    write_csv,
                    leaves,
                    out_path,
                    ns_index=req.ns_index,
                    ns_prefix=req.ns_prefix,
                    node_language=req.node_language,
                )
                STATE.last_extract_csv = str(out_path)
                STATE.last_extract_count = len(rows)
            return {
                "ok": True,
                "count": len(rows),
                "out_path": str(out_path) if not req.preview_only else None,
                "rows": rows[:500],  # 前 500 行预览
                "truncated": len(rows) > 500,
            }
        finally:
            STATE.busy = None


# -- POU 编辑 --------------------------------------------------------------
@router.get("/api/pou")
async def api_pou_get(path: str = Query(...)) -> dict[str, Any]:
    tk = _require_tk()
    async with STATE.mcp_lock:
        STATE.busy = "reading_pou"
        try:
            raw = await asyncio.to_thread(tk.get_pou_code, path)
            decl, impl = _split_pou_output(raw)
            return {
                "ok": True,
                "path": path,
                "declaration": decl,
                "implementation": impl,
                "raw": raw,
            }
        finally:
            STATE.busy = None


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
    impl = text[i + len("implementation:") :].strip() if i >= 0 else ""
    return decl, impl


class PouSetReq(BaseModel):
    path: str
    declaration: str | None = None
    implementation: str | None = None
    save: bool = False
    compile: bool = False


@router.post("/api/pou")
async def api_pou_set(req: PouSetReq) -> dict[str, Any]:
    tk = _require_tk()
    if req.declaration is None and req.implementation is None:
        raise HTTPException(400, "declaration 与 implementation 至少给一个")
    async with STATE.mcp_lock:
        STATE.busy = "writing_pou"
        result: dict[str, Any] = {}
        try:
            if STATE.version_repo is not None:
                await asyncio.to_thread(
                    STATE.version_repo.snapshot_if_changed,
                    f"修改 {req.path} 前自动快照",
                )
            out = await asyncio.to_thread(
                tk.set_pou_code,
                req.path,
                declaration=req.declaration,
                implementation=req.implementation,
            )
            result["set"] = out.strip()
            if req.save:
                result["save"] = (await asyncio.to_thread(tk.save_project)).strip()
                if STATE.version_repo is not None:
                    result["version"] = await asyncio.to_thread(
                        STATE.version_repo.snapshot_if_changed,
                        f"修改 {req.path}",
                    )
            if req.compile:
                cr = await asyncio.to_thread(tk.compile_project)
                result["compile"] = {"ok": cr.ok, "summary": cr.summary}
            return {"ok": True, **result}
        finally:
            STATE.busy = None


@router.get("/api/project/symbols")
async def api_project_symbols(path: str = Query(...)) -> dict[str, Any]:
    tk = _require_tk()
    async with STATE.mcp_lock:
        raw = await asyncio.to_thread(tk.get_pou_code, path)
        declaration, _ = _split_pou_output(raw)
        return {"ok": True, "path": path, "symbols": parse_symbols(declaration)}


class SymbolSetReq(BaseModel):
    path: str
    name: str
    enabled: bool
    compile: bool = False


@router.post("/api/project/symbol")
async def api_project_symbol_set(req: SymbolSetReq) -> dict[str, Any]:
    tk = _require_tk()
    async with STATE.mcp_lock:
        if STATE.version_repo is not None:
            await asyncio.to_thread(
                STATE.version_repo.snapshot_if_changed,
                f"修改符号 {req.path}/{req.name} 前自动快照",
            )
        raw = await asyncio.to_thread(tk.get_pou_code, req.path)
        declaration, _ = _split_pou_output(raw)
        try:
            updated = set_symbol_pragma(declaration, req.name, req.enabled)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        await asyncio.to_thread(
            tk.set_pou_code, req.path, declaration=updated, implementation=None
        )
        await asyncio.to_thread(tk.save_project)
        compile_result = None
        if req.compile:
            result = await asyncio.to_thread(tk.compile_project)
            compile_result = {"ok": result.ok, "summary": result.summary}
        version = None
        if STATE.version_repo is not None:
            version = await asyncio.to_thread(
                STATE.version_repo.snapshot_if_changed,
                f"符号 {req.name}={'on' if req.enabled else 'off'}",
            )
        return {
            "ok": compile_result is None or compile_result["ok"],
            "path": req.path,
            "name": req.name,
            "enabled": req.enabled,
            "compile": compile_result,
            "version": version,
        }


@router.get("/api/project/versions")
async def api_project_versions() -> dict[str, Any]:
    _require_tk()
    if STATE.version_repo is None:
        return {"ok": True, "items": []}
    return {"ok": True, "items": await asyncio.to_thread(STATE.version_repo.history)}


@router.get("/api/project/versions/{rev}/download")
async def api_project_version_download(rev: str) -> FileResponse:
    _require_tk()
    if STATE.version_repo is None:
        raise HTTPException(404, "工程版本库未启用")
    try:
        path = await asyncio.to_thread(STATE.version_repo.version_path, rev)
    except KeyError as exc:
        raise HTTPException(404, f"工程版本不存在: {rev}") from exc
    return FileResponse(
        path, filename=f"{Path(STATE.current_project or 'plc').stem}-{rev}.project"
    )


class VersionRestoreReq(BaseModel):
    confirm: bool = False


@router.post("/api/project/versions/{rev}/restore")
async def api_project_version_restore(
    rev: str, req: VersionRestoreReq
) -> dict[str, Any]:
    if not req.confirm:
        raise HTTPException(400, "恢复二进制工程必须显式 confirm=true")
    _require_tk()
    async with STATE.mcp_lock:
        repo = STATE.version_repo
        if repo is None:
            raise HTTPException(404, "工程版本库未启用")
        # InoProShop 持有二进制工程时禁止外部替换；先完整关闭会话再原子恢复。
        if STATE.mcp is not None:
            await asyncio.to_thread(STATE.mcp.close)
        STATE.mcp = None
        STATE.toolkit = None
        STATE.current_project = None
        STATE.version_repo = None
        STATE.declarations_dump = None
        STATE.editables_cache = None
        result = await asyncio.to_thread(repo.restore, rev)
        return {"ok": True, **result, "reopen_required": True}


# -- Server / Agent 子进程 -------------------------------------------------
