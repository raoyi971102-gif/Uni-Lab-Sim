"""
XUSE OPC UA 仿真服务器（纯服务器版）
=======================================================================
职责 —— 只做 OPC UA 服务：
  1. 从 CSV 批量创建变量节点，NodeId 严格保持 ns=4;s=uniab|<中文名>
  2. 允许匿名接入（NoSecurity），驱动一键连上
  3. 可选把 "_占位 / _空闲" 类节点初值设为 TRUE，便于握手代理跑 Type-B

握手仿真已拆到独立进程 szlab_handshake_agent.py，按需另行启动。

用法：
    python server.py                             # 默认参数启动
    python server.py --port 4855 --csv my.csv
    python server.py --csv a.csv --csv b.csv     # 合并多份 CSV
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, MutableMapping

from opcua import Server, ua

try:
    from .common import (
        DEFAULT_MAP,
        VTYPE_MAP,
        NodeDef,
        OCC_RE,
        connection_state_path,
        default_csv_path,
        load_csvs,
        load_ptlc_nodes,
        setup_logging,
    )
except ImportError:  # Direct `python server.py` compatibility.
    from common import (
        DEFAULT_MAP,
        VTYPE_MAP,
        NodeDef,
        OCC_RE,
        connection_state_path,
        default_csv_path,
        load_csvs,
        load_ptlc_nodes,
        setup_logging,
    )


log = setup_logging("XUSE-Server")


# ---------------------------------------------------------------------------
# OPC UA Server 构建
# ---------------------------------------------------------------------------
def build_server(endpoint: str) -> Server:
    server = Server()
    server.set_endpoint(endpoint)
    server.set_server_name("XUSE Simulation OPC UA Server")
    server.set_security_policy([ua.SecurityPolicyType.NoSecurity])
    return server


def register_ns_padding(server: Server, target_index: int, xuse_uri: str) -> int:
    """确保 xuse URI 命名空间索引 == target_index（默认 4）。"""
    log.info("初始命名空间: %s", server.get_namespace_array())
    while len(server.get_namespace_array()) < target_index:
        placeholder = f"urn:xuse:sim:placeholder:{len(server.get_namespace_array())}"
        idx = server.register_namespace(placeholder)
        log.debug("占位命名空间 %s → ns=%d", placeholder, idx)
    idx = server.register_namespace(xuse_uri)
    log.info("XUSE 命名空间 %s → ns=%d", xuse_uri, idx)
    assert idx == target_index, f"命名空间索引不匹配: 期望 {target_index}, 实际 {idx}"
    return idx


def add_nodes(server: Server, ns_idx: int, defs: List[NodeDef]) -> Dict[str, Any]:
    """创建标量/数组变量；``browse_path`` 非空时先构造嵌套对象树。"""
    objects = server.get_objects_node()
    parents: Dict[tuple[str, ...], Any] = {(): objects}
    result: Dict[str, Any] = {}
    for nd in defs:
        variant = VTYPE_MAP[nd.data_type]
        scalar_default = DEFAULT_MAP[nd.data_type]
        default = nd.initial_value
        if default is None:
            default = [scalar_default] * nd.array_len if nd.array_len else scalar_default
        parent = objects
        partial: tuple[str, ...] = ()
        for part in nd.browse_path:
            partial += (part,)
            if partial not in parents:
                parents[partial] = parent.add_object(ns_idx, part)
            parent = parents[partial]
        try:
            nid = ua.NodeId.from_string(nd.node_id)
        except Exception:
            s_part = nd.node_id.split("s=", 1)[-1]
            nid = ua.NodeId(s_part, ns_idx, ua.NodeIdType.String)

        if nid.NamespaceIndex != ns_idx:
            log.warning("CSV NodeId ns=%d 与目标 ns=%d 不一致 (%s)，已改写",
                        nid.NamespaceIndex, ns_idx, nd.name_cn)
            nid = ua.NodeId(nid.Identifier, ns_idx, nid.NodeIdType)

        var = parent.add_variable(nid, nd.name_cn, default, varianttype=variant)
        if nd.array_len:
            var.set_value_rank(1)
            var.set_array_dimensions([nd.array_len])
        var.set_writable()
        if nd.name_en:
            try:
                var.set_attribute(
                    ua.AttributeIds.Description,
                    ua.DataValue(ua.Variant(ua.LocalizedText(nd.name_en), ua.VariantType.LocalizedText)),
                )
            except Exception:
                pass
        result[nd.name_cn] = var
    log.info("已创建 %d 个变量节点 (ns=%d)", len(result), ns_idx)
    return result


def set_initial_occupancy(nodes_by_cn: Dict[str, Any], enable: bool) -> int:
    """把 "_占位 / _空闲" 类节点初值置 TRUE，方便 Type-B 握手代理直接跑。"""
    if not enable:
        return 0
    count = 0
    for cn, node in nodes_by_cn.items():
        if OCC_RE.search(cn):
            try:
                node.set_value(True)
                count += 1
            except Exception:
                pass
    log.info("已把 %d 个 '_占位/_空闲' 节点初值置 TRUE", count)
    return count


def collect_connection_snapshot(
    server: Server,
    endpoint: str,
    first_seen: MutableMapping[int, float],
) -> Dict[str, Any]:
    """采集当前 TCP 客户端及 OPC UA Session 状态。

    ``python-opcua`` 在 ``bserver.clients`` 中保存活动 TCP 协议实例；每个实例
    都带有 peername 和 processor.session。复制列表后再遍历，避免异步网络线程
    增删客户端时影响本次快照。
    """
    now = time.time()
    clients = list(getattr(getattr(server, "bserver", None), "clients", []) or [])
    active_keys = {id(client) for client in clients}
    for key in list(first_seen):
        if key not in active_keys:
            first_seen.pop(key, None)

    items: List[Dict[str, Any]] = []
    for client in clients:
        key = id(client)
        connected_at = first_seen.setdefault(key, now)
        peer = getattr(client, "peername", None) or ()
        local = getattr(getattr(client, "processor", None), "sockname", None) or ()
        session = getattr(getattr(client, "processor", None), "session", None)
        session_state = "None"
        session_id = None
        if session is not None:
            state = getattr(session, "state", None)
            session_state = getattr(state, "name", None) or str(state).rsplit(".", 1)[-1]
            raw_session_id = getattr(session, "session_id", None)
            if raw_session_id is not None:
                session_id = (
                    raw_session_id.to_string()
                    if hasattr(raw_session_id, "to_string")
                    else str(raw_session_id)
                )

        items.append({
            "host": str(peer[0]) if len(peer) >= 1 else "",
            "port": int(peer[1]) if len(peer) >= 2 else None,
            "local_host": str(local[0]) if len(local) >= 1 else "",
            "local_port": int(local[1]) if len(local) >= 2 else None,
            "connected_at": connected_at,
            "session_state": session_state,
            "session_id": session_id,
        })

    items.sort(key=lambda item: (item["host"], item["port"] or 0))
    return {
        "version": 1,
        "server_pid": os.getpid(),
        "generated_at": now,
        "endpoint": endpoint,
        "tcp_connection_count": len(items),
        "session_count": sum(
            item["session_state"] == "Activated" for item in items
        ),
        "clients": items,
    }


def write_connection_snapshot(path: Path, snapshot: Dict[str, Any]) -> None:
    """原子写入连接状态，避免 GUI 读到半份 JSON。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    os.replace(temporary, path)


def remove_own_connection_snapshot(path: Path) -> None:
    """仅删除由当前进程生成的状态，避免旧进程误删新 Server 的快照。"""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("server_pid") == os.getpid():
            path.unlink(missing_ok=True)
    except (OSError, ValueError, TypeError):
        pass


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(description="XUSE OPC UA 仿真服务器（纯服务器版）")
    parser.add_argument("--host", default="0.0.0.0", help="监听地址 (默认 0.0.0.0)")
    parser.add_argument("--port", type=int, default=4855, help="监听端口 (默认 4855)")
    parser.add_argument(
        "--profile", choices=("csv", "ptlc"), default="csv",
        help="节点模型：csv=现有 CSV；ptlc=PTLC V2 嵌套 GVL/真数组",
    )
    default_csv = str(default_csv_path())
    parser.add_argument(
        "--csv",
        action="append",
        default=None,
        help=("CSV 变量表路径 (可指定多次以加载多份 CSV；默认: " + default_csv + ")"),
    )
    parser.add_argument("--ns-uri", default="urn:xuse:sim", help="XUSE 命名空间 URI")
    parser.add_argument("--ns-index", type=int, default=4, help="XUSE 命名空间索引 (默认 4)")
    parser.add_argument("--no-occupancy-true", action="store_true",
                        help="禁用 '_占位 / _空闲' 节点初值默认 TRUE")
    parser.add_argument(
        "--connection-state",
        default=str(connection_state_path()),
        help="连接状态 JSON 路径（供 GUI 展示客户端连接）",
    )
    args = parser.parse_args()

    if args.profile == "ptlc":
        default_ptlc = Path(__file__).with_name("config") / "ptlc_nodes.yaml"
        profile_paths = [Path(p).resolve() for p in (args.csv or [str(default_ptlc)])]
    else:
        profile_paths = [Path(p).resolve() for p in (args.csv or [default_csv])]
    for cp in profile_paths:
        if not cp.exists():
            log.error("节点表不存在: %s", cp)
            return 2
    log.info("将加载 %d 份 %s 节点表：", len(profile_paths), args.profile)
    for cp in profile_paths:
        log.info("  - %s", cp)

    if args.profile == "ptlc":
        if len(profile_paths) != 1:
            log.error("PTLC profile 只接受一份 YAML 节点表")
            return 2
        node_defs = load_ptlc_nodes(profile_paths[0], ns_index=args.ns_index)
    else:
        node_defs = load_csvs(profile_paths)

    endpoint = f"opc.tcp://{args.host}:{args.port}/xuse_sim/"
    server = build_server(endpoint)
    ns_idx = register_ns_padding(server, args.ns_index, args.ns_uri)
    connection_path = Path(args.connection_state).expanduser().resolve()

    server.start()
    log.info("=" * 68)
    log.info("OPC UA 服务器已启动")
    log.info("  Endpoint : %s", endpoint)
    log.info("  Namespace: ns=%d (%s)", ns_idx, args.ns_uri)
    log.info("  Anon     : 允许匿名 (NoSecurity)")
    log.info("  Profile  : %s", args.profile)
    log.info("  Handshake: 未启动 (请另开对应 profile 的握手代理)")
    log.info("=" * 68)

    try:
        nodes_by_cn = add_nodes(server, ns_idx, node_defs)
        set_initial_occupancy(nodes_by_cn, enable=not args.no_occupancy_true)

        stop_evt = threading.Event()

        def _handler(signum, frame):
            log.info("收到信号 %s，退出…", signum)
            stop_evt.set()

        signal.signal(signal.SIGINT, _handler)
        try:
            signal.signal(signal.SIGTERM, _handler)
        except (AttributeError, ValueError):
            pass

        first_seen: Dict[int, float] = {}
        last_state_error: str = ""
        while not stop_evt.is_set():
            try:
                snapshot = collect_connection_snapshot(server, endpoint, first_seen)
                write_connection_snapshot(connection_path, snapshot)
                last_state_error = ""
            except Exception as exc:  # noqa: BLE001
                error = str(exc)
                if error != last_state_error:
                    log.warning("写入客户端连接状态失败: %s", exc)
                    last_state_error = error
            stop_evt.wait(timeout=0.5)
    finally:
        server.stop()
        remove_own_connection_snapshot(connection_path)
        log.info("服务器已停止。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
