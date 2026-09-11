"""Turn a PLC variable table into a reviewable, data-only device package."""
from __future__ import annotations
import csv, hashlib, json, re
from pathlib import Path
from typing import Any
try:
    from ..common import SZLAB_TYPE_MAP, load_csv, parse_suffix
except ImportError:
    from common import SZLAB_TYPE_MAP, load_csv, parse_suffix
try:
    import yaml
except ImportError:
    yaml = None

SCHEMA = "unilab.plc_simulation_package/v1"
ENCODINGS = ("utf-8-sig", "utf-16", "utf-16-le", "utf-8", "gbk", "gb18030")

def _read_table(path: Path):
    raw = path.read_bytes(); text = None; encoding = ""
    for candidate in ENCODINGS:
        try:
            text, encoding = raw.decode(candidate), candidate; break
        except UnicodeDecodeError: continue
    if text is None: raise ValueError(f"无法解码变量表: {path}")
    lines = text.splitlines()
    if not lines: raise ValueError("变量表为空")
    delimiter = "\t" if lines[0].count("\t") > lines[0].count(",") else ","
    reader = csv.DictReader(lines, delimiter=delimiter)
    headers = [str(h or "").strip() for h in (reader.fieldnames or [])]
    if "变量名" not in headers: raise ValueError("仅支持包含“变量名”列的 SZLab/PLC 变量表")
    return encoding, delimiter, [{str(k or "").strip(): str(v or "").strip() for k, v in row.items()} for row in reader]

def _safe_id(value: str, fallback: str = "device") -> str:
    value = re.sub(r"[^0-9A-Za-z_]+", "_", value).strip("_").lower()
    return value or fallback

def _semantic_device_group(name: str, structural: str) -> str:
    """Map PLC naming fragments to a process-level device group.

    Structure names identify ownership; these stable keywords identify the
    process device users expect to see in the OS catalog.  Unknown names keep
    their structural group and are available for AI/manual review.
    """
    text = name.replace("_", "")
    rules = (
        ("上料流程", ("上料", "加料", "供料", "喂料")),
        ("下料流程", ("下料", "卸料", "出料", "排料")),
        ("取料流程", ("取料", "取样", "取瓶")),
        ("放料流程", ("放料", "放瓶", "投料")),
        ("输送流程", ("输送", "传送", "转运")),
    )
    for group, keywords in rules:
        if any(keyword in text for keyword in keywords):
            return group
    return structural

def _dump(path: Path, payload: Any) -> None:
    path.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False) if yaml else json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

def inspect_table(source: str | Path) -> dict[str, Any]:
    path = Path(source).expanduser().resolve(); encoding, delimiter, rows = _read_table(path); parsed = load_csv(path)
    nodes=[]; seen=set(); duplicates=[]; unsupported=[]
    for index, row in enumerate(rows, 2):
        name=row.get("变量名", "").strip()
        if not name: continue
        if name in seen: duplicates.append(name); continue
        seen.add(name); raw_type=row.get("数据类型", "").strip().upper(); dtype=SZLAB_TYPE_MAP.get(raw_type, "")
        node=next((n for n in parsed if n.name_cn == name), None)
        if node is None or not dtype: unsupported.append({"name":name,"data_type":raw_type}); continue
        parts = [part for part in re.split(r"[./]", name) if part]
        # PLC 导出的结构体通常是 ``设备根.成员[索引].叶``。设备边界默认取
        # 第一个路径段；取前两段会把同一设备的每个成员结构误拆成独立设备。
        device_path = parts[0] if parts else name
        leaf_name = parts[-1] if parts else name
        role = "action" if any(token in leaf_name for token in ("触发", "开始", "请求", "下发")) else "state"
        group = _semantic_device_group(name, device_path)
        nodes.append({"name":name,"device_path":device_path,"device_group":group,"device_id":_safe_id(group),"leaf_name":leaf_name,"role":role,"data_type":dtype,"node_id":node.node_id,"cell_address":row.get("单元格地址", ""),"initial_value":row.get("初始值", ""),"comment":row.get("注释", ""),"source_row":index,"evidence_id":f"row:{index}"})
    return {"schema":"unilab.plc_evidence/v1","source":{"path":str(path),"sha256":hashlib.sha256(path.read_bytes()).hexdigest(),"encoding":encoding,"delimiter":delimiter},"nodes":nodes,"stats":{"rows":len(rows),"nodes":len(nodes),"unsupported":len(unsupported),"duplicates":len(duplicates)},"unsupported":unsupported,"duplicates":duplicates}

def build_package(source: str | Path, destination: str | Path) -> dict[str, Any]:
    evidence=inspect_table(source); output=Path(destination).expanduser().resolve(); output.mkdir(parents=True, exist_ok=True); nodes=evidence["nodes"]; channels={}; review=[]
    for node in nodes:
        parsed=parse_suffix(node["name"])
        if not parsed: review.append({"severity":"info","code":"UNCLASSIFIED_NODE","node":node["name"],"question":"该变量是状态、参数还是动作通道？"}); continue
        base, role, kind, delay=parsed; channel=channels.setdefault(base,{"channel_id":_safe_id(base),"name":base,"kind":kind,"delay_ms":delay,"nodes":{}}); channel["nodes"][role]=node["name"]
    for channel in channels.values():
        missing=[role for role in ("W","R") if role not in channel["nodes"]]
        if missing: review.append({"severity":"warning","code":"INCOMPLETE_HANDSHAKE","channel":channel["name"],"missing_roles":missing,"question":"请确认该通道的写入/完成节点及成功条件。"})
    package_id=_safe_id(Path(source).stem,"plc_table")
    manifest={"schema":SCHEMA,"package_id":f"generated.{package_id}","maturity":"L1","source_sha256":evidence["source"]["sha256"],"node_count":len(nodes),"channel_count":len(channels),"review_count":len(review)+len(evidence["unsupported"]),"runtime":{"mode":"package","data_only":True}}
    devices = []
    for device_id, group in __import__("itertools").groupby(sorted(nodes, key=lambda n: n["device_id"]), key=lambda n: n["device_id"]):
        members = list(group); devices.append({"device_id":device_id,"display_name":members[0].get("device_group", members[0]["device_path"]),"structural_paths":sorted({n["device_path"] for n in members}),"actions":[n["leaf_name"] for n in members if n["role"] == "action"],"states":[n["leaf_name"] for n in members if n["role"] == "state"],"nodes":[n["name"] for n in members]})
    manifest["device_count"] = len(devices)
    _dump(output/"manifest.yaml",manifest); _dump(output/"nodes.yaml",{"schema":"unilab.package_nodes/v1","nodes":nodes}); _dump(output/"devices.yaml",{"schema":"unilab.package_devices/v1","devices":devices}); _dump(output/"handshakes.yaml",{"schema":"unilab.package_handshakes/v1","channels":list(channels.values())}); _dump(output/"review.yaml",{"schema":"unilab.package_review/v1","questions":review,"unsupported":evidence["unsupported"],"duplicates":evidence["duplicates"]})
    (output/"evidence.json").write_text(json.dumps(evidence,ensure_ascii=False,indent=2),encoding="utf-8"); (output/"README.md").write_text("# Generated PLC-Sim device package\n\nData-only L1 package; resolve review questions before enabling behavior.\n",encoding="utf-8")
    return {"ok":True,"package":str(output),"manifest":manifest,"review":review,"evidence":evidence["stats"]}
