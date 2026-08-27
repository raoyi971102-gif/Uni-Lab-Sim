"""PTLC L2 握手代理的命令行入口和进程生命周期。"""

from __future__ import annotations

import argparse
import json
import signal
import sys
import time
from pathlib import Path
from typing import Any

try:
    from .common import load_yaml
    from .ptlc_handshake_agent import PtlcHandshakeSimulator
    from .ptlc_runtime import MODELED_ACTIONS, OpcUaVariableAdapter
except ImportError:  # 兼容从源码目录直接执行。
    from common import load_yaml
    from ptlc_handshake_agent import PtlcHandshakeSimulator
    from ptlc_runtime import MODELED_ACTIONS, OpcUaVariableAdapter


def _config_path() -> str:
    """返回随包发布的默认 PTLC 握手配置路径。"""

    return str(Path(__file__).with_name("config") / "ptlc_handshake.yaml")


def build_parser() -> argparse.ArgumentParser:
    """构建命令行解析器；无参数，返回配置完整的解析器。"""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", nargs="?", choices=("list", "check", "serve"), default="serve"
    )
    parser.add_argument("--config", default=_config_path())
    parser.add_argument("--url", default="opc.tcp://127.0.0.1:4855/xuse_sim/")
    parser.add_argument("--username", default="")
    parser.add_argument("--password", default="")
    parser.add_argument("--delay-ms", type=int, default=None)
    parser.add_argument("--poll-ms", type=int, default=None)
    parser.add_argument("--time-scale", type=float, default=None)
    parser.add_argument(
        "--sensor-mode",
        choices=("standalone", "federated"),
        default=None,
        help="覆盖配置中的 PTLC 传感器模式",
    )
    parser.add_argument(
        "--fault-file",
        default=None,
        help="运行期故障 JSON；格式 {Station:{ActionCode: outcome}}",
    )
    parser.add_argument(
        "--world-file",
        default=None,
        help="外部设备模拟器写入的 PLC 输入世界 JSON；接受物料计数、传感器和幂等 events",
    )
    parser.add_argument(
        "--state-file", default=None, help="周期写出确定性进程/动作状态 JSON"
    )
    parser.add_argument("--max-actions", type=int, default=0)
    parser.add_argument("--no-initialize", action="store_true")
    parser.add_argument("--keep-state-on-exit", action="store_true")
    return parser


def _load_fault_file(
    simulator: PtlcHandshakeSimulator,
    fault_path: Path | None,
    fault_stamp: int | None,
) -> int | None:
    """按修改时间热加载故障文件；返回新的时间戳，文件删除时返回空。"""

    if fault_path is None:
        return fault_stamp
    try:
        stamp = fault_path.stat().st_mtime_ns
        if stamp != fault_stamp:
            payload = json.loads(fault_path.read_text(encoding="utf-8"))
            simulator.runtime_faults.load_payload(payload or {})
        return stamp
    except FileNotFoundError:
        if fault_stamp is not None:
            simulator.runtime_faults.clear()
        return None
    except (OSError, ValueError, TypeError) as exc:
        print(f"运行期故障文件无效: {exc}", file=sys.stderr, flush=True)
        return fault_stamp


def _write_state_file(
    simulator: PtlcHandshakeSimulator, state_path: Path | None
) -> None:
    """把状态机快照写到可选 JSON 路径；路径为空时不执行。"""

    if state_path is None:
        return
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps(simulator.snapshot(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _load_world_file(
    simulator: PtlcHandshakeSimulator,
    world_path: Path | None,
    world_stamp: int | None,
) -> int | None:
    """按修改时间加载外部设备提供的 PLC 输入事实，返回新的时间戳。"""

    if world_path is None:
        return world_stamp
    try:
        stamp = world_path.stat().st_mtime_ns
        if stamp != world_stamp:
            payload = json.loads(world_path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise TypeError("world JSON 顶层必须是对象")
            simulator.plant.apply_world_patch(payload)
        return stamp
    except FileNotFoundError:
        return None
    except (OSError, ValueError, TypeError) as exc:
        print(f"PLC 输入世界文件无效: {exc}", file=sys.stderr, flush=True)
        return world_stamp


def main(argv: list[str] | None = None) -> int:
    """运行 PTLC 代理命令；参数为可选 argv，返回进程退出码。"""

    args = build_parser().parse_args(argv)
    config = load_yaml(args.config)
    if args.sensor_mode is not None:
        plant = dict(config.get("plant", {}))
        plant["sensor_mode"] = args.sensor_mode
        config["plant"] = plant
    browse_path = tuple(str(part) for part in config.get("gvl_path", ()))
    if not browse_path:
        print("PTLC 配置缺少 gvl_path", file=sys.stderr)
        return 2
    delay_s = (
        max(
            float(
                args.delay_ms
                if args.delay_ms is not None
                else config.get("delay_ms", 200)
            ),
            0.0,
        )
        / 1000.0
    )
    poll_s = (
        max(
            float(
                args.poll_ms if args.poll_ms is not None else config.get("poll_ms", 20)
            ),
            5.0,
        )
        / 1000.0
    )
    adapter = OpcUaVariableAdapter(
        args.url, browse_path, username=args.username, password=args.password
    )
    simulator = PtlcHandshakeSimulator(adapter, config=config, delay_s=delay_s)

    if args.command == "list":
        print(
            json.dumps(
                {
                    "scope": "plc-only",
                    "orchestrator": "Uni-Lab OS Backend",
                    "sensor_mode": simulator.plant.sensors.mode,
                    "stations": simulator.stations,
                    "nodes": simulator.contract_names(),
                    "actions": {
                        station: list(simulator.contracts[station].accepts)
                        for station in simulator.stations
                    },
                    "modeled_actions": {
                        station: sorted(MODELED_ACTIONS[station])
                        for station in simulator.stations
                    },
                    "unmodeled_actions": {
                        station: sorted(
                            set(simulator.contracts[station].accepts)
                            - MODELED_ACTIONS[station]
                        )
                        for station in simulator.stations
                    },
                    "coverage": simulator.plant.snapshot()["coverage"],
                    "behavior_sha256": {
                        station: simulator.contracts[station].source_sha256
                        for station in simulator.stations
                    },
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    adapter.connect()
    try:
        if args.command == "check":
            missing = simulator.check()
            if missing:
                print("缺少 PTLC 协议节点：\n" + "\n".join(missing), file=sys.stderr)
                return 1
            print(f"PTLC 协议检查通过：{len(simulator.contract_names())} 个节点")
            return 0

        missing = simulator.check()
        if missing:
            preview = ", ".join(missing[:20])
            suffix = f" 等 {len(missing)} 项" if len(missing) > 20 else ""
            print(
                f"警告：PTLC 节点表与服务端存在漂移，相关功能将降级：{preview}{suffix}",
                file=sys.stderr,
                flush=True,
            )
        if not args.no_initialize:
            simulator.initialize()
        stopping = False

        def _stop(signum: int, frame: Any) -> None:
            """响应终止信号；参数由 signal 传入，无返回值。"""

            del signum, frame
            nonlocal stopping
            stopping = True

        signal.signal(signal.SIGINT, _stop)
        try:
            signal.signal(signal.SIGTERM, _stop)
        except (AttributeError, ValueError):
            pass

        completed = 0
        time_scale = float(
            args.time_scale
            if args.time_scale is not None
            else config.get("time_scale", 1.0)
        )
        if not 0 < time_scale <= 1000:
            print("time-scale 必须在 0..1000 之间", file=sys.stderr)
            return 2
        real_epoch = time.monotonic()
        sim_epoch = real_epoch
        fault_path = Path(args.fault_file).resolve() if args.fault_file else None
        world_path = Path(args.world_file).resolve() if args.world_file else None
        state_path = Path(args.state_file).resolve() if args.state_file else None
        fault_stamp: int | None = None
        world_stamp: int | None = None
        next_state_write = 0.0
        print(
            f"PTLC L2 代理已连接 {args.url}，工位={','.join(simulator.stations)}，"
            f"传感器模式={simulator.plant.sensors.mode}",
            flush=True,
        )
        while not stopping:
            real_now = time.monotonic()
            sim_now = sim_epoch + (real_now - real_epoch) * time_scale
            fault_stamp = _load_fault_file(simulator, fault_path, fault_stamp)
            world_stamp = _load_world_file(simulator, world_path, world_stamp)
            for event in simulator.step(now=sim_now):
                print(json.dumps(event.__dict__, ensure_ascii=False), flush=True)
                if event.phase in {"completed", "rejected", "error", "interrupted"}:
                    completed += 1
            if state_path is not None and real_now >= next_state_write:
                _write_state_file(simulator, state_path)
                next_state_write = real_now + 0.25
            if args.max_actions and completed >= args.max_actions:
                break
            time.sleep(poll_s)
    finally:
        if args.command == "serve" and not args.keep_state_on_exit:
            try:
                simulator.cleanup()
            except Exception as exc:  # noqa: BLE001 - 退出阶段必须继续断开连接。
                print(f"PTLC 代理清理状态失败: {exc}", file=sys.stderr)
        adapter.disconnect()
    return 0


if __name__ == "__main__":
    sys.exit(main())
