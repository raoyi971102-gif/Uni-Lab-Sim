"""PyInstaller 冻结入口，同时调度验收 GUI 与 PLC-Sim 子进程。"""

from __future__ import annotations

import os
import sys
from typing import TextIO


def _configure_utf8(stream: TextIO) -> None:
    """把已有标准流切换为可写中文日志的 UTF-8 编码。"""

    reconfigure = getattr(stream, "reconfigure", None)
    if not callable(reconfigure):
        return
    try:
        reconfigure(encoding="utf-8", errors="backslashreplace")
    except (AttributeError, OSError, TypeError, ValueError):
        # 某些宿主提供的伪标准流不支持重配置；保留原流让主流程继续运行。
        return


def ensure_standard_streams() -> list[TextIO]:
    """为无控制台窗口程序补充可写标准流。

    参数：无。
    返回：本函数创建、进程退出时可关闭的标准流列表。
    """

    opened: list[TextIO] = []
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name)
        if stream is not None:
            _configure_utf8(stream)
            continue
        stream = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115 - 进程级标准流
        setattr(sys, name, stream)
        opened.append(stream)
    return opened


def main() -> int:
    """按首个参数进入 PLC-Sim 子命令或默认启动验收 GUI。

    参数：无；读取当前进程参数。
    返回：被调度入口的退出码。
    """

    ensure_standard_streams()
    from plc_sim.cli import COMMANDS
    from plc_sim.cli import main as plc_sim_main

    if len(sys.argv) > 1 and sys.argv[1] in COMMANDS:
        return plc_sim_main()
    from plc_acceptance.gui_backend import main as gui_main

    return gui_main()


if __name__ == "__main__":
    raise SystemExit(main())
