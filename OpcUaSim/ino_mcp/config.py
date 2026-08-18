"""InoProShop MCP 的可移植配置解析。"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any, Dict, Iterable, Optional


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _mcp_config_files() -> Iterable[Path]:
    explicit = os.environ.get("OPCUASIM_MCP_CONFIG")
    if explicit:
        yield Path(explicit).expanduser()
    yield Path.home() / ".cursor" / "mcp.json"
    yield Path.home() / ".mcp.json"


def _read_server_config(server_name: str) -> Dict[str, Any]:
    """读取第一个包含指定 server 的 MCP JSON 配置。"""
    for path in _mcp_config_files():
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            continue
        server = (data.get("mcpServers") or {}).get(server_name)
        if not isinstance(server, dict):
            continue

        result: Dict[str, Any] = {}
        command = server.get("command")
        if command:
            result["node_cmd"] = str(command)
        args = list(server.get("args") or [])
        if args and str(args[0]).lower().endswith(".js"):
            result["bundle_js"] = str(args[0])
        iterator = iter(args)
        for value in iterator:
            if value == "--codesys-path":
                result["codesys_path"] = next(iterator, None)
            elif value == "--codesys-profile":
                result["codesys_profile"] = next(iterator, None)
            elif value == "--workspace":
                result["workspace"] = next(iterator, None)
        return {key: value for key, value in result.items() if value}
    return {}


def _first_existing(paths: Iterable[Path]) -> Optional[str]:
    for path in paths:
        if path.exists():
            return str(path.resolve())
    return None


def resolve_mcp_config(
    *,
    project: Optional[str] = None,
    server_name: str = "codesys_local",
    overrides: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """按“内置探测 < MCP JSON < 环境变量 < 显式参数”合并配置。"""
    workspace = str(Path(project).expanduser().resolve().parent) if project else str(Path.cwd())
    cfg: Dict[str, Any] = {
        "bundle_js": None,
        "codesys_path": None,
        "codesys_profile": "InoProShop(V1.9.1.6)",
        "workspace": workspace,
        "node_cmd": "node",
    }

    local_bundle = _first_existing(
        (
            PROJECT_ROOT / "vendor" / "inoproshop-mcp" / "persistent-launcher.js",
            PROJECT_ROOT / "vendor" / "inoproshop-mcp" / "bundle.min.js",
            PROJECT_ROOT.parent / "InoProShop_LIMIT_MCP-main"
            / "InoProShop_LIMIT_MCP-main" / "bundle.min.js",
        )
    )
    if local_bundle:
        cfg["bundle_js"] = local_bundle

    program_files = [
        os.environ.get("ProgramFiles"),
        os.environ.get("ProgramW6432"),
        r"C:\Program Files",
        r"D:\Program Files",
    ]
    codesys_candidates = [
        Path(base) / "Inovance Control" / "InoProShop" / "CODESYS"
        / "Common" / "InoProShop.exe"
        for base in program_files
        if base
    ]
    cfg["codesys_path"] = _first_existing(codesys_candidates)

    # User-level MCP files often outlive a moved or deleted checkout.  Do not let
    # stale file paths hide a working repository-local bundle or an automatically
    # detected InoProShop installation.  Environment variables and explicit
    # arguments below remain strict overrides so configuration mistakes there are
    # still reported to the caller.
    mcp_values = _read_server_config(server_name)
    for key, value in mcp_values.items():
        if key in ("bundle_js", "codesys_path"):
            if not Path(value).expanduser().exists():
                continue
        cfg[key] = value

    env_values = {
        "bundle_js": os.environ.get("OPCUASIM_MCP_BUNDLE"),
        "codesys_path": os.environ.get("OPCUASIM_INOPROSHOP_EXE"),
        "codesys_profile": os.environ.get("OPCUASIM_INOPROSHOP_PROFILE"),
        "workspace": os.environ.get("OPCUASIM_MCP_WORKSPACE"),
        "node_cmd": os.environ.get("OPCUASIM_NODE"),
    }
    cfg.update({key: value for key, value in env_values.items() if value})
    cfg.update({key: value for key, value in (overrides or {}).items() if value})

    for key in ("bundle_js", "codesys_path", "workspace"):
        if cfg.get(key):
            cfg[key] = str(Path(cfg[key]).expanduser().resolve())
    if not cfg.get("node_cmd"):
        cfg["node_cmd"] = shutil.which("node") or "node"
    return cfg
