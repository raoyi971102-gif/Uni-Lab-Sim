"""Read ``project.version`` without importing the package under construction."""

from __future__ import annotations

import argparse
import re
from pathlib import Path


VERSION_LINE = re.compile(r'version\s*=\s*"([^"]+)"\s*(?:#.*)?$')


def read_project_version(path: Path) -> str:
    in_project_section = False
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if line.startswith("[") and line.endswith("]"):
            in_project_section = line == "[project]"
            continue
        if not in_project_section:
            continue
        match = VERSION_LINE.fullmatch(line)
        if match:
            return match.group(1)
    raise ValueError(f"Missing project.version in {path}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("pyproject", type=Path)
    args = parser.parse_args()
    print(read_project_version(args.pyproject))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
