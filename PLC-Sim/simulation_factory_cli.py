"""CLI for the deterministic CSV-to-device-package factory."""
from __future__ import annotations
import argparse, json
from pathlib import Path
try:
    from .simulation_factory import SimulationFactory
except ImportError:
    from simulation_factory import SimulationFactory

def main(argv=None) -> int:
    parser=argparse.ArgumentParser(prog="plc-sim factory"); sub=parser.add_subparsers(dest="action",required=True)
    inspect=sub.add_parser("inspect",help="提取变量并输出证据"); inspect.add_argument("source")
    build=sub.add_parser("build",help="生成设备包和 OS Python 骨架"); build.add_argument("source"); build.add_argument("--out",required=True); build.add_argument("--patch")
    validate=sub.add_parser("validate",help="验证场景报告"); validate.add_argument("spec")
    args=parser.parse_args(argv); factory=SimulationFactory()
    if args.action == "inspect": result=factory.inspect(args.source)
    elif args.action == "validate": result=factory.validate(args.spec)
    else:
        patch=json.loads(Path(args.patch).read_text(encoding="utf-8")) if args.patch else None
        result=factory.build(args.source,args.out,patch)
    print(json.dumps(result,ensure_ascii=False,indent=2)); return 0

if __name__ == "__main__":
    raise SystemExit(main())
