from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType


FROZEN_ENTRY_PATH = (
    Path(__file__).parents[1] / "packaging" / "frozen_entry.py"
)


def _load_frozen_entry() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "plc_sim_frozen_entry",
        FROZEN_ENTRY_PATH,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_frozen_entry_repairs_missing_console_streams(monkeypatch) -> None:
    frozen_entry = _load_frozen_entry()

    with monkeypatch.context() as no_console:
        no_console.setattr(sys, "stdout", None)
        no_console.setattr(sys, "stderr", None)
        opened = frozen_entry.ensure_standard_streams()

        assert sys.stdout is opened[0]
        assert sys.stderr is opened[1]
        # Windows PTY 下即使目标是 NUL，isatty() 也可能返回 True；这里的契约是
        # 冻结 GUI 进程获得可写文本流，而不是伪造终端属性。
        assert sys.stdout.writable()
        assert sys.stderr.writable()

    for stream in opened:
        stream.close()


def test_frozen_entry_preserves_existing_console_streams() -> None:
    frozen_entry = _load_frozen_entry()

    assert frozen_entry.ensure_standard_streams() == []
