"""Installed command for the Modbus-Sim GUI and headless server."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from dataclasses import replace
from pathlib import Path
from typing import Sequence

from pymodbus import pymodbus_apply_logging_config

from . import __version__
from .config import (
    ConfigError,
    SerialTransportSpec,
    TcpTransportSpec,
    TransportMode,
    config_to_dict,
    default_config_path,
    load_config,
    parse_config,
    replace_transport,
    select_transport,
)
from .server import build_server_plan, run_server


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="modbus-sim", description="Uni-Lab Modbus 协议仿真器")
    parser.add_argument("-V", "--version", action="version", version=__version__)
    subparsers = parser.add_subparsers(dest="command")

    gui = subparsers.add_parser("gui", help="启动本地 Web GUI（默认）")
    gui.add_argument("--host", default=os.environ.get("MODBUSSIM_GUI_HOST", "127.0.0.1"))
    gui.add_argument("--port", type=int, default=int(os.environ.get("MODBUSSIM_GUI_PORT", "18865")))
    gui.add_argument("--config", type=Path, default=default_config_path())
    gui.add_argument("--no-open", action="store_true", help="不自动打开浏览器")

    serve = subparsers.add_parser("serve", help="无 GUI 运行一个 Modbus 从站服务")
    _add_config_args(serve)
    serve.add_argument("--log-level", choices=("DEBUG", "INFO", "WARNING", "ERROR"), default="INFO")

    validate = subparsers.add_parser("validate", help="校验 YAML 配置但不启动服务")
    validate.add_argument("--config", type=Path, default=default_config_path())
    return parser


def _add_config_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", type=Path, default=default_config_path())
    parser.add_argument("--transport", choices=[item.value for item in TransportMode])
    parser.add_argument("--host", help="覆盖 Modbus TCP 监听地址")
    parser.add_argument("--tcp-port", type=int, help="覆盖 Modbus TCP 监听端口")
    parser.add_argument("--serial-port", help="覆盖所选串行模式的设备路径或 COM 端口")
    parser.add_argument("--baudrate", type=int, help="覆盖所选串行模式的波特率")


def _load_with_overrides(args: argparse.Namespace):
    config = load_config(args.config)
    mode = TransportMode.parse(args.transport or os.environ.get("MODBUSSIM_TRANSPORT", config.active_transport.value))
    config = select_transport(config, mode)
    spec = config.transport(mode)
    if isinstance(spec, TcpTransportSpec):
        host = args.host or os.environ.get("MODBUSSIM_TCP_HOST") or spec.host
        port = args.tcp_port if args.tcp_port is not None else int(os.environ.get("MODBUSSIM_TCP_PORT", spec.port))
        config = replace_transport(config, mode, replace(spec, host=host, port=port))
    elif isinstance(spec, SerialTransportSpec):
        device = args.serial_port or os.environ.get("MODBUSSIM_SERIAL_PORT") or spec.device
        baudrate = args.baudrate if args.baudrate is not None else int(os.environ.get("MODBUSSIM_BAUDRATE", spec.baudrate))
        config = replace_transport(config, mode, replace(spec, device=device, baudrate=baudrate))
    return parse_config(config_to_dict(config))


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    raw_args = list(sys.argv[1:] if argv is None else argv)
    args = parser.parse_args(raw_args)
    command = args.command or "gui"
    try:
        if command == "validate":
            config = load_config(args.config)
            modes = ", ".join(mode.value for mode in config.transports)
            print(f"配置有效：{len(config.devices)} 个从站；传输方式 {modes}")
            return 0
        if command == "serve":
            config = _load_with_overrides(args)
            plan = build_server_plan(config)
            pymodbus_apply_logging_config(args.log_level)
            print(f"Modbus-Sim 正在监听 {plan.endpoint}")
            asyncio.run(run_server(config))
            return 0
        if command == "gui":
            if not raw_args:
                args = parser.parse_args(["gui"])
            from .gui.backend import run_gui

            run_gui(host=args.host, port=args.port, config_path=args.config, open_browser=not args.no_open)
            return 0
    except KeyboardInterrupt:
        return 130
    except ConfigError as exc:
        print(f"modbus-sim: {exc}", file=sys.stderr)
        return 2
    parser.error(f"未知命令: {command}")
    return 2
