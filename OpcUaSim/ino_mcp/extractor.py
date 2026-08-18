"""
ino_mcp.extractor —— 从 InoProShop 项目提取 OPC UA 变量到 CSV
============================================================================
思路：
    1) get_project_structure 拿到项目对象树 → 识别所有 GVL 相对路径
    2) 逐个 read_gvl_declaration 拿 VAR_GLOBAL ... END_VAR 声明文本
    3) 并行拿项目里所有 DUT 声明 (TYPE ST_XX : STRUCT ... END_TYPE)，
       解析成 registry: {DUT名: [(字段名, IEC 类型, 注释), ...]}
    4) 对每个 GVL 顶层变量递归展开：
         - ARRAY [a..b] OF X → 迭代下标 (支持多维)
         - STRUCT / DUT 类型 → 深入字段
       生成叶子节点 name 形如 `马弗炉_写[1].自动开门温度写`
    5) 写 CSV: Name / EnglishName / NodeType / DataType / NodeLanguage / NodeId
       NodeId = ns=<ns>;s=<prefix><叶子 name>

NodeId 格式对齐真实 xuse_variables.csv:
    ns=4;s=uniab|<PLC 里变量原始名>
其中 ns_index / ns_prefix 是 InoProShop Symbols 配置里的应用命名空间前缀,
在这里通过参数暴露, 默认 (4, "uniab|") —— unilabos 项目约定;
"""

from __future__ import annotations

import csv
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .toolkit import InoToolkit


log = logging.getLogger("ino_mcp.extractor")


# ---------------------------------------------------------------------------
# IEC 61131-3 类型 → OpcUaSim CSV DataType 映射
# ---------------------------------------------------------------------------
_IEC_TO_CSV: Dict[str, str] = {
    "BOOL":     "BOOLEAN",
    "SINT":     "INT16",
    "USINT":    "INT16",
    "INT":      "INT16",
    "UINT":     "INT16",
    "WORD":     "INT16",
    "BYTE":     "INT16",
    "DINT":     "INT32",
    "UDINT":    "INT32",
    "DWORD":    "INT32",
    "LINT":     "INT32",
    "ULINT":    "INT32",
    "LWORD":    "INT32",
    "REAL":     "FLOAT",
    "LREAL":    "FLOAT",
    "STRING":   "STRING",
    "WSTRING":  "STRING",
    "TIME":     "INT32",
    "DATE":     "INT32",
    "DT":       "INT32",
    "TOD":      "INT32",
}


def iec_to_csv_type(iec_type: str) -> str:
    """把 IEC 类型字符串 (可能带 (80) 长度) 映射到 OpcUaSim CSV 里的 DataType。
    未知类型回落到 STRING，方便后续手工修正。
    """
    base = iec_type.split("(", 1)[0].strip().upper()
    return _IEC_TO_CSV.get(base, "STRING")


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------
@dataclass
class GvlVariable:
    """从 GVL 声明段解析出的原始顶层变量 (未展开)。"""
    gvl_path: str          # e.g. "Application/GVL_XUSE"
    name: str              # 原始 IEC 变量名 (可能是中文)
    iec_type: str          # 原始 IEC 类型串, 可能是 ARRAY / DUT / 基本类型
    comment: str = ""      # 行尾 (* ... *) 或 // ...
    symbol_mode: str = ""  # "readwrite" / "readonly" / "write" / ""


@dataclass
class FlatLeaf:
    """递归展开后的一个叶子节点 (对应真实 OPC UA server 里一个 VARIABLE)。"""
    name: str              # 完整访问路径, e.g. "马弗炉_写[1].自动开门温度写"
    iec_type: str          # 叶子的 IEC 基础类型 (如 "INT", "BOOL", "STRING(80)")
    comment: str = ""
    symbol_mode: str = ""


DutField = Tuple[str, str, str]                       # (字段名, IEC 类型, 注释)
DutRegistry = Dict[str, List[DutField]]               # {DUT名: [(字段, 类型, 注释), ...]}


# ---------------------------------------------------------------------------
# get_project_structure 输出解析
# ---------------------------------------------------------------------------
_INDENT_RE = re.compile(r"^([ \t\-\|>]*)(.*)$")
# 抓行首对象名: 允许标识符里出现空格 (如 "Plc Logic"), 到括号/大括号/冒号截止
_NODE_NAME_RE = re.compile(r"^([^\(\{\:]+?)\s*(?=[\(\{\:]|$)")


def _normalize_to_app_relative(full_path: str) -> str:
    """`Unnamed/Device/Plc Logic/Application/GVL_X` → `Application/GVL_X`。

    MCP 的 get_pou_code / set_pou_code 等工具期望的 pouPath 是从
    Application 段起算的相对路径 (bundle.min.js 里 schema 示例就是
    'Application/MyPOU'). 找不到就回退原路径。
    """
    parts = [p for p in full_path.split("/") if p]
    for i, p in enumerate(parts):
        if p.strip().lower() == "application":
            return "/".join(parts[i:])
    return full_path


def find_gvl_paths(structure_text: str) -> List[str]:
    """从 get_project_structure 输出识别所有 GVL 相对 Application 的路径。"""
    if not structure_text:
        return []

    hits: List[str] = []
    stack: List[Tuple[int, str]] = []
    for raw_line in structure_text.splitlines():
        m = _INDENT_RE.match(raw_line)
        indent = len(m.group(1)) if m else 0
        body = (m.group(2) if m else raw_line).strip()
        if not body:
            continue

        nm = _NODE_NAME_RE.match(body)
        if not nm:
            continue
        name = nm.group(1).strip()
        if not name:
            continue

        while stack and stack[-1][0] >= indent:
            stack.pop()
        stack.append((indent, name))
        cur_path = "/".join(n for _, n in stack)

        upper = name.upper()
        if ("GVL" in upper) and (upper != "APPLICATION"):
            rel = _normalize_to_app_relative(cur_path)
            hits.append(rel)
            log.debug("[extractor] GVL 命中: %s (原始: %s)", rel, cur_path)

    seen: set = set()
    uniq: List[str] = []
    for p in hits:
        if p not in seen:
            seen.add(p)
            uniq.append(p)
    log.info("[extractor] 识别到 %d 个 GVL 路径: %s", len(uniq), uniq)
    return uniq


# ---------------------------------------------------------------------------
# 通用: 从声明文本里逐字段解析 (共用给 GVL / DUT)
# ---------------------------------------------------------------------------
_LINE_COMMENT_RE = re.compile(r"//(.*)$")
_BLOCK_COMMENT_RE = re.compile(r"\(\*(.*?)\*\)", re.DOTALL)
_ATTR_SYMBOL_RE = re.compile(
    r"\{attribute\s+'symbol'\s*:=\s*'(readwrite|readonly|write|none)'\s*\}",
    re.IGNORECASE,
)
# 匹配 `name, name2 : TYPE ( := 初值)?` 一整条 (不含结尾 ;)
_VAR_DECL_RE = re.compile(
    r"^\s*([\w\u4e00-\u9fffA-Za-z_][\w\u4e00-\u9fffA-Za-z_,\s]*?)\s*:\s*"
    r"(.+?)\s*(?::=\s*[^;]+?)?\s*$",
    re.DOTALL,
)


def _iter_field_lines(body: str) -> Iterable[Tuple[str, str, str]]:
    """把去掉块头 (VAR_GLOBAL/VAR/STRUCT) 的文本按 `;` 切分出 (语句, 注释, 属性)。

    产出: (statement_without_comment, tail_comment, symbol_mode_or_empty)
    """
    pending_symbol_mode = ""
    buf = ""
    for raw in body.splitlines():
        stripped = raw.strip()
        if not stripped:
            continue

        attr_m = _ATTR_SYMBOL_RE.search(stripped)
        if attr_m:
            pending_symbol_mode = attr_m.group(1).lower()
            continue

        # 纯注释行不给下一条挂 comment
        if stripped.startswith("//"):
            continue
        if stripped.startswith("(*") and stripped.endswith("*)") and stripped.count("(*") == 1:
            continue

        remaining = raw
        while ";" in remaining:
            pre, remaining = remaining.split(";", 1)

            pre_block = " ".join(m.group(1).strip() for m in _BLOCK_COMMENT_RE.finditer(pre))
            pre_clean = _BLOCK_COMMENT_RE.sub(" ", pre)
            pre_line_com = ""
            plc = _LINE_COMMENT_RE.search(pre_clean)
            if plc:
                pre_line_com = plc.group(1).strip()
                pre_clean = _LINE_COMMENT_RE.sub("", pre_clean)

            tail_scope = remaining.split(";", 1)[0]
            tail_comment = ""
            tbm = _BLOCK_COMMENT_RE.search(tail_scope)
            if tbm:
                tail_comment = tbm.group(1).strip()
                remaining = remaining.replace(tbm.group(0), " ", 1)
            else:
                tlm = _LINE_COMMENT_RE.search(tail_scope)
                if tlm:
                    tail_comment = tlm.group(1).strip()
                    remaining = _LINE_COMMENT_RE.sub("", remaining, count=1)

            stmt_full = (buf + " " + pre_clean).strip()
            comment = tail_comment or pre_block or pre_line_com
            yield stmt_full, comment, pending_symbol_mode
            buf = ""
            pending_symbol_mode = ""

        remaining = _BLOCK_COMMENT_RE.sub(" ", remaining)
        remaining = _LINE_COMMENT_RE.sub("", remaining)
        if remaining.strip():
            buf += " " + remaining

    if buf.strip():
        yield buf.strip(), "", pending_symbol_mode


def _strip_var_block_headers(text: str) -> str:
    """去掉 VAR_GLOBAL / END_VAR / VAR / STRUCT / END_STRUCT / TYPE / END_TYPE 头尾。"""
    kept: List[str] = []
    for ln in text.splitlines():
        s = ln.strip().upper()
        if s.startswith("VAR_GLOBAL") or s == "END_VAR":
            continue
        if s == "VAR" or s.startswith("VAR "):
            continue
        if s == "STRUCT" or s == "END_STRUCT" or s == "END_STRUCT;":
            continue
        if s.startswith("TYPE ") or s == "END_TYPE" or s == "END_TYPE;":
            continue
        kept.append(ln)
    return "\n".join(kept)


# ---------------------------------------------------------------------------
# GVL 声明文本 → 顶层变量列表
# ---------------------------------------------------------------------------
def parse_gvl_declaration(gvl_path: str, decl_text: str) -> List[GvlVariable]:
    """把一个 GVL 的声明文本切成 GvlVariable 列表 (顶层, 未展开)。"""
    if not decl_text:
        return []
    body = _strip_var_block_headers(decl_text)
    vars_out: List[GvlVariable] = []

    for stmt, comment, sym_mode in _iter_field_lines(body):
        m = _VAR_DECL_RE.match(stmt)
        if not m:
            log.debug("[extractor] 未识别的 GVL 声明: %r", stmt)
            continue
        names_str, iec_type = m.group(1), m.group(2).strip()
        for raw_name in names_str.split(","):
            n = raw_name.strip()
            if not n:
                continue
            vars_out.append(GvlVariable(
                gvl_path=gvl_path,
                name=n,
                iec_type=iec_type,
                comment=comment,
                symbol_mode=sym_mode,
            ))
    log.debug("[extractor] %s: 顶层 %d 个变量", gvl_path, len(vars_out))
    return vars_out


# ---------------------------------------------------------------------------
# DUT 声明文本 → 字段列表 (供 registry 使用)
# ---------------------------------------------------------------------------
_TYPE_HEADER_RE = re.compile(
    r"^\s*TYPE\s+([\w\u4e00-\u9fff][\w\u4e00-\u9fff]*)\s*:?\s*(EXTENDS\s+\S+)?\s*$",
    re.IGNORECASE,
)
_STRUCT_START_RE = re.compile(r"^\s*STRUCT\s*$", re.IGNORECASE)
_END_STRUCT_RE = re.compile(r"^\s*END_STRUCT\s*;?\s*$", re.IGNORECASE)
_END_TYPE_RE = re.compile(r"^\s*END_TYPE\s*;?\s*$", re.IGNORECASE)


def parse_dut_declaration(dut_text: str) -> Tuple[Optional[str], List[DutField]]:
    """解析一个 DUT 声明文本。
    支持:
        TYPE ST_XX :
        STRUCT
            fieldA : INT;
            fieldB : ARRAY[1..3] OF REAL;
        END_STRUCT
        END_TYPE
    返回 (类型名, [(字段名, IEC 类型, 注释), ...]).
    如果不是 STRUCT (比如 ENUM/UNION), 字段列表回退为空 —— 我们暂不展开。
    """
    if not dut_text:
        return None, []

    type_name: Optional[str] = None
    for raw in dut_text.splitlines():
        m = _TYPE_HEADER_RE.match(raw)
        if m:
            type_name = m.group(1).strip()
            break

    # 找 STRUCT ... END_STRUCT 之间的字段体
    lines = dut_text.splitlines()
    body_lines: List[str] = []
    in_struct = False
    for ln in lines:
        if _STRUCT_START_RE.match(ln):
            in_struct = True
            continue
        if _END_STRUCT_RE.match(ln):
            in_struct = False
            continue
        if in_struct:
            body_lines.append(ln)
    if not body_lines:
        # 不是标准 STRUCT (可能 ENUM / UNION), 我们暂不展开
        return type_name, []
    body = "\n".join(body_lines)

    fields: List[DutField] = []
    for stmt, comment, _sym in _iter_field_lines(body):
        m = _VAR_DECL_RE.match(stmt)
        if not m:
            log.debug("[extractor] 未识别的 DUT 字段: %r", stmt)
            continue
        names_str, iec_type = m.group(1), m.group(2).strip()
        for raw_name in names_str.split(","):
            n = raw_name.strip()
            if n:
                fields.append((n, iec_type, comment))
    return type_name, fields


# ---------------------------------------------------------------------------
# 递归展开: ARRAY / DUT → 叶子节点
# ---------------------------------------------------------------------------
# 匹配 ARRAY [lo..hi] OF X  以及  ARRAY [lo1..hi1, lo2..hi2] OF X (最多支持三维)
_ARRAY_RE = re.compile(
    r"^\s*ARRAY\s*\[\s*(-?\d+)\s*\.\.\s*(-?\d+)\s*"
    r"(?:,\s*(-?\d+)\s*\.\.\s*(-?\d+)\s*)?"
    r"(?:,\s*(-?\d+)\s*\.\.\s*(-?\d+)\s*)?"
    r"\]\s*OF\s+(.+?)\s*$",
    re.IGNORECASE,
)


def expand_variable(
    name: str,
    iec_type: str,
    dut_registry: DutRegistry,
    *,
    comment: str = "",
    symbol_mode: str = "",
    depth: int = 0,
    max_depth: int = 12,
) -> List[FlatLeaf]:
    """递归把一个 (name, type) 展开成叶子列表。
    展开规则:
        1. ARRAY [a..b] (,[c..d] (,[e..f])?)? OF X  →  遍历下标 name[i]/name[i,j]/name[i,j,k]
        2. dut_registry 里注册的 STRUCT 类型  →  遍历字段 name.field
        3. 其它 (基本类型 / 未知类型)  →  作为叶子输出

    对未知类型 (既不是 ARRAY 也不在 registry 里) 保守当作叶子, 不抛错;
    max_depth 防止 registry 里自引用 / 循环引用死循环。
    """
    if depth > max_depth:
        log.warning("[extractor] 展开深度超限, 截断: %s", name)
        return [FlatLeaf(name, iec_type, comment, symbol_mode)]

    t = iec_type.strip()

    # 1) ARRAY
    m = _ARRAY_RE.match(t)
    if m:
        inner = m.group(7)
        lo1, hi1 = int(m.group(1)), int(m.group(2))
        dims: List[Tuple[int, int]] = [(lo1, hi1)]
        if m.group(3) is not None:
            dims.append((int(m.group(3)), int(m.group(4))))
        if m.group(5) is not None:
            dims.append((int(m.group(5)), int(m.group(6))))

        # 迭代出所有下标组合, 生成 name[i]  /  name[i,j]  /  name[i,j,k]
        def _iter_indices(dims_left: List[Tuple[int, int]], prefix: List[int]):
            if not dims_left:
                yield tuple(prefix)
                return
            lo, hi = dims_left[0]
            for i in range(lo, hi + 1):
                for combo in _iter_indices(dims_left[1:], prefix + [i]):
                    yield combo

        out: List[FlatLeaf] = []
        for idx in _iter_indices(dims, []):
            sub_name = f"{name}[{','.join(str(i) for i in idx)}]"
            out.extend(expand_variable(
                sub_name, inner, dut_registry,
                comment=comment, symbol_mode=symbol_mode, depth=depth + 1,
            ))
        return out

    # 2) DUT STRUCT
    #    可能形如 "ST_XX", 也可能是 "ST_XX(...)"  —— 剥括号后查 registry
    base = t.split("(", 1)[0].strip()
    if base in dut_registry:
        fields = dut_registry[base]
        if not fields:
            # 空 STRUCT / ENUM / UNION —— 保守当叶子
            return [FlatLeaf(name, iec_type, comment, symbol_mode)]
        out = []
        for fname, ftype, fcomment in fields:
            fc = fcomment or comment
            out.extend(expand_variable(
                f"{name}.{fname}", ftype, dut_registry,
                comment=fc, symbol_mode=symbol_mode, depth=depth + 1,
            ))
        return out

    # 3) 基本 / 未知类型: 叶子
    return [FlatLeaf(name, iec_type, comment, symbol_mode)]


# ---------------------------------------------------------------------------
# 从 toolkit.dump_all_declarations 输出里识别 DUT
# ---------------------------------------------------------------------------
# 注意: 结尾用 `\n?===DECL_END===` 而不是 `\n===DECL_END===` —— 因为 body 可能是空,
# 空 body 时输出会是 `---BODY---\n\n===DECL_END===`, 用贪婪 `\s*\n` 会把 `\n\n` 都吃掉,
# 导致 `(.*?)\n===DECL_END===` 跨块跑到下一个块的 END, 把中间的对象整个吞掉. (曾丢 PLC_PRG)
_DECL_BLOCK_RE = re.compile(
    r"===DECL_BEGIN===\s*\n(.*?)\n---BODY---\n(.*?)\n?===DECL_END===",
    re.DOTALL,
)
# 从 header 段抽 PATH / IMPL 字段
_HDR_PATH_RE = re.compile(r"^PATH:\s*(.+)$", re.MULTILINE)
_HDR_IMPL_RE = re.compile(r"^IMPL:\s*([01])$", re.MULTILINE)


@dataclass
class EditableObject:
    """项目里一个可编辑的顶层对象 (POU / GVL / DUT), 供 GUI 列表显示。"""
    name: str                    # e.g. "GVL_XUSE"
    path: str                    # e.g. "Application/GVL_XUSE" (可传给 get_pou_code)
    kind: str                    # "POU" | "GVL" | "DUT" | "OTHER"
    has_implementation: bool     # 是否有 implementation 段 (POU 才有)
    lang: str = ""               # POU 的实现语言 (ST/LD/FBD/...) 若能识别


def _classify_declaration(body: str, has_impl: bool) -> Tuple[str, str]:
    """根据声明段文本 + 是否有实现, 判断对象类型和语言。返回 (kind, lang)。"""
    body_head = body.lstrip()[:200].upper()
    if body_head.startswith("TYPE "):
        return "DUT", ""
    if body_head.startswith("VAR_GLOBAL"):
        return "GVL", ""
    if body_head.startswith(("PROGRAM ", "FUNCTION_BLOCK ", "FUNCTION ")):
        # POU (ST 或其它); 语言暂无法从 declaration 里可靠推断
        return "POU", "ST" if has_impl else ""
    if has_impl:
        return "POU", ""
    return "OTHER", ""


# 参见 _DECL_BLOCK_RE 上的注释 —— 用 \n? 避免 GVL / 无 impl 的对象因空 body
# 触发 non-greedy 的跨块吞噬 bug
_WARM_BLOCK_RE = re.compile(
    r"===OBJ_BEGIN===\s*\n(.*?)\n---DECL---\n(.*?)\n?---IMPL---\n(.*?)\n?===OBJ_END===",
    re.DOTALL,
)


@dataclass
class WarmEntry:
    """warm_all_code 里一个对象的完整解析结果。"""
    path: str
    kind: str
    lang: str
    has_impl: bool
    declaration: str
    implementation: str


def parse_warm_dump(warm_text: str) -> List[WarmEntry]:
    """把 tk.warm_all_code() 的原始输出切成 WarmEntry 列表。

    这是「一次探针搞定三件事」的核心:
      1. editables 列表 (name/kind/path)
      2. DUT registry (从 kind==DUT 的 declaration 建)
      3. pou_code cache 预填 (path -> Declaration:\n... + Implementation:\n...)
    """
    out: List[WarmEntry] = []
    if not warm_text:
        return out
    for m in _WARM_BLOCK_RE.finditer(warm_text):
        header, dec, impl = m.group(1), m.group(2), m.group(3)
        path_m = _HDR_PATH_RE.search(header)
        impl_m = _HDR_IMPL_RE.search(header)
        if not path_m:
            continue
        full_path = path_m.group(1).strip()
        has_impl  = bool(impl_m and impl_m.group(1) == "1")
        kind, lang = _classify_declaration(dec, has_impl)
        out.append(WarmEntry(
            path=full_path, kind=kind, lang=lang, has_impl=has_impl,
            declaration=dec, implementation=impl,
        ))
    log.info("[extractor] warm_dump 解析: %d 个对象 (POU=%d, GVL=%d, DUT=%d)",
             len(out),
             sum(1 for e in out if e.kind == "POU"),
             sum(1 for e in out if e.kind == "GVL"),
             sum(1 for e in out if e.kind == "DUT"))
    return out


def build_dut_registry_from_warm(entries: Iterable[WarmEntry]) -> DutRegistry:
    """从 parse_warm_dump 的结果建 DUT registry。"""
    reg: DutRegistry = {}
    for e in entries:
        if e.kind != "DUT":
            continue
        name, fields = parse_dut_declaration(e.declaration)
        if name:
            reg[name.upper()] = fields
    log.info("[extractor] 从 warm 结果注册 %d 个 DUT", len(reg))
    return reg


def list_editables_from_dump(dump_text: str) -> List[EditableObject]:
    """从 dump_all_declarations 的原始输出里抽出可编辑对象列表 (供 GUI 树/列表用)。

    与 build_dut_registry_from_dump 共用同一份 dump 结果 —— 一次 20s 探针搞定两件事。
    """
    out: List[EditableObject] = []
    if not dump_text:
        return out
    for m in _DECL_BLOCK_RE.finditer(dump_text):
        header, body = m.group(1), m.group(2)
        path_m = _HDR_PATH_RE.search(header)
        impl_m = _HDR_IMPL_RE.search(header)
        if not path_m:
            continue
        full_path = path_m.group(1).strip()
        has_impl = bool(impl_m and impl_m.group(1) == "1")
        name = full_path.rsplit("/", 1)[-1]
        kind, lang = _classify_declaration(body, has_impl)
        out.append(EditableObject(
            name=name, path=full_path, kind=kind,
            has_implementation=has_impl, lang=lang,
        ))
    log.info("[extractor] editables: %d 个 (POU=%d, GVL=%d, DUT=%d)",
             len(out),
             sum(1 for e in out if e.kind == "POU"),
             sum(1 for e in out if e.kind == "GVL"),
             sum(1 for e in out if e.kind == "DUT"))
    return out


def build_dut_registry_from_dump(dump_text: str) -> DutRegistry:
    """从 InoToolkit.dump_all_declarations 的原始输出里, 识别所有 DUT (TYPE ... END_TYPE)
    声明, 解析成 registry。POU / GVL 那些不是 TYPE 开头的会被跳过。
    """
    registry: DutRegistry = {}
    if not dump_text:
        return registry
    count_total = 0
    for m in _DECL_BLOCK_RE.finditer(dump_text):
        count_total += 1
        body = m.group(2)
        if not re.search(r"^\s*TYPE\s+", body, re.IGNORECASE | re.MULTILINE):
            continue
        name, fields = parse_dut_declaration(body)
        if name and fields:
            registry[name] = fields
            log.debug("[extractor] DUT 注册: %s (%d 字段)", name, len(fields))
    log.info("[extractor] 从 dump (%d 块声明) 里识别到 %d 个 DUT 类型",
             count_total, len(registry))
    return registry


# ---------------------------------------------------------------------------
# 主 API: 从 InoProShop 项目一次性抽出扁平叶子节点列表
# ---------------------------------------------------------------------------
def extract_gvl_variables(
    tk: InoToolkit,
    *,
    gvl_paths: Optional[Sequence[str]] = None,
    include_all: bool = False,
    dut_registry: Optional[DutRegistry] = None,
    auto_build_dut_registry: bool = True,
) -> List[FlatLeaf]:
    """遍历项目里所有 (或指定) GVL, 展开数组/DUT, 返回叶子节点列表。

    参数:
        tk                       : 已 open_project 过的 InoToolkit
        gvl_paths                : 显式指定要抽取的 GVL 路径; None 时自动发现
        include_all              : True → 全部导出; False → 仅带 {attribute 'symbol'} 的
        dut_registry             : 显式的 DUT 类型注册表 (用于展开 STRUCT)
        auto_build_dut_registry  : 当 dut_registry 为 None 时, 自动调
                                   tk.dump_all_declarations() 拿全部声明识别 DUT。
                                   拉不到就 fallback 空 registry (只展开 ARRAY).
    """
    discovery_dump = None
    if gvl_paths is None:
        # Names such as IO or HMI_Date carry no "GVL" hint in the structure
        # output. Classify declaration blocks by VAR_GLOBAL instead; warm/dump
        # cache makes this path instantaneous after project open.
        try:
            discovery_dump = tk.dump_all_declarations()
            gvl_paths = [
                item.path
                for item in list_editables_from_dump(discovery_dump)
                if item.kind == "GVL"
            ]
        except Exception as exc:  # noqa: BLE001
            log.warning("[extractor] 声明扫描发现 GVL 失败，回退结构文本: %s", exc)
            structure = tk.get_project_structure()
            gvl_paths = find_gvl_paths(structure)

    if dut_registry is None:
        if auto_build_dut_registry:
            try:
                dump = discovery_dump or tk.dump_all_declarations()
                dut_registry = build_dut_registry_from_dump(dump)
            except Exception as exc:  # noqa: BLE001
                log.warning("[extractor] 自动拉 DUT registry 失败, 只展开 ARRAY: %s", exc)
                dut_registry = {}
        else:
            dut_registry = {}

    top_vars: List[GvlVariable] = []
    for gp in gvl_paths:
        try:
            decl = tk.read_gvl_declaration(gp)
        except Exception as exc:  # noqa: BLE001
            log.warning("[extractor] 读 %s 声明失败: %s", gp, exc)
            continue
        parsed = parse_gvl_declaration(gp, decl)
        if not include_all:
            parsed = [v for v in parsed if v.symbol_mode in ("readwrite", "readonly", "write")]
        top_vars.extend(parsed)

    leaves: List[FlatLeaf] = []
    for v in top_vars:
        leaves.extend(expand_variable(
            v.name, v.iec_type, dut_registry,
            comment=v.comment, symbol_mode=v.symbol_mode,
        ))

    log.info("[extractor] 顶层 %d 个变量 → 展开 %d 个叶子节点 (registry: %d DUT)",
             len(top_vars), len(leaves), len(dut_registry))
    return leaves


# ---------------------------------------------------------------------------
# CSV 写出
# ---------------------------------------------------------------------------
_ASCII_ONLY_RE = re.compile(r"^[\x00-\x7f]*$")


def _to_csv_rows(
    leaves: Iterable[FlatLeaf],
    *,
    ns_index: int = 4,
    ns_prefix: str = "uniab|",
    node_language: str = "Chinese",
) -> List[Dict[str, str]]:
    """把 FlatLeaf 列表映射成 OpcUaSim CSV 行。

    NodeId 格式对齐真实 InoProShop OPC UA server:
        ns=<ns_index>;s=<ns_prefix><PLC 变量原始名>
    Name         = PLC 变量原始名 (中文/英文原样)
    EnglishName  = 优先 comment (若有 ASCII), 否则回退 Name
    """
    rows: List[Dict[str, str]] = []
    for v in leaves:
        pkg_name = v.name                              # PLC 里的原始变量名, 保留中文
        comment_ascii = v.comment.strip() if _ASCII_ONLY_RE.match(v.comment or "") else ""
        english_name = comment_ascii or pkg_name
        node_id = f"ns={ns_index};s={ns_prefix}{pkg_name}"
        rows.append({
            "Name": pkg_name,
            "EnglishName": english_name,
            "NodeType": "VARIABLE",
            "DataType": iec_to_csv_type(v.iec_type),
            "NodeLanguage": node_language,
            "NodeId": node_id,
        })
    return rows


def write_csv(
    leaves: Iterable[FlatLeaf],
    out_path: str | Path,
    *,
    ns_index: int = 4,
    ns_prefix: str = "uniab|",
    node_language: str = "Chinese",
) -> Path:
    """写 OpcUaSim 兼容的 CSV。字段顺序对齐真实 xuse_variables.csv。"""
    out = Path(out_path).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    rows = _to_csv_rows(list(leaves), ns_index=ns_index,
                        ns_prefix=ns_prefix, node_language=node_language)
    fieldnames = ["Name", "EnglishName", "NodeType", "DataType", "NodeLanguage", "NodeId"]
    with out.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    log.info("[extractor] 已写出 %d 行到 %s", len(rows), out)
    return out
