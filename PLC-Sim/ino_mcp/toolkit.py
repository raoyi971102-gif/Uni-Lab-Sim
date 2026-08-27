"""
ino_mcp.toolkit —— InoProShop MCP 业务级封装
============================================================================
把 24 个原生 MCP 工具收敛为「工程师视角」的高层方法：
    open_project / save_project / compile_project
    get_project_structure
    create_pou / get_pou_code / set_pou_code / delete_pou
    create_gvl / set_gvl_declaration / read_gvl_declaration
    probe_custom
    download_program（保存 + 编译，MCP 不支持真正的在线下载 —— 见下方说明）

关于「下载程序块」:
    InoProShop MCP 目前 **不提供** online-download API（读 bundle.min.js 已确认）。
    因此 download_program 有两种策略：
        - DownloadStrategy.SAVE_COMPILE (默认): save_project + compile_project，
          编译通过就视作"部署到项目文件"完成。这是最保守也最可复现的方式。
        - DownloadStrategy.ONLINE_IRONPYTHON: 额外通过 probe_api(custom) 注入
          IronPython 尝试 online.login()/download(), 依赖 SP11 内核实际暴露的
          online 对象。若失败会退回 SAVE_COMPILE。
"""

from __future__ import annotations

import enum
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .client import McpClient, McpError


log = logging.getLogger("ino_mcp.toolkit")


class DownloadStrategy(str, enum.Enum):
    SAVE_COMPILE = "save_compile"
    ONLINE_IRONPYTHON = "online"


# ---------------------------------------------------------------------------
# CODESYS 在线下载脚本（尽可能 best-effort；不同 SP 内核 API 差异较大）
# ---------------------------------------------------------------------------
_ONLINE_DOWNLOAD_SCRIPT = r"""
# probe_api custom: 尝试触发 online.login + download
try:
    app = projects.primary.active_application
    online_app = online.create_online_application(app)
    online_app.login(OnlineChangeOption.Try, True)   # Try change first
    online_app.download()
    online_app.logout()
    rlog("ONLINE_DOWNLOAD_OK")
except Exception, ex:
    rlog("ONLINE_DOWNLOAD_FAIL: %s" % ex)
"""


# 递归 dump 项目里所有 IScriptObject 的 textual_declaration.text —— 用于批量识别 DUT/GVL/POU。
# InoProShop SP11 里 POU/GVL/DUT 都实现 IScriptTextualDeclarationObject, 都能拿 declaration.text。
# POU 额外实现 IScriptTextualImplementationObject, 有 textual_implementation.text。
# 切块靠 ===DECL_BEGIN=== / PATH: / MIXIN: / IMPL: / ---BODY--- / ===DECL_END=== 多个 marker
# (Python 端 regex 切分)。
_DUMP_ALL_DECLARATIONS_SCRIPT = r"""
import re
_NAME_RE = re.compile(r'Name=([^,)]+)')

try:
    def _extract_name(obj):
        s = str(obj)
        m = _NAME_RE.search(s)
        return m.group(1).strip() if m else '<unknown>'

    def _walk(obj, path, depth):
        if depth > 8: return
        try:
            children = list(obj.get_children())
        except Exception, ex:
            children = []
        for c in children:
            c_name = _extract_name(c)
            cur_path = path + '/' + c_name
            try:
                dec = getattr(c, 'textual_declaration', None)
                if dec is not None:
                    text = dec.text
                    if text is not None:
                        # 判断是否有实现 (POU 有, GVL/DUT 没有)
                        has_impl = False
                        try:
                            impl = getattr(c, 'textual_implementation', None)
                            has_impl = impl is not None
                        except: pass
                        rlog('===DECL_BEGIN===')
                        rlog('PATH: ' + cur_path)
                        rlog('IMPL: ' + ('1' if has_impl else '0'))
                        rlog('MIXIN: ' + str(c))
                        rlog('---BODY---')
                        rlog(text)
                        rlog('===DECL_END===')
            except Exception, ex:
                rlog('OBJ_ERR: ' + str(ex))
            _walk(c, cur_path, depth + 1)

    p = projects.primary
    apps = list(p.find('Application', True))
    if not apps:
        rlog('DUMP_FAIL: no Application object')
    else:
        _walk(apps[0], 'Application', 0)
        rlog('DUMP_DONE')
except Exception, ex:
    rlog('DUMP_FAIL_TOP: ' + str(ex))
"""


# ---------------------------------------------------------------------------
# warm_all_code: dump 的超集 —— 一次探针同时拉回 declaration + implementation
# 供 GUI 项目打开后一次性预热所有 POU/GVL/DUT, 之后单个读取全部命中缓存
# ---------------------------------------------------------------------------
_WARM_ALL_CODE_SCRIPT = r"""
import re
_NAME_RE = re.compile(r'Name=([^,)]+)')

try:
    def _extract_name(obj):
        s = str(obj)
        m = _NAME_RE.search(s)
        return m.group(1).strip() if m else '<unknown>'

    def _walk(obj, path, depth):
        if depth > 8: return
        try: children = list(obj.get_children())
        except Exception, ex_ch:
            rlog('WALK_CHILDREN_ERR ' + path + ': ' + str(ex_ch))
            children = []
        for c in children:
            c_name = _extract_name(c)
            cur_path = path + '/' + c_name
            rlog('WALK ' + cur_path)
            # 与 dump 脚本一样, 只需 declaration 存在就输出 (impl 部分单独 try 抓)
            emitted = False
            try:
                dec = getattr(c, 'textual_declaration', None)
                if dec is None:
                    rlog('SKIP_NO_DEC ' + cur_path)
                else:
                    text = None
                    try: text = dec.text
                    except Exception, ex_dt:
                        rlog('DEC_TEXT_ERR ' + cur_path + ': ' + str(ex_dt))
                    if text is None:
                        rlog('SKIP_DEC_TEXT_NONE ' + cur_path)
                    else:
                        # impl 独立 try, 拿不到就当空
                        impl_text = ''
                        has_impl = False
                        try:
                            impl = getattr(c, 'textual_implementation', None)
                            if impl is not None:
                                has_impl = True
                                it = impl.text
                                if it is not None:
                                    impl_text = it
                        except Exception, ex_impl:
                            impl_text = '<IMPL_ERR: ' + str(ex_impl) + '>'
                        rlog('===OBJ_BEGIN===')
                        rlog('PATH: ' + cur_path)
                        rlog('IMPL: ' + ('1' if has_impl else '0'))
                        rlog('MIXIN: ' + str(c))
                        rlog('---DECL---')
                        rlog(text)
                        rlog('---IMPL---')
                        rlog(impl_text)
                        rlog('===OBJ_END===')
                        emitted = True
            except Exception, ex:
                rlog('OBJ_ERR ' + cur_path + ': ' + str(ex))
            if not emitted:
                rlog('NOT_EMITTED ' + cur_path)
            _walk(c, cur_path, depth + 1)

    p = projects.primary
    apps = list(p.find('Application', True))
    if not apps:
        rlog('WARM_FAIL: no Application object')
    else:
        _walk(apps[0], 'Application', 0)
        rlog('WARM_DONE')
except Exception, ex:
    rlog('WARM_FAIL_TOP: ' + str(ex))
"""


@dataclass
class CompileResult:
    ok: bool
    summary: str          # "Compiled 0 errors, 0 warnings" 之类
    raw: str              # 原始 MCP 输出（供报错时看细节）

    def __bool__(self) -> bool:  # 方便 `if not compile_project(): ...`
        return self.ok


@dataclass
class ToolkitCache:
    """InoToolkit 的本地缓存 —— 减少 MCP 冷启动次数 (每次 bundle 都要 spawn InoProShop.exe)。

    invalidate 语义:
        - set_pou_code(path)       -> 清 pou_code[path], 清 dump/warm, 保留 structure
        - create_pou/gvl/delete_*  -> 清全部 (结构变了)
        - save_project/compile_*   -> 什么都不清 (纯观察操作)
    """
    structure: Optional[str] = None                       # get_project_structure
    dump_all: Optional[str] = None                        # dump_all_declarations
    warm_all: Optional[str] = None                        # warm_all_code
    pou_code: Dict[str, str] = field(default_factory=dict)  # get_pou_code(path)

    def stats(self) -> Dict[str, Any]:
        return {
            "structure": self.structure is not None,
            "dump_all":  self.dump_all is not None,
            "warm_all":  self.warm_all is not None,
            "pou_code":  len(self.pou_code),
        }

    def clear_all(self) -> None:
        self.structure = None
        self.dump_all  = None
        self.warm_all  = None
        self.pou_code.clear()


class InoToolkit:
    """在 McpClient 之上的高层封装；所有 API 都是同步阻塞。

    带本地缓存 (ToolkitCache) —— 只读操作 (get_pou_code/get_project_structure/
    dump_all_declarations) 会自动命中缓存, 写操作 (set_pou_code / create_* /
    delete_*) 会自动失效相关条目。这是「方案 A: 缓存 + 合并」的基础。
    """

    def __init__(self, mcp: McpClient, project_file: str | os.PathLike) -> None:
        self.mcp = mcp
        self.project_file = str(Path(project_file).resolve())
        self.cache = ToolkitCache()

    # -- 项目 -------------------------------------------------------------
    def open_project(self) -> str:
        """打开当前 project_file（幂等；已打开则复用）。

        注意: InoProShop MCP 里 open_project / create_project 用字段 `filePath`,
        而其它所有工具（save/compile/get/set/create_*）都用 `projectFilePath` ——
        这是 bundle.min.js 的历史命名不一致，别踩坑。
        """
        log.info("open_project: %s", self.project_file)
        return self.mcp.call_tool("open_project", {"filePath": self.project_file})

    def save_project(self) -> str:
        log.info("save_project")
        return self.mcp.call_tool("save_project", {"projectFilePath": self.project_file})

    def compile_project(self) -> CompileResult:
        log.info("compile_project")
        try:
            raw = self.mcp.call_tool("compile_project", {"projectFilePath": self.project_file})
            summary = _first_line_starting_with(raw, "Compiled ") or raw.splitlines()[0] if raw else ""
            return CompileResult(ok=True, summary=summary, raw=raw)
        except McpError as exc:
            return CompileResult(ok=False, summary=str(exc).splitlines()[0][:200], raw=str(exc))

    def get_project_structure(self, *, use_cache: bool = True) -> str:
        if use_cache and self.cache.structure is not None:
            log.info("[cache HIT] get_project_structure")
            return self.cache.structure
        r = self.mcp.call_tool("get_project_structure",
                               {"projectFilePath": self.project_file})
        self.cache.structure = r
        return r

    # -- POU / 代码 -------------------------------------------------------
    def create_pou(self, name: str, pou_type: str = "Program",
                   language: str = "ST", parent_path: str = "Application") -> str:
        r = self.mcp.call_tool("create_pou", {
            "projectFilePath": self.project_file,
            "name": name, "type": pou_type,
            "language": language, "parentPath": parent_path,
        })
        self.cache.clear_all()   # 结构变了, 全清
        return r

    def get_pou_code(self, pou_path: str, *, use_cache: bool = True) -> str:
        if use_cache:
            cached = self.cache.pou_code.get(pou_path)
            if cached is not None:
                log.info("[cache HIT] get_pou_code: %s", pou_path)
                return cached
        r = self.mcp.call_tool("get_pou_code", {
            "projectFilePath": self.project_file,
            "pouPath": pou_path,
        })
        self.cache.pou_code[pou_path] = r
        return r

    def set_pou_code(self, pou_path: str, *, declaration: Optional[str] = None,
                     implementation: Optional[str] = None) -> str:
        args: Dict[str, Any] = {"projectFilePath": self.project_file, "pouPath": pou_path}
        if declaration is not None:
            args["declarationCode"] = declaration
        if implementation is not None:
            args["implementationCode"] = implementation
        r = self.mcp.call_tool("set_pou_code", args)
        # 写完清: 该 POU 代码缓存 + dump/warm (变量可能变了, DUT 表可能失效)
        self.cache.pou_code.pop(pou_path, None)
        self.cache.dump_all = None
        self.cache.warm_all = None
        return r

    def delete_pou(self, pou_path: str) -> str:
        r = self.mcp.call_tool("delete_pou", {
            "projectFilePath": self.project_file, "pouPath": pou_path,
        })
        self.cache.clear_all()
        return r

    # -- GVL --------------------------------------------------------------
    def create_gvl(self, name: str, parent_path: str = "Application") -> str:
        r = self.mcp.call_tool("create_gvl", {
            "projectFilePath": self.project_file,
            "gvlName": name, "parentPath": parent_path,
        })
        self.cache.clear_all()
        return r

    def read_gvl_declaration(self, gvl_path: str) -> str:
        """读一个 GVL 的声明段（VAR_GLOBAL...END_VAR）——复用 get_pou_code。"""
        raw = self.get_pou_code(gvl_path)
        # get_pou_code 通常返回 "Declaration:\n<text>\n\nImplementation:\n<text>"
        return _extract_declaration(raw)

    def set_gvl_declaration(self, gvl_path: str, declaration_text: str) -> str:
        return self.set_pou_code(gvl_path, declaration=declaration_text)   # set_pou_code 会自动清缓存

    # -- 探针 -------------------------------------------------------------
    def probe_custom(self, target_path: str, script_code: str) -> str:
        """probe_api custom：对 target_path 上下文执行任意 IronPython 脚本。"""
        return self.mcp.call_tool("probe_api", {
            "projectFilePath": self.project_file,
            "targetPath": target_path,
            "probeMode": "custom",
            "customCode": script_code,
        })

    def probe(self, target_path: str, mode: str = "dir") -> str:
        return self.mcp.call_tool("probe_api", {
            "projectFilePath": self.project_file,
            "targetPath": target_path,
            "probeMode": mode,
        })

    def dump_all_declarations(self, *, use_cache: bool = True) -> str:
        """一次 IronPython 探针拉回 Application 下所有对象的 textual_declaration.text。

        输出格式 (extractor.build_dut_registry_from_dump 会切):
            ===DECL_BEGIN===
            PATH: Application/xxx
            IMPL: 0|1
            MIXIN: <对象 str 描述>
            ---BODY---
            <VAR_GLOBAL... / TYPE... 完整声明段文本>
            ===DECL_END===

        走 probe_api custom, 平均 15~30s (跟对象数量线性)。带缓存。
        """
        if use_cache and self.cache.dump_all is not None:
            log.info("[cache HIT] dump_all_declarations")
            return self.cache.dump_all
        log.info("dump_all_declarations: 一次探针拿全部 declaration")
        r = self.probe_custom("Application", _DUMP_ALL_DECLARATIONS_SCRIPT)
        self.cache.dump_all = r
        return r

    def warm_all_code(self, *, use_cache: bool = True) -> str:
        """一次 IronPython 探针拉回所有 POU/GVL/DUT 的 declaration + implementation。

        输出格式:
            ===OBJ_BEGIN===
            PATH: Application/xxx
            IMPL: 0|1
            MIXIN: <对象 str 描述>
            ---DECL---
            <declaration text>
            ---IMPL---
            <implementation text (POU 才有, GVL/DUT 是空)>
            ===OBJ_END===

        走 probe_api custom。这是 dump_all_declarations 的超集 —— 项目打开后
        跑一次这个 (~20s), 之后所有单 POU 读取全部命中 pou_code 缓存。
        """
        if use_cache and self.cache.warm_all is not None:
            log.info("[cache HIT] warm_all_code")
            return self.cache.warm_all
        log.info("warm_all_code: 一次探针拿全部 declaration + implementation")
        r = self.probe_custom("Application", _WARM_ALL_CODE_SCRIPT)
        self.cache.warm_all = r
        return r

    def prefill_pou_code_cache(self, entries: List[Tuple[str, str, str]]) -> None:
        """把 warm 结果预填到 pou_code 缓存。

        entries: [(path, declaration_text, implementation_text), ...]

        存的字符串格式跟 get_pou_code 返回的一致 —— 即:
            "Declaration:\n<decl>\n\nImplementation:\n<impl>"
        这样 backend / GUI 拿的时候接口不变。
        """
        n = 0
        for path, dec, impl in entries:
            self.cache.pou_code[path] = (
                "Declaration:\n" + (dec or "") +
                "\n\nImplementation:\n" + (impl or "")
            )
            n += 1
        log.info("[cache] prefill_pou_code_cache: 塞入 %d 个 POU", n)

    # -- 下载 -------------------------------------------------------------
    def download_program(
        self,
        strategy: DownloadStrategy = DownloadStrategy.SAVE_COMPILE,
    ) -> Dict[str, Any]:
        """把编辑好的程序块"下载"到 PLC。

        返回:
            {"saved": bool, "compile": CompileResult, "online": str|None, "strategy": ...}
        """
        report: Dict[str, Any] = {"strategy": strategy.value, "saved": False,
                                  "compile": None, "online": None}
        # 1) 先保存
        try:
            self.save_project()
            report["saved"] = True
        except McpError as exc:
            log.error("save 失败: %s", exc)
            report["error"] = f"save: {exc}"
            return report

        # 2) 编译验证
        cr = self.compile_project()
        report["compile"] = {"ok": cr.ok, "summary": cr.summary}
        if not cr.ok:
            report["error"] = f"compile: {cr.summary}"
            return report

        # 3) 可选：走 IronPython 尝试在线下载
        if strategy == DownloadStrategy.ONLINE_IRONPYTHON:
            try:
                online_out = self.probe_custom("Application", _ONLINE_DOWNLOAD_SCRIPT)
                report["online"] = online_out
                if "ONLINE_DOWNLOAD_OK" not in online_out:
                    report["error"] = "online download 未确认成功；请在 InoProShop 内手工触发"
            except McpError as exc:
                report["online"] = f"probe 失败: {exc}"
                report["error"] = "online download 未确认成功；禁止记录为已部署或自动重试"

        return report


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------
def _first_line_starting_with(text: str, prefix: str) -> str:
    for line in (text or "").splitlines():
        if line.startswith(prefix):
            return line
    return ""


def _extract_declaration(get_pou_output: str) -> str:
    """从 get_pou_code 的返回体中抠出 Declaration 段。

    MCP 输出格式（观察多次一致）:
        Declaration:
        <text...>

        Implementation:
        <text...>
    容错：如果没找到 Implementation 分隔，就把 Declaration: 后的全部作为声明。
    """
    if not get_pou_output:
        return ""
    txt = get_pou_output
    lower = txt.lower()
    d_idx = lower.find("declaration:")
    i_idx = lower.find("implementation:")
    if d_idx < 0:
        return txt.strip()
    start = d_idx + len("declaration:")
    end = i_idx if i_idx > d_idx else len(txt)
    return txt[start:end].strip()
