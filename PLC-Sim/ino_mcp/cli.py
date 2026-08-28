"""
ino_mcp.cli —— InoProShop MCP 命令行入口
============================================================================
用法（在 PLC-Sim/ 目录里）:

    # 1) 打开项目并保持 MCP 连接（供其他命令后续调用；一般不常用，只做验证）
    python -m ino_mcp.cli open --project "C:\\path\\to\\XUSE.project"

    # 2) 查看项目结构
    python -m ino_mcp.cli structure --project ... > tree.txt

    # 3) 读一个 POU / GVL 的代码
    python -m ino_mcp.cli get-pou --project ... --pou "Application/GVL_XUSE"

    # 4) 写一个 POU / GVL 的代码
    python -m ino_mcp.cli set-pou --project ... --pou "Application/POU_1" \
                          --decl-file decl.iecst --impl-file impl.iecst

    # 5) 编译
    python -m ino_mcp.cli compile --project ...

    # 6) "下载"（保存 + 编译；如需在线下载加 --strategy online）
    python -m ino_mcp.cli download --project ... [--strategy online]

    # 7) 从 InoProShop 提取 GVL 变量到 CSV（喂给 server.py）
    python -m ino_mcp.cli extract --project ... --out extracted.csv \
                          [--name-mode en|comment] [--all]

    # 8) 一键流水线：extract → 启动 server.py（--serve 后跟的额外参数会转发给服务器）
    python -m ino_mcp.cli pipeline --project ... --out extracted.csv \
                          --serve --port 4855

配置来源:
    支持 MCP JSON、PLCSIM_* 环境变量和命令行参数。
"""

from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from ..cli import runtime_command
except ImportError:  # Direct `python -m ino_mcp.cli` compatibility.
    from cli import runtime_command

from .client import McpClient, McpError
from .config import resolve_mcp_config
from .toolkit import InoToolkit, DownloadStrategy
from .extractor import extract_gvl_variables, write_csv


log = logging.getLogger("ino_mcp.cli")


# ---------------------------------------------------------------------------
# 配置读取
# ---------------------------------------------------------------------------
def _resolve_mcp_config(args: argparse.Namespace) -> Dict[str, Any]:
    """合并 MCP JSON、环境变量和命令行连接参数。"""
    defaults = resolve_mcp_config(
        project=args.project,
        server_name=args.mcp_server or "codesys_local",
        overrides={
            "bundle_js": args.bundle,
            "codesys_path": args.codesys_path,
            "codesys_profile": args.codesys_profile,
            "workspace": args.workspace,
            "node_cmd": args.node,
        },
    )
    if not defaults["bundle_js"] or not Path(defaults["bundle_js"]).exists():
        raise SystemExit(
            "❌ 找不到 MCP bundle.min.js；请通过 --bundle、"
            "PLCSIM_MCP_BUNDLE 或 MCP JSON 配置指定。"
        )
    if not defaults["codesys_path"] or not Path(defaults["codesys_path"]).exists():
        raise SystemExit(
            "❌ 找不到 InoProShop.exe；请通过 --codesys-path 或 "
            "PLCSIM_INOPROSHOP_EXE 指定。"
        )
    return defaults


def _new_client(args: argparse.Namespace) -> McpClient:
    cfg = _resolve_mcp_config(args)
    log.info("MCP 配置: bundle=%s | codesys=%s | profile=%s | ws=%s",
             cfg["bundle_js"], cfg["codesys_path"], cfg["codesys_profile"], cfg["workspace"])
    return McpClient(
        bundle_js=cfg["bundle_js"],
        codesys_path=cfg["codesys_path"],
        codesys_profile=cfg["codesys_profile"],
        workspace=cfg["workspace"],
        node_cmd=cfg["node_cmd"],
    )


# ---------------------------------------------------------------------------
# 子命令实现
# ---------------------------------------------------------------------------
def cmd_open(args: argparse.Namespace) -> int:
    with _new_client(args) as mcp:
        tk = InoToolkit(mcp, args.project)
        out = tk.open_project()
        print(out.strip())
        if args.keep_alive:
            print("[open] 保持连接，Ctrl+C 退出…")
            try:
                import time
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                print("[open] 结束")
    return 0


def cmd_structure(args: argparse.Namespace) -> int:
    with _new_client(args) as mcp:
        tk = InoToolkit(mcp, args.project)
        tk.open_project()
        out = tk.get_project_structure()
        if args.output:
            Path(args.output).write_text(out, encoding="utf-8")
            print(f"[structure] 已写入 {args.output}（{len(out)} 字符）")
        else:
            print(out)
    return 0


def cmd_get_pou(args: argparse.Namespace) -> int:
    with _new_client(args) as mcp:
        tk = InoToolkit(mcp, args.project)
        tk.open_project()
        code = tk.get_pou_code(args.pou)
        if args.output:
            Path(args.output).write_text(code, encoding="utf-8")
            print(f"[get-pou] 已写入 {args.output}（{len(code)} 字符）")
        else:
            print(code)
    return 0


def _read_maybe(path: Optional[str]) -> Optional[str]:
    if not path:
        return None
    return Path(path).read_text(encoding="utf-8")


def cmd_set_pou(args: argparse.Namespace) -> int:
    decl = args.decl if args.decl is not None else _read_maybe(args.decl_file)
    impl = args.impl if args.impl is not None else _read_maybe(args.impl_file)
    if decl is None and impl is None:
        print("❌ 请至少提供 --decl / --decl-file 或 --impl / --impl-file 其中之一", file=sys.stderr)
        return 2
    with _new_client(args) as mcp:
        tk = InoToolkit(mcp, args.project)
        tk.open_project()
        out = tk.set_pou_code(args.pou, declaration=decl, implementation=impl)
        print(out.strip())
        if args.save:
            print(tk.save_project().strip())
    return 0


def cmd_compile(args: argparse.Namespace) -> int:
    with _new_client(args) as mcp:
        tk = InoToolkit(mcp, args.project)
        tk.open_project()
        cr = tk.compile_project()
        print(f"[compile] ok={cr.ok} summary={cr.summary}")
        if not cr.ok:
            print(cr.raw)
        return 0 if cr.ok else 1


def cmd_download(args: argparse.Namespace) -> int:
    strategy = DownloadStrategy(args.strategy)
    with _new_client(args) as mcp:
        tk = InoToolkit(mcp, args.project)
        tk.open_project()
        report = tk.download_program(strategy=strategy)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if "error" not in report else 1


def cmd_extract(args: argparse.Namespace) -> int:
    with _new_client(args) as mcp:
        tk = InoToolkit(mcp, args.project)
        tk.open_project()
        gvls: Optional[List[str]] = args.gvl or None
        variables = extract_gvl_variables(tk, gvl_paths=gvls, include_all=args.all)
        if not variables:
            print("⚠️  没抽到任何变量。可用 `structure` 查看树，或用 --gvl 显式指定 GVL 路径，或加 --all")
        out = write_csv(
            variables,
            args.out,
            name_mode=args.name_mode,
            ns_index=args.ns_index,
            ns_prefix=args.ns_prefix,
        )
        print(f"[extract] {len(variables)} 变量 → {out}")
    return 0


def cmd_pipeline(args: argparse.Namespace) -> int:
    # 1) extract
    with _new_client(args) as mcp:
        tk = InoToolkit(mcp, args.project)
        tk.open_project()
        variables = extract_gvl_variables(tk, gvl_paths=args.gvl or None, include_all=args.all)
        write_csv(variables, args.out, name_mode=args.name_mode,
                  ns_index=args.ns_index, ns_prefix=args.ns_prefix)
        print(f"[pipeline] extracted {len(variables)} → {args.out}")
    # 2) launch server
    if args.serve:
        server_py = Path(__file__).resolve().parents[1] / "server.py"
        cmd = runtime_command(
            "server",
            server_py,
            [
                "--csv", str(Path(args.out).resolve()),
                "--port", str(args.port), "--host", args.host,
            ],
        )
        if args.no_occupancy_true:
            cmd.append("--no-occupancy-true")
        log.info("[pipeline] 启动 server: %s", " ".join(cmd))
        return subprocess.call(cmd)
    return 0


# ---------------------------------------------------------------------------
# argparse 组装
# ---------------------------------------------------------------------------
def _add_common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--project", required=True, help=".project 文件绝对路径")
    p.add_argument("--bundle", help="InoProShop MCP bundle.min.js 路径（覆盖 mcp.json）")
    p.add_argument("--codesys-path", help="InoProShop.exe 路径（覆盖 mcp.json）")
    p.add_argument("--codesys-profile", help="InoProShop profile（覆盖 mcp.json）")
    p.add_argument("--workspace", help="工作区目录（覆盖 mcp.json）")
    p.add_argument("--node", help="Node.js 命令或绝对路径（默认 node）")
    p.add_argument("--mcp-server", default="codesys_local",
                   help="mcp.json 里的服务器名（默认 codesys_local）")
    p.add_argument("-v", "--verbose", action="store_true", help="打印 DEBUG 日志")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ino_mcp.cli",
        description="InoProShop MCP 桥 - PLC-Sim 集成命令行",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    p_open = sub.add_parser("open", help="打开 InoProShop 项目")
    p_open.add_argument("--keep-alive", action="store_true", help="保持连接直到 Ctrl+C")
    _add_common(p_open)
    p_open.set_defaults(func=cmd_open)

    p_str = sub.add_parser("structure", help="导出项目对象树")
    p_str.add_argument("-o", "--output", help="写到文件；不给则打印")
    _add_common(p_str)
    p_str.set_defaults(func=cmd_structure)

    p_g = sub.add_parser("get-pou", help="读一个 POU/GVL 的代码")
    p_g.add_argument("--pou", required=True, help="POU 路径，例如 Application/GVL_XUSE")
    p_g.add_argument("-o", "--output", help="写到文件；不给则打印")
    _add_common(p_g)
    p_g.set_defaults(func=cmd_get_pou)

    p_s = sub.add_parser("set-pou", help="写一个 POU/GVL 的代码")
    p_s.add_argument("--pou", required=True)
    p_s.add_argument("--decl", help="声明段文本（与 --decl-file 二选一）")
    p_s.add_argument("--decl-file", help="声明段文件（UTF-8）")
    p_s.add_argument("--impl", help="实现段文本（与 --impl-file 二选一）")
    p_s.add_argument("--impl-file", help="实现段文件（UTF-8）")
    p_s.add_argument("--save", action="store_true", help="写完立即 save_project")
    _add_common(p_s)
    p_s.set_defaults(func=cmd_set_pou)

    p_c = sub.add_parser("compile", help="编译项目")
    _add_common(p_c)
    p_c.set_defaults(func=cmd_compile)

    p_d = sub.add_parser("download", help="\"下载程序块\"（保存+编译，可选 online）")
    p_d.add_argument("--strategy", choices=[s.value for s in DownloadStrategy],
                     default=DownloadStrategy.SAVE_COMPILE.value)
    _add_common(p_d)
    p_d.set_defaults(func=cmd_download)

    p_e = sub.add_parser("extract", help="从 InoProShop 提取 GVL 变量到 CSV")
    p_e.add_argument("--out", required=True, help="输出 CSV 路径")
    p_e.add_argument("--gvl", action="append",
                     help="显式指定 GVL 路径（可多次）；不给则自动发现")
    p_e.add_argument("--all", action="store_true",
                     help="导出全部变量（否则仅带 symbol attribute 的）")
    p_e.add_argument("--name-mode", choices=["en", "comment"], default="en",
                     help="Name 列取英文变量名(en) 或注释里的中文名(comment)")
    p_e.add_argument("--ns-index", type=int, default=4)
    p_e.add_argument("--ns-prefix", default="uniab|",
                     help="NodeId 的 s= 前缀（默认 uniab|）")
    _add_common(p_e)
    p_e.set_defaults(func=cmd_extract)

    p_pl = sub.add_parser("pipeline", help="一键：extract → 启动 server.py")
    p_pl.add_argument("--out", required=True)
    p_pl.add_argument("--gvl", action="append")
    p_pl.add_argument("--all", action="store_true")
    p_pl.add_argument("--name-mode", choices=["en", "comment"], default="en")
    p_pl.add_argument("--ns-index", type=int, default=4)
    p_pl.add_argument("--ns-prefix", default="uniab|")
    p_pl.add_argument("--serve", action="store_true", help="紧接着启动 server.py")
    p_pl.add_argument("--host", default="0.0.0.0")
    p_pl.add_argument("--port", type=int, default=4855)
    p_pl.add_argument("--no-occupancy-true", action="store_true")
    _add_common(p_pl)
    p_pl.set_defaults(func=cmd_pipeline)

    return p


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if getattr(args, "verbose", False) else logging.INFO,
        format="%(asctime)s.%(msecs)03d [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    try:
        return int(args.func(args) or 0)
    except McpError as exc:
        log.error("MCP 错误: %s", exc)
        return 3
    except KeyboardInterrupt:
        log.info("用户中断")
        return 130


if __name__ == "__main__":
    sys.exit(main())
