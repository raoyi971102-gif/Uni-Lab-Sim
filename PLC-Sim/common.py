"""
XUSE OPC UA 仿真项目 —— 公共工具模块
==========================================================================
被 server.py 与 szlab_handshake_agent.py 共用：
  - 日志初始化 setup_logging()
  - CSV 数据结构 NodeDef 与 load_csv()
  - 数据类型映射表 VTYPE_MAP / DEFAULT_MAP
  - 握手节点分类正则 _HS_PATTERNS + parse_suffix()
  - Type-A 位置节点匹配 _POS_PATTERNS + match_pos_node()
  - YAML 配置读取 load_yaml()
"""

from __future__ import annotations

import csv
import hashlib
import json
import logging
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from opcua import ua

try:
    import yaml  # PyYAML
except ImportError:  # pragma: no cover
    yaml = None


# ---------------------------------------------------------------------------
# 日志
# ---------------------------------------------------------------------------
def setup_logging(name: str = "XUSE") -> logging.Logger:
    """初始化日志格式；opcua 库自身噪声降为 WARNING。"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s.%(msecs)03d [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("opcua").setLevel(logging.WARNING)
    return logging.getLogger(name)


log = logging.getLogger("XUSE-common")


# ---------------------------------------------------------------------------
# 可移植路径
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent
BUILTIN_DEMO_CSV = PROJECT_ROOT / "data" / "demo_variables.csv"


def runtime_data_dir() -> Path:
    """Return a writable data directory for uploads, extracts, and runtime state."""
    configured = os.environ.get("PLCSIM_DATA_DIR")
    if configured:
        return Path(configured).expanduser().resolve()

    # Source checkouts keep established repository-local paths. Installed
    # wheels do not ship pyproject.toml and must never write into site-packages.
    if (PROJECT_ROOT / "pyproject.toml").is_file():
        return PROJECT_ROOT / "data"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "PLC-Sim"
    if os.name == "nt":
        windows_root = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        if windows_root:
            return Path(windows_root).expanduser() / "PLC-Sim"
        return Path.home() / "PLC-Sim"
    xdg_root = os.environ.get("XDG_DATA_HOME")
    if xdg_root:
        return Path(xdg_root).expanduser() / "plc-sim"
    return Path.home() / ".local" / "share" / "plc-sim"


def default_csv_path() -> Path:
    """返回默认变量表；可用 PLCSIM_CSV 环境变量覆盖。"""
    configured = os.environ.get("PLCSIM_CSV")
    if configured:
        return Path(configured).expanduser().resolve()
    return BUILTIN_DEMO_CSV


def connection_state_path() -> Path:
    """返回 Server 与 GUI 共享的连接状态文件路径。"""
    configured = os.environ.get("PLCSIM_CONNECTION_STATE")
    if configured:
        return Path(configured).expanduser().resolve()
    return runtime_data_dir() / "runtime" / "server-connections.json"


# ---------------------------------------------------------------------------
# 数据类型映射（CSV → opcua）
# ---------------------------------------------------------------------------
VTYPE_MAP: Dict[str, ua.VariantType] = {
    "BOOLEAN": ua.VariantType.Boolean,
    "BYTE":    ua.VariantType.Byte,
    "INT16":   ua.VariantType.Int16,
    "INT32":   ua.VariantType.Int32,
    "FLOAT":   ua.VariantType.Float,
    "DOUBLE":  ua.VariantType.Double,
    "STRING":  ua.VariantType.String,
}
SZLAB_TYPE_MAP: Dict[str, str] = {
    "BOOL": "BOOLEAN",
    "INT": "INT16",
    "DINT": "INT32",
    "REAL": "FLOAT",
    "STRING": "STRING",
}
DEFAULT_MAP: Dict[str, Any] = {
    "BOOLEAN": False,
    "BYTE":    0,
    "INT16":   0,
    "INT32":   0,
    "FLOAT":   0.0,
    "DOUBLE":  0.0,
    "STRING":  "",
}

PTLC_TYPE_MAP: Dict[str, str] = {
    "BOOLEAN": "BOOLEAN",
    "BYTE": "BYTE",
    "INT16": "INT16",
    "INT32": "INT32",
    "FLOAT": "FLOAT",
    "DOUBLE": "DOUBLE",
    "STRING": "STRING",
}


# ---------------------------------------------------------------------------
# 节点定义
# ---------------------------------------------------------------------------
@dataclass
class NodeDef:
    name_cn: str          # 中文名，来自 CSV `Name`
    name_en: str          # 英文名，来自 CSV `EnglishName`
    node_type: str        # VARIABLE / METHOD
    data_type: str        # BOOLEAN / INT16 / ...
    node_id: str          # ns=4;s=uniab|<name_cn>
    array_len: int = 0    # 0=标量，>0=固定长度真数组
    browse_path: Tuple[str, ...] = ()  # Objects 下的父对象 BrowseName 路径
    initial_value: Any = None
    # 写所有权只约束 PLC-Sim 的维护 API，不改变 OPC UA AccessLevel。握手代理代表
    # PLC 扫描逻辑，仍须能写 PLC 输出；普通 GUI 则不得成为第二个 PLC 输出写者。
    write_owner: str = "shared"  # shared / host / plc / maintenance


def node_defs_fingerprint(nodes: Sequence[NodeDef]) -> str:
    """为变量定义生成稳定指纹，用于按 CSV 恢复前端监控列表。

    指纹按 NodeId 排序后计算，因此相同变量表即使文件路径、编码或行顺序不同，
    仍会得到同一个标识；变量名、类型或 NodeId 变化则会得到新标识。
    """
    normalized = sorted(
        (
            node.node_id,
            node.name_cn,
            node.name_en,
            node.node_type,
            node.data_type,
            node.array_len,
            node.browse_path,
            node.write_owner,
        )
        for node in nodes
    )
    payload = json.dumps(
        normalized,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def load_csv(path: Path) -> List[NodeDef]:
    """读取 PLC-Sim 或 SZLab PLC CSV，只保留可表示的标量 VARIABLE 节点。"""
    encodings = ("utf-8-sig", "utf-16", "utf-16-le", "utf-8", "gbk", "gb18030")
    text: Optional[str] = None
    for enc in encodings:
        try:
            text = path.read_text(encoding=enc)
            log.info("CSV 使用编码读取成功: %s (%s)", enc, path)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        raise RuntimeError(f"无法用常见编码读取 CSV: {path}")

    first_line = text.splitlines()[0] if text.splitlines() else ""
    delimiter = "\t" if first_line.count("\t") > first_line.count(",") else ","
    reader = csv.DictReader(text.splitlines(), delimiter=delimiter)
    fieldnames = [str(name or "").strip() for name in (reader.fieldnames or [])]
    szlab_schema = "变量名" in fieldnames
    nodes: List[NodeDef] = []
    seen: set = set()
    for row in reader:
        if szlab_schema:
            name_cn = (row.get("变量名") or "").strip()
            name_en = ""
            ntype = "VARIABLE"
            raw_dtype = (row.get("数据类型") or "").strip().upper()
            # 结构体和数组由 PLC CSV 逐层展开；只创建最终标量叶节点。
            dtype = SZLAB_TYPE_MAP.get(raw_dtype, "")
            node_id_field = next(
                (
                    key
                    for key in row
                    if str(key or "").strip().lower() in {"node_id", "nodeid"}
                ),
                None,
            )
            nid = (row.get(node_id_field) or "").strip() if node_id_field else ""
        else:
            name_cn = (row.get("Name") or "").strip()
            name_en = (row.get("EnglishName") or "").strip()
            ntype = (row.get("NodeType") or "VARIABLE").strip().upper()
            dtype = (row.get("DataType") or "BOOLEAN").strip().upper()
            nid = (row.get("NodeId") or "").strip()

        if not name_cn or name_cn in seen:
            continue
        seen.add(name_cn)

        if ntype != "VARIABLE":
            log.debug("跳过非 VARIABLE 节点: %s", name_cn)
            continue
        if not dtype or dtype not in VTYPE_MAP:
            if szlab_schema:
                log.debug("跳过 SZLab 非标量类型 %r（%s）", row.get("数据类型"), name_cn)
                continue
            log.warning("未知数据类型 %r（%s），跳过", dtype, name_cn)
            continue

        if not nid:
            prefix = "上位机通讯|" if szlab_schema else "uniab|"
            nid = f"ns=4;s={prefix}{name_cn}"

        nodes.append(NodeDef(name_cn, name_en, ntype, dtype, nid))
    log.info("CSV 解析完成：共 %d 个 VARIABLE 节点", len(nodes))
    return nodes


def load_ptlc_nodes(path: Path, ns_index: int = 4) -> List[NodeDef]:
    """读取自包含的 PTLC ``plc_nodes.yaml`` 协议快照。

    文件格式与 PTLC V2 的节点表兼容，但 PLC-Sim 只消费 ``gvl_path``、
    ``nodes.*.type`` 和 ``array_len``，因此运行时无需安装或导入 PTLC。
    """
    if yaml is None:
        raise RuntimeError("PTLC profile 需要 PyYAML")
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    browse_path = tuple(str(part) for part in payload.get("gvl_path", ()))
    if not browse_path:
        raise ValueError(f"PTLC 节点表缺少 gvl_path: {path}")
    raw_nodes = payload.get("nodes")
    if not isinstance(raw_nodes, dict) or not raw_nodes:
        raise ValueError(f"PTLC 节点表缺少 nodes: {path}")

    result: List[NodeDef] = []
    for name, raw_spec in raw_nodes.items():
        spec = raw_spec if isinstance(raw_spec, dict) else {"type": raw_spec}
        raw_type = str(spec.get("type", "")).upper()
        data_type = PTLC_TYPE_MAP.get(raw_type)
        if data_type is None:
            raise ValueError(f"PTLC 节点 {name} 使用未知类型: {raw_type!r}")
        array_len = int(spec.get("array_len", 0) or 0)
        if array_len < 0:
            raise ValueError(f"PTLC 节点 {name} 的 array_len 不能为负数")
        initial = spec.get("initial_value")
        comment = str(spec.get("comment", ""))
        result.append(NodeDef(
            name_cn=str(name),
            name_en=comment,
            node_type="VARIABLE",
            data_type=data_type,
            node_id=f"ns={ns_index};s=ptlc|{name}",
            array_len=array_len,
            browse_path=browse_path,
            initial_value=initial,
            write_owner=str(spec.get("write_owner") or _infer_ptlc_write_owner(
                str(name), comment
            )),
        ))
    log.info("PTLC 节点表解析完成：%d 个节点，GVL=%s", len(result), "/".join(browse_path))
    return result


_PTLC_L2_HOST_FIELDS = frozenset({"ActionCode", "RequestSeq", "Start", "Reset"})
_PTLC_L2_PLC_FIELDS = frozenset({
    "State", "ActiveCode", "AcceptedSeq", "CompletedSeq", "Step",
    "ErrorCode", "SafeState", "Retryable",
})


def _infer_ptlc_write_owner(name: str, comment: str) -> str:
    """从 PTLC 稳定命名/注释推导 GUI 写所有权。

    未明确归属的变量保留 ``maintenance``，避免把历史调试量误判为业务输入；
    CSV/SZLab 路径不调用本函数，继续维持原来的完全可写行为。
    """
    l2_match = re.match(
        r"^(Sampling|Collect|Develop|PhotoScrape|FeedLift|Pump|Rail|StagingA)_L2_(.+)$",
        name,
    )
    if l2_match:
        field = l2_match.group(2)
        if field in _PTLC_L2_HOST_FIELDS:
            return "host"
        if field in _PTLC_L2_PLC_FIELDS:
            return "plc"
    if name in {
        "PLC_Deploy_RequestSeq", "PLC_Deploy_CommitSeq",
        "PLC_Deploy_Start", "PLC_Deploy_Reset",
    }:
        return "host"
    if name in {
        "PLC_Deploy_State", "PLC_Deploy_AcceptedSeq", "PLC_Deploy_ErrorCode",
        "PLC_Startup_State", "PLC_Startup_ErrorCode", "PLC_Ready",
        "PLC_Startup_AlarmInhibit", "PLC_HandWheel_Active",
        "PLC_Axis_CommOperational", "PLC_Axis_FaultSource", "PLC_Axis_FaultCode",
    }:
        return "plc"
    normalized = comment.replace("：", ":").replace("仅 ", "仅")
    if re.search(r"(?:仅)?(?:PC|PC/HMI|上位机|请求方)\s*写", normalized, re.I):
        return "host"
    if re.search(r"(?:仅)?PLC\s*写", normalized, re.I):
        return "plc"
    return "maintenance"


def load_csvs(csv_paths: List[Path]) -> List[NodeDef]:
    """批量加载并跨表去重（后加载的同名节点会被跳过）。"""
    node_defs: List[NodeDef] = []
    seen: set = set()
    for cp in csv_paths:
        for nd in load_csv(cp):
            if nd.name_cn in seen:
                log.debug("跨 CSV 去重: %s", nd.name_cn)
                continue
            seen.add(nd.name_cn)
            node_defs.append(nd)
    log.info("合并后共 %d 个 VARIABLE 节点", len(node_defs))
    return node_defs


# ---------------------------------------------------------------------------
# 握手分类器 —— 根据后缀识别节点角色
# ---------------------------------------------------------------------------
# CSV 里的中文命名是无下划线连接的（例如 "工站初始化"、"机械臂初始化_1"），
# 通道号才用下划线；因此不能用简单的 endswith("_初始化") 匹配。
_HS_PATTERNS = [
    # role_key,  regex,                                              kind,        default_delay_ms
    ("param_R",  re.compile(r"^(.+?)参数下发完成(_\d+)?$"),         "param_C",   200),
    ("param_W",  re.compile(r"^(.+?)参数下发(_\d+)?$"),             "param_C",   200),
    ("init_R",   re.compile(r"^(.+?)初始化完成(_\d+)?$"),           "init_D",    600),
    ("init_W",   re.compile(r"^(.+?)初始化(_\d+)?$"),               "init_D",    600),
    ("proc_R",   re.compile(r"^(.+?)加工完成(_\d+)?$"),             "process_B", 1500),
    ("proc_W",   re.compile(r"^(.+?)开始加工(_\d+)?$"),             "process_B", 1500),
    ("action_R", re.compile(r"^(.+?)动作完成(_\d+)?$"),             "action_A",  1200),
    ("action_W", re.compile(r"^(.+?)动作触发(_\d+)?$"),             "action_A",  1200),
    ("req",      re.compile(r"^(.+?)请求加工(_\d+)?$"),             "process_B", 0),
]


def parse_suffix(name_cn: str):
    """返回 (base, role, kind, delay_ms) 或 None。base 会带上通道后缀 (如 '机械臂_1')。"""
    for role_key, pat, kind, delay in _HS_PATTERNS:
        m = pat.match(name_cn)
        if not m:
            continue
        base = m.group(1) + (m.group(2) or "")
        if role_key.endswith("_R"):
            role = "R"
        elif role_key == "req":
            role = "REQ"
        else:
            role = "W"
        return (base, role, kind, delay)
    return None


# Type-A 编码触发的联动节点
_POS_PATTERNS = {
    "target_pos_node":   re.compile(r"^(.+?)目标位置代码(_\d+)?$"),
    "target_pick_node":  re.compile(r"^(.+?)目标取放代码(_\d+)?$"),
    "current_pos_node":  re.compile(r"^(.+?)当前位置(_\d+)?$"),
    "current_pick_node": re.compile(r"^(.+?)当前取放料(_\d+)?$"),
}


def match_pos_node(name_cn: str, hs_base: str) -> Optional[str]:
    """如果 name_cn 属于 hs_base 对应的位置节点，返回属性名；否则 None。"""
    for attr, pat in _POS_PATTERNS.items():
        m = pat.match(name_cn)
        if not m:
            continue
        base = m.group(1) + (m.group(2) or "")
        if base == hs_base:
            return attr
    return None


# "_占位 / _空闲" 类节点（Type-B 前置条件的仿真开关）
OCC_RE = re.compile(r"(占位|空闲)(_\d+)?$")


# ---------------------------------------------------------------------------
# YAML 配置
# ---------------------------------------------------------------------------
def load_yaml(path: Optional[str]) -> Dict[str, Any]:
    if not path:
        return {}
    if yaml is None:
        log.warning("未安装 PyYAML，忽略 --config")
        return {}
    p = Path(path)
    if not p.exists():
        log.warning("配置文件不存在: %s", p)
        return {}
    with p.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}
