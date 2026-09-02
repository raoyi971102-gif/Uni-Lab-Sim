"""定位验收配置、用户数据和安装包内置资源。"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def default_kit_root() -> Path:
    """返回当前运行形态中的验收配置根目录。

    参数：无。
    返回：源码目录或 PyInstaller 冻结目录中的验收包根目录。
    异常：未找到完整配置时抛出 ``FileNotFoundError``。
    """

    configured = os.environ.get("PLC_ACCEPTANCE_KIT_ROOT")
    candidates = []
    if configured:
        candidates.append(Path(configured).expanduser())
    candidates.append(Path(__file__).resolve().parent / "bundles" / "szlab")
    candidates.append(Path(__file__).resolve().parents[1])
    frozen_root = getattr(sys, "_MEIPASS", None)
    if frozen_root:
        candidates.append(Path(frozen_root) / "plc-acceptance-kit")

    for candidate in candidates:
        resolved = candidate.resolve()
        if (resolved / "protocol" / "plc-interface.yaml").is_file():
            return resolved
    raise FileNotFoundError("未找到 PLC 自动化验收配置包")


def runtime_data_dir() -> Path:
    """返回 GUI 报告、候选包和运行日志的可写目录。

    参数：无。
    返回：可由 ``PLC_ACCEPTANCE_DATA_DIR`` 覆盖的平台用户数据目录。
    """

    configured = os.environ.get("PLC_ACCEPTANCE_DATA_DIR")
    if configured:
        return Path(configured).expanduser().resolve()
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "SZLab PLC Acceptance"
    if os.name == "nt":
        windows_root = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        if windows_root:
            return Path(windows_root).expanduser() / "SZLab PLC Acceptance"
        return Path.home() / "SZLab PLC Acceptance"
    xdg_root = os.environ.get("XDG_DATA_HOME")
    if xdg_root:
        return Path(xdg_root).expanduser() / "szlab-plc-acceptance"
    return Path.home() / ".local" / "share" / "szlab-plc-acceptance"


def reports_dir() -> Path:
    """返回自动验收报告的可写根目录。

    参数：无。
    返回：用户数据目录下的 ``reports`` 目录。
    """

    return runtime_data_dir() / "reports"
