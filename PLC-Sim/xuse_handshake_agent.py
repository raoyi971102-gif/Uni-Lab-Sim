#!/usr/bin/env python3
"""XUSE 全局握手代理。

默认 package mode：一次启动常驻全部机械臂、工艺、初始化与参数下发通道。
不创建 OPC UA 节点，只连接由 ``xuse_variables.csv`` 建好的服务器。
"""

from __future__ import annotations

import argparse
import json
import signal
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

try:
    from .common import load_csv, load_yaml, match_pos_node, parse_suffix
    from .package_simulation import write_snapshot_atomic
    from .xuse_package_runtime import XusePackageRuntime
except ImportError:
    from common import load_csv, load_yaml, match_pos_node, parse_suffix
    from package_simulation import write_snapshot_atomic
    from xuse_package_runtime import XusePackageRuntime

DEFAULT_URL = "opc.tcp://127.0.0.1:4855/xuse_sim/"
DEFAULT_NODE_PREFIX = "ns=4;s=uniab|"
DEFAULT_CSV = Path(__file__).resolve().with_name("data") / "xuse_variables.csv"


class VariableAdapter(Protocol):
    def read(self, name: str) -> Any: ...
    def write(self, name: str, value: Any) -> None: ...


class MemoryAdapter:
    def __init__(self, values: dict[str, Any] | None = None) -> None:
        self.values: dict[str, Any] = dict(values or {})

    def read(self, name: str) -> Any:
        return self.values.get(name)

    def write(self, name: str, value: Any) -> None:
        self.values[name] = value


class OpcUaVariableAdapter:
    def __init__(self, url: str, node_prefix: str = DEFAULT_NODE_PREFIX) -> None:
        self.url = url
        self.node_prefix = node_prefix
        self._client = None
        self._nodes: dict[str, Any] = {}

    def connect(self) -> None:
        from opcua import Client

        self._client = Client(self.url, timeout=10)
        self._client.connect()

    def disconnect(self) -> None:
        if self._client is not None:
            try:
                self._client.disconnect()
            except Exception:
                pass

    def _node(self, name: str):
        if name not in self._nodes:
            self._nodes[name] = self._client.get_node(f"{self.node_prefix}{name}")
        return self._nodes[name]

    def read(self, name: str) -> Any:
        return self._node(name).get_value()

    def write(self, name: str, value: Any) -> None:
        self._node(name).set_value(value)


@dataclass
class HandshakeEvent:
    action: str
    phase: str
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class Channel:
    base: str
    kind: str
    write_name: str
    read_name: str
    request_name: str | None = None
    delay_s: float = 0.5
    pending_until: float | None = None
    last_write: bool = False
    target_pos: str | None = None
    target_pick: str | None = None
    current_pos: str | None = None
    current_pick: str | None = None


def _apply_occupancy(adapter: VariableAdapter, pick_code: int, arm: int) -> dict[str, bool]:
    updates: dict[str, bool] = {}

    def set_flag(name: str, value: bool) -> None:
        updates[name] = value
        try:
            adapter.write(name, value)
        except Exception:
            pass

    if arm == 1:
        if 1 <= pick_code <= 32:
            set_flag(f"ROBOT_1_occupy[{pick_code}]", False)
        elif pick_code == 40:
            set_flag("开罐占位_罐体", True)
        elif pick_code == 41:
            set_flag("开罐占位_罐体", False)
        elif pick_code == 42:
            set_flag("加样占位", True)
        elif pick_code == 43:
            set_flag("加样占位", False)
        elif pick_code == 44:
            set_flag("加珠占位", True)
        elif pick_code == 45:
            set_flag("加珠占位", False)
        elif pick_code == 46:
            set_flag("开罐占位_罐体", True)
        elif pick_code == 47:
            set_flag("开罐占位_罐体", False)
        elif 50 <= pick_code <= 53:
            set_flag(f"球磨占位_{pick_code - 49}", True)
        elif 54 <= pick_code <= 57:
            set_flag(f"球磨占位_{pick_code - 53}", False)
        elif pick_code % 10 == 2 and 60 <= pick_code <= 97:
            set_flag("过筛球磨罐占位", True)
        elif pick_code % 10 == 3 and 60 <= pick_code <= 97:
            set_flag("过筛球磨罐占位", False)
        elif pick_code % 10 == 4 and 60 <= pick_code <= 97:
            set_flag("刮粉占位", True)
        elif pick_code % 10 == 5 and 60 <= pick_code <= 97:
            set_flag("刮粉占位", False)
        elif 101 <= pick_code <= 132:
            set_flag(f"ROBOT_1_occupy[{pick_code - 100}]", True)
    elif arm == 2:
        if pick_code == 42:
            set_flag("过筛小坩埚占位", True)
        elif pick_code == 41:
            set_flag("过筛小坩埚占位", False)
        elif pick_code == 44:
            set_flag("过筛漏斗占位", True)
        elif pick_code == 43:
            set_flag("过筛漏斗占位", False)
        elif 47 <= pick_code <= 50:
            set_flag(f"小坩埚出料占位_{pick_code - 46}", True)
        elif 21 <= pick_code <= 28:
            set_flag(f"ROBOT_2_occupy[{pick_code}]", True)
        elif 31 <= pick_code <= 38:
            set_flag(f"ROBOT_2_occupy[{pick_code}]", False)
    elif arm == 3:
        if 2 <= pick_code <= 7:
            set_flag(f"马弗炉占位_{pick_code - 1}", True)
        elif 8 <= pick_code <= 13:
            set_flag(f"马弗炉占位_{pick_code - 7}", False)
        elif pick_code == 14:
            set_flag("上成品料架占位", True)
        elif pick_code == 15:
            set_flag("下成品料架占位", True)
    return updates


def _arm_index(base: str) -> int | None:
    if base.endswith("_1") or base.endswith("机械臂_1"):
        return 1
    if base.endswith("_2") or base.endswith("机械臂_2"):
        return 2
    if base.endswith("_3") or base.endswith("机械臂_3"):
        return 3
    return None


class XuseHandshakeSimulator:
    """全部 XUSE 握手通道常驻的全局代理。"""

    def __init__(
        self,
        adapter: VariableAdapter,
        *,
        csv_path: str | Path | None = None,
        delay_ms: float = 500,
        initial_values: dict[str, Any] | None = None,
        occupy_can_rack: bool = True,
    ) -> None:
        self.adapter = adapter
        self.delay_s = float(delay_ms) / 1000.0
        self.initial_values = dict(initial_values or {})
        self.occupy_can_rack = occupy_can_rack
        self.channels = self._build_channels(Path(csv_path or DEFAULT_CSV))
        self._owned: list[str] = []

    def _build_channels(self, csv_path: Path) -> list[Channel]:
        nodes = load_csv(csv_path) if csv_path.is_file() else []
        by_key: dict[tuple[str, str], dict[str, Any]] = {}
        names = [node.name_cn for node in nodes]
        pos_attr = {
            "target_pos_node": "target_pos",
            "target_pick_node": "target_pick",
            "current_pos_node": "current_pos",
            "current_pick_node": "current_pick",
        }
        for node in nodes:
            parsed = parse_suffix(node.name_cn)
            if parsed is None:
                continue
            base, role, kind, delay_ms = parsed
            bucket = by_key.setdefault((base, kind), {"kind": kind, "base": base, "delay_s": delay_ms / 1000.0})
            bucket[role] = node.name_cn
        channels: list[Channel] = []
        for (base, kind), payload in by_key.items():
            write_name = payload.get("W")
            read_name = payload.get("R")
            if not write_name or not read_name:
                continue
            channel = Channel(
                base=base,
                kind=kind,
                write_name=write_name,
                read_name=read_name,
                request_name=payload.get("REQ"),
                delay_s=self.delay_s,
            )
            for name in names:
                attr = pos_attr.get(match_pos_node(name, base) or "")
                if attr:
                    setattr(channel, attr, name)
            channels.append(channel)
        return channels

    def initialization_values(self) -> dict[str, Any]:
        values: dict[str, Any] = {
            "机械臂空闲_1": True,
            "机械臂空闲_2": True,
            "机械臂空闲_3": True,
            "机械臂故障_1": False,
            "机械臂故障_2": False,
            "机械臂故障_3": False,
        }
        for channel in self.channels:
            if channel.request_name:
                values[channel.request_name] = True
            values[channel.read_name] = False
            values[channel.write_name] = False
        if self.occupy_can_rack:
            for index in range(1, 33):
                values[f"ROBOT_1_occupy[{index}]"] = True
        values.update(self.initial_values)
        return values

    def initialize(self) -> None:
        for name, value in self.initialization_values().items():
            try:
                self.adapter.write(name, value)
                self._owned.append(name)
            except Exception:
                continue

    def cleanup(self) -> None:
        for name in self._owned:
            try:
                current = self.adapter.read(name)
            except Exception:
                continue
            if isinstance(current, bool):
                self.adapter.write(name, False)

    def protocol_snapshot(self) -> dict[str, Any]:
        return {
            "mode": "package",
            "channels": [channel.base for channel in self.channels],
            "channel_count": len(self.channels),
        }

    def step(self, now: float | None = None) -> list[HandshakeEvent]:
        clock = time.monotonic() if now is None else now
        events: list[HandshakeEvent] = []
        for channel in self.channels:
            try:
                write_value = bool(self.adapter.read(channel.write_name))
            except Exception:
                continue
            rising = write_value and not channel.last_write
            falling = (not write_value) and channel.last_write
            channel.last_write = write_value
            if rising:
                if channel.kind == "process_B" and channel.request_name:
                    try:
                        if not self.adapter.read(channel.request_name):
                            continue
                    except Exception:
                        pass
                channel.pending_until = clock + (channel.delay_s or self.delay_s)
                if channel.target_pos and channel.current_pos:
                    try:
                        self.adapter.write(channel.current_pos, self.adapter.read(channel.target_pos))
                    except Exception:
                        pass
                if channel.target_pick and channel.current_pick:
                    try:
                        self.adapter.write(channel.current_pick, self.adapter.read(channel.target_pick))
                    except Exception:
                        pass
                events.append(HandshakeEvent(channel.base, "accepted", {"kind": channel.kind}))
            if channel.pending_until is not None and clock >= channel.pending_until and write_value:
                self.adapter.write(channel.read_name, True)
                detail: dict[str, Any] = {"kind": channel.kind}
                if channel.target_pick:
                    try:
                        pick_code = int(self.adapter.read(channel.target_pick) or 0)
                        arm = _arm_index(channel.base)
                        if arm and pick_code:
                            detail["occupancy"] = _apply_occupancy(self.adapter, pick_code, arm)
                            if channel.base.startswith("开罐") or "开罐" in channel.base:
                                if pick_code == 1:
                                    self.adapter.write("开罐占位_上盖", True)
                                elif pick_code == 2:
                                    self.adapter.write("开罐占位_上盖", False)
                    except Exception:
                        pass
                if "开罐" in channel.base and channel.kind == "process_B":
                    try:
                        code = int(self.adapter.read("开罐动作控制代码") or 0)
                        if code == 1:
                            self.adapter.write("开罐占位_上盖", True)
                        elif code == 2:
                            self.adapter.write("开罐占位_上盖", False)
                    except Exception:
                        pass
                channel.pending_until = None
                events.append(HandshakeEvent(channel.base, "completed", detail))
            if falling:
                self.adapter.write(channel.read_name, False)
                channel.pending_until = None
                events.append(HandshakeEvent(channel.base, "reset", {"kind": channel.kind}))
        return events


def default_config_path() -> Path:
    return Path(__file__).resolve().with_name("config") / "xuse_handshake.yaml"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="XUSE 全局握手代理")
    parser.add_argument("command", nargs="?", default="serve", choices=["list", "check", "serve"])
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--config", default=str(default_config_path()))
    parser.add_argument("--csv", default=str(DEFAULT_CSV))
    parser.add_argument("--delay-ms", type=float, default=None)
    parser.add_argument("--poll-ms", type=float, default=None)
    parser.add_argument("--time-scale", type=float, default=None)
    parser.add_argument("--state-file", default="")
    args = parser.parse_args(argv)

    config = load_yaml(args.config) if Path(args.config).is_file() else {}
    delay_ms = args.delay_ms if args.delay_ms is not None else float(config.get("delay_ms", 500))
    poll_ms = args.poll_ms if args.poll_ms is not None else float(config.get("poll_ms", 20))
    time_scale = args.time_scale if args.time_scale is not None else float(config.get("time_scale", 1.0))
    node_prefix = str(config.get("node_prefix", DEFAULT_NODE_PREFIX))
    initial_values = dict(config.get("initial_values") or {})

    if args.command == "list":
        simulator = XuseHandshakeSimulator(MemoryAdapter(), csv_path=args.csv, delay_ms=delay_ms)
        print(json.dumps(simulator.protocol_snapshot(), ensure_ascii=False, indent=2))
        return 0

    adapter: VariableAdapter
    connected = False
    if args.command in {"check", "serve"}:
        live = OpcUaVariableAdapter(args.url, node_prefix)
        live.connect()
        adapter = live
        connected = True
    else:
        adapter = MemoryAdapter()

    simulator = XuseHandshakeSimulator(
        adapter,
        csv_path=args.csv,
        delay_ms=delay_ms,
        initial_values=initial_values,
    )
    runtime = XusePackageRuntime(time_scale=time_scale)

    if args.command == "check":
        missing = []
        for name in simulator.initialization_values():
            try:
                adapter.read(name)
            except Exception:
                missing.append(name)
        print(json.dumps({"ok": not missing, "missing": missing}, ensure_ascii=False, indent=2))
        if connected:
            live.disconnect()
        return 0 if not missing else 1

    stop = False

    def _stop(*_args) -> None:
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, _stop)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _stop)

    simulator.initialize()
    runtime.initialize_protocol(simulator.initialization_values())
    print(json.dumps({"event": "ready", **simulator.protocol_snapshot()}, ensure_ascii=False), flush=True)
    try:
        while not stop:
            events = simulator.step()
            for event in events:
                runtime.observe(event)
                print(json.dumps({"action": event.action, "phase": event.phase, "detail": event.detail}, ensure_ascii=False), flush=True)
            if events and args.state_file:
                write_snapshot_atomic(args.state_file, runtime.snapshot(simulator.protocol_snapshot()))
            time.sleep(max(poll_ms, 1.0) / 1000.0 / max(time_scale, 0.01))
    finally:
        if config.get("cleanup_on_exit", True):
            simulator.cleanup()
        runtime.stop()
        if connected:
            live.disconnect()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
