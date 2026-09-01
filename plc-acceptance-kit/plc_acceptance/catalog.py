"""读取 SZLab PLC CSV 点表并生成稳定节点目录。"""

from __future__ import annotations

import csv
import hashlib
from collections.abc import Iterable
from pathlib import Path

from .models import CatalogNode

TYPE_MAP = {
    "BOOL": "BOOLEAN",
    "BOOLEAN": "BOOLEAN",
    "INT": "INT16",
    "INT16": "INT16",
    "DINT": "INT32",
    "INT32": "INT32",
    "REAL": "FLOAT",
    "FLOAT": "FLOAT",
    "STRING": "STRING",
}


def _decode_csv(path: Path) -> str:
    """使用项目允许的编码读取 PLC 点表。

    参数：``path`` 是 CSV 路径。
    返回：解码后的文本。
    异常：所有受支持编码均失败时抛出 ``UnicodeError``。
    """

    for encoding in ("utf-8-sig", "utf-16", "utf-16-le", "utf-8", "gbk", "gb18030"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    raise UnicodeError(f"无法解码 PLC 点表: {path}")


def _first_present(row: dict[str, str], candidates: Iterable[str]) -> str:
    """从 CSV 行中读取第一个存在的候选列。

    参数：``row`` 是原始行，``candidates`` 是按优先级排列的列名。
    返回：清理后的字段值；没有匹配时返回空串。
    """

    normalized = {str(key or "").strip().lower(): value for key, value in row.items()}
    for candidate in candidates:
        value = normalized.get(candidate.strip().lower())
        if value is not None:
            return str(value).strip()
    return ""


def normalize_data_type(raw_type: str) -> str:
    """把 PLC 厂商类型规范化为验收类型。

    参数：``raw_type`` 是 CSV 数据类型。
    返回：``BOOLEAN/INT16/INT32/FLOAT/STRING`` 或空串；数组和结构体返回空串。
    """

    value = raw_type.strip().upper()
    if "[" in value or value.startswith("ST_"):
        return ""
    return TYPE_MAP.get(value, "")


def load_catalog(path: str | Path, *, node_id_prefix: str) -> dict[str, CatalogNode]:
    """从点表发现全部可表示的标量节点。

    参数：``path`` 是点表路径；``node_id_prefix`` 用于没有显式 NodeId 的变量。
    返回：以中文变量名为键的节点目录。
    异常：同名节点的类型或 NodeId 冲突时抛出 ``ValueError``。
    """

    csv_path = Path(path).resolve()
    text = _decode_csv(csv_path)
    first_line = text.splitlines()[0] if text.splitlines() else ""
    delimiter = "\t" if first_line.count("\t") > first_line.count(",") else ","
    reader = csv.DictReader(text.splitlines(), delimiter=delimiter)
    catalog: dict[str, CatalogNode] = {}
    for row in reader:
        name = _first_present(row, ("变量名", "name", "namecn"))
        data_type = normalize_data_type(
            _first_present(row, ("数据类型", "datatype", "data_type"))
        )
        if not name or not data_type:
            continue
        explicit_node_id = _first_present(row, ("node_id", "nodeid"))
        node_id = explicit_node_id or f"{node_id_prefix}{name}"
        node = CatalogNode(name=name, data_type=data_type, node_id=node_id)
        previous = catalog.get(name)
        if previous is not None and previous != node:
            raise ValueError(f"点表同名节点定义冲突: {name}")
        catalog[name] = node
    return catalog


def catalog_fingerprint(nodes: Iterable[CatalogNode]) -> str:
    """计算与行顺序无关的节点目录指纹。

    参数：``nodes`` 是点表标量节点集合。
    返回：节点名、类型和 NodeId 的 SHA-256 十六进制摘要。
    """

    payload = "\n".join(
        f"{node.name}\t{node.data_type}\t{node.node_id}"
        for node in sorted(nodes, key=lambda item: (item.node_id, item.name))
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
