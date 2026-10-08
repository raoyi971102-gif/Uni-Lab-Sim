"""共享 OPC 标量 CSV schema；不加载设备模型、GUI 或运行配置。"""
from __future__ import annotations
import csv
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from opcua import ua

log = logging.getLogger("plc-signals-csv")

def runtime_data_dir() -> Path:
    configured = os.environ.get("PLCSIM_DATA_DIR")
    if configured:
        return Path(configured).expanduser().resolve()
    return Path.home() / ".local" / "share" / "plc-sim"

def connection_state_path() -> Path:
    configured = os.environ.get("PLCSIM_CONNECTION_STATE")
    if configured:
        return Path(configured).expanduser().resolve()
    return runtime_data_dir() / "runtime" / "server-connections.json"

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
