"""PyInstaller entry point for the native PLC-Sim desktop application."""

from __future__ import annotations

import os
import sys
from typing import TextIO


def ensure_standard_streams() -> list[TextIO]:
    """Provide writable streams when a windowed executable has no console."""
    opened: list[TextIO] = []
    for name in ("stdout", "stderr"):
        if getattr(sys, name) is not None:
            continue
        stream = open(os.devnull, "w", encoding="utf-8")
        setattr(sys, name, stream)
        opened.append(stream)
    return opened


if __name__ == "__main__":
    ensure_standard_streams()
    from plc_sim.cli import main

    raise SystemExit(main())
