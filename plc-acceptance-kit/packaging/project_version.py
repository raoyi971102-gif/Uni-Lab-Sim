"""从 pyproject.toml 读取安装包版本。"""

from __future__ import annotations

import argparse
from pathlib import Path

import tomllib


def project_version(path: Path) -> str:
    """读取 Python 项目的发布版本。

    参数：``path`` 是 ``pyproject.toml`` 路径。
    返回：``project.version`` 文本。
    """

    payload = tomllib.loads(path.read_text(encoding="utf-8"))
    return str(payload["project"]["version"])


def main() -> int:
    """输出命令行指定项目的版本。

    参数：无；从命令行读取 pyproject 路径。
    返回：成功输出版本后返回 ``0``。
    """

    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    arguments = parser.parse_args()
    print(project_version(arguments.path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
