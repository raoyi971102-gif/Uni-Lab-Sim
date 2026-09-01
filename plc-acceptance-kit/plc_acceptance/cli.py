"""PLC 自动化验收包命令行入口。"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from .config import load_bundle
from .reporting import write_reports
from .runner import run_acceptance
from .simulator import run_simulator_acceptance
from .validator import validate_bundle


def _default_root() -> Path:
    """返回安装包内置配置的根目录。"""

    return Path(__file__).resolve().parents[1]


def _parser() -> argparse.ArgumentParser:
    """构建命令行参数解析器。

    返回：包含 validate、run 和 verify-simulator 子命令的解析器。
    """

    parser = argparse.ArgumentParser(
        prog="plc-acceptance", description="UniLab PLC 自动化验收"
    )
    parser.add_argument("--kit-root", default=str(_default_root()), help="验收包根目录")
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser("validate", help="执行 L0 协议和点表静态检查")
    validate.add_argument("--environment", default="szlab-simulator")

    run = subparsers.add_parser("run", help="连接已有 OPC UA Endpoint 执行测试")
    run.add_argument("--environment", default="szlab-simulator")
    run.add_argument("--endpoint", help="覆盖环境配置中的 Endpoint")
    run.add_argument("--output", default=str(_default_root() / "reports"))
    run.add_argument(
        "--case", action="append", dest="cases", help="只运行指定用例，可重复"
    )
    run.add_argument("--confirm-safe-test-mode", action="store_true")
    run.add_argument("--plc-artifact", help="PLC 候选版本包，用于报告哈希绑定")

    simulator = subparsers.add_parser(
        "verify-simulator",
        help="启动 PLC-Sim Server + SZLab Agent 并执行 L1 门禁",
    )
    simulator.add_argument("--output", default=str(_default_root() / "reports"))
    simulator.add_argument(
        "--case", action="append", dest="cases", help="只运行指定用例，可重复"
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """执行 PLC 自动化验收命令。

    参数：``argv`` 是可选参数序列；省略时读取进程参数。
    返回：通过为 0，失败或阻塞为 1，配置错误为 2。
    """

    args = _parser().parse_args(argv)
    root = Path(args.kit_root).resolve()
    if args.command == "validate":
        bundle = load_bundle(root, environment_name=args.environment)
        findings = validate_bundle(bundle)
        for finding in findings:
            print(f"[{finding.severity.upper()}] {finding.case_id}: {finding.message}")
        if any(item.severity == "error" for item in findings):
            return 1
        print(
            f"L0 PASSED: project={bundle.project_id} protocol={bundle.protocol_version} "
            f"nodes={bundle.expected_scalar_nodes}"
        )
        return 0

    selected = set(args.cases) if args.cases else None
    if args.command == "verify-simulator":
        result, report_dir = run_simulator_acceptance(
            root,
            output_root=args.output,
            selected_case_ids=selected,
        )
    else:
        bundle = load_bundle(
            root,
            environment_name=args.environment,
            endpoint_override=args.endpoint,
        )
        result = run_acceptance(
            bundle,
            confirm_safe_test_mode=args.confirm_safe_test_mode,
            selected_case_ids=selected,
            plc_artifact=args.plc_artifact,
        )
        report_dir = write_reports(result, args.output)
    print(f"{result.status}: {result.run_id}")
    print(f"报告: {report_dir}")
    return 0 if result.status == "PASSED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
