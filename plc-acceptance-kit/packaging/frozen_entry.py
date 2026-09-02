"""PyInstaller 冻结入口，同时调度验收 GUI 与 PLC-Sim 子进程。"""

from __future__ import annotations

import os
import sys
from typing import TextIO


def ensure_standard_streams() -> list[TextIO]:
    """为无控制台窗口程序补充可写标准流。

    参数：无。
    返回：本函数创建、进程退出时可关闭的标准流列表。
    """

    opened: list[TextIO] = []
    for name in ("stdout", "stderr"):
        if getattr(sys, name) is not None:
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
