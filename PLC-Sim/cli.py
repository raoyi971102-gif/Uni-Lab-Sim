"""明确委托原OPC UA产品，不复制旧设备或GUI模块。"""
from __future__ import annotations

import sys
from typing import Sequence
from . import __version__


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] in {"-V", "--version"}:
        print(f"plc-sim {__version__}; compatibility runtime: unilab-opcua-sim 0.2.6")
        return 0
    if args and args[0] == "signals":
        from .signals_cli import main as signals_main
        return signals_main(args[1:])
    # 仅调用CLI时加载旧产品；import plc_sim不会隐式拉入设备/GUI。
    from opcua_sim.cli import main as legacy_main
    if args and args[0] in {"-h", "--help"}:
        print("plc-sim: compatibility entry for unilab-opcua-sim 0.2.6.")
        print("signals: run a source-locked public signal assembly (plc_sim).")
        print("The commands below are provided by the original opcua-sim runtime.")
    return legacy_main(args)
