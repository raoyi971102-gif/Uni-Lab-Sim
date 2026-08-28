"""PLC 变量「OPC 符号导出」pragma 解析与编辑
=============================================
功能:
    纯文本处理 CODESYS GVL/POU 声明里的 `{attribute 'symbol' := 'readwrite'}` pragma ——
    它是把变量纳入 OPC UA 符号导出集的官方机制 (编译时并入符号集, 全下载即暴露)。
    本模块只做声明文本的解析与最小增删, 供 PlcProgramService 经 worker 的 read/write 落地,
    使「把变量发布到 OPC」可在 web /plc 页完成, 不必回 InoProShop 勾符号配置。

    不脚本化 CODESYS 符号配置对象 (SP11 该 API 受限/脆弱); 改用 pragma + 复用既有 op_write。

设计:
    逐行扫描声明文本。变量声明行 = 行首缩进 + 标识符 + 可选 `AT %地址` + ':' + 类型 + ';',
    且不在 `//`///` 行注释 / `(* *)` 块注释 / `{...}` pragma 行内。某变量是否已导出, 看其声明行
    上方是否紧邻 `{attribute 'symbol' ...}` pragma 行。增删只插入/删除该 pragma 行, 其余文本逐字不动。
"""

from __future__ import annotations

import re

# 写入用的标准符号导出 pragma (读写权限)
SYMBOL_PRAGMA = "{attribute 'symbol' := 'readwrite'}"

# 变量声明行: 缩进(1) + 标识符(2) + 可选 AT %地址 + ':' + 类型(3, 到 ':=' 或 ';' 前) + ';'
# %[^\s:]+ 让 AT 地址在遇到声明冒号前停下 (避免吞掉冒号)
_DECL_RE = re.compile(
    r"^(\s*)([A-Za-z_]\w*)\s*(?:AT\s+%[^\s:]+\s*)?:\s*([^;]+?)\s*;")
# 符号导出 pragma 行 (属性名大小写不敏感, 允许 'readwrite'/'read'/'write')
_SYMBOL_PRAGMA_RE = re.compile(r"^\s*\{\s*attribute\s+'symbol'", re.IGNORECASE)
# 任意 pragma/属性行 (列举时跳过, 不当变量)
_PRAGMA_RE = re.compile(r"^\s*\{")
# 行注释 (// 或 ///)
_LINE_COMMENT_RE = re.compile(r"^\s*//")
# IEC 声明区段关键字行 (非变量)
_SECTION_RE = re.compile(
    r"^\s*(VAR_GLOBAL|VAR_INPUT|VAR_OUTPUT|VAR_IN_OUT|VAR_TEMP|VAR_STAT|VAR|END_VAR|TYPE|END_TYPE|STRUCT|END_STRUCT)\b",
    re.IGNORECASE)


def _strip_block_comments_flags(lines):
    """逐行标注是否处于 `(* *)` 块注释内 (该行起始即在块注释中则为 True)。

    参数:
        lines: 行文本列表 (不含换行符也可)
    返回:
        list[bool], 与 lines 等长, 第 i 项表示第 i 行"开始时"是否已在块注释内
    """
    flags = []
    in_block = False
    for line in lines:
        flags.append(in_block)
        idx = 0
        # 在本行内推进块注释开闭状态 (供下一行的起始标志)
        while idx < len(line):
            if not in_block:
                start = line.find("(*", idx)
                if start < 0:
                    break
                in_block = True
                idx = start + 2
            else:
                end = line.find("*)", idx)
                if end < 0:
                    idx = len(line)
                else:
                    in_block = False
                    idx = end + 2
    return flags


def _is_declaration(line: str, in_block: bool):
    """判断一行是否为变量声明行; 是则返回 (name, type), 否则 None。

    参数:
        line: 单行文本 (不含换行符); in_block: 该行起始是否在 `(* *)` 块注释内
    返回:
        (变量名, 类型字符串) 或 None
    """
    if in_block:
        return None
    if _LINE_COMMENT_RE.match(line) or _PRAGMA_RE.match(line) or _SECTION_RE.match(line):
        return None
    m = _DECL_RE.match(line)
    if not m:
        return None
    name = m.group(2)
    vtype = m.group(3).strip()
    # 类型里若带初值 (:=) 只取冒号后到 := 前的纯类型, 供显示
    if ":=" in vtype:
        vtype = vtype.split(":=", 1)[0].strip()
    return name, vtype


def parse_symbols(declaration: str) -> list[dict]:
    """解析声明文本, 列出每个变量及其当前是否已标记 OPC 符号导出。

    参数:
        declaration: GVL/POU 的完整声明文本
    返回:
        list[dict], 每项 {"name": str, "type": str, "exported": bool}; 按出现顺序
    """
    lines = declaration.splitlines()
    block_flags = _strip_block_comments_flags(lines)
    out: list[dict] = []
    for i, raw in enumerate(lines):
        parsed = _is_declaration(raw, block_flags[i])
        if parsed is None:
            continue
        name, vtype = parsed
        # 向上跳过空行后, 看紧邻的非空行是否为 symbol pragma
        exported = False
        j = i - 1
        while j >= 0 and lines[j].strip() == "":
            j -= 1
        if j >= 0 and _SYMBOL_PRAGMA_RE.match(lines[j]):
            exported = True
        out.append({"name": name, "type": vtype, "exported": exported})
    return out


def _find_decl_index(lines: list[str], block_flags: list[bool], name: str):
    """定位某变量声明行的下标; 未找到返回 -1。"""
    for i, raw in enumerate(lines):
        parsed = _is_declaration(raw, block_flags[i])
        if parsed is not None and parsed[0] == name:
            return i
    return -1


def set_symbol_pragma(declaration: str, name: str, enabled: bool) -> str:
    """在变量 name 声明行上方插入或删除 symbol 导出 pragma, 返回完整新声明。

    功能:
        enabled=True  -> 若未导出, 在声明行上方插入一行 `{attribute 'symbol' := 'readwrite'}`
                         (缩进与换行符对齐声明行)。
        enabled=False -> 若已导出, 删除声明行上方紧邻的 symbol pragma 行。
        其余文本逐字保留 (按原换行符重组)。
    参数:
        declaration: 完整声明文本; name: 目标变量名; enabled: 目标导出态
    返回:
        新的完整声明文本
    异常:
        ValueError: 声明中未找到该变量
    """
    # keepends 保留每行原换行符, 重组后与原文逐字一致 (仅在目标处增删)
    lines_ke = declaration.splitlines(keepends=True)
    lines = [ln.splitlines()[0] if ln.splitlines() else "" for ln in lines_ke]
    block_flags = _strip_block_comments_flags(lines)
    idx = _find_decl_index(lines, block_flags, name)
    if idx < 0:
        raise ValueError(f"声明中未找到变量: {name}")

    # 上方紧邻的非空行是否已是 symbol pragma (跳过空行)
    j = idx - 1
    while j >= 0 and lines[j].strip() == "":
        j -= 1
    already = (j >= 0 and bool(_SYMBOL_PRAGMA_RE.match(lines[j])))

    if enabled and not already:
        indent_match = re.match(r"^(\s*)", lines_ke[idx])
        indent = indent_match.group(1) if indent_match else ""
        # 用声明行同款换行符; 末行可能无换行符则退 '\n'
        eol = lines_ke[idx][len(lines[idx]):] or "\n"
        pragma_line = f"{indent}{SYMBOL_PRAGMA}{eol}"
        lines_ke.insert(idx, pragma_line)
    elif (not enabled) and already:
        # 删除紧邻的 symbol pragma 行 (j 即其下标)
        lines_ke.pop(j)

    return "".join(lines_ke)
