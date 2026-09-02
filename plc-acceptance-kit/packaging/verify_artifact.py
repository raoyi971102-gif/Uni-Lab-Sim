"""拒绝不完整或命名错误的原生验收安装包。"""

from __future__ import annotations

import argparse
from pathlib import Path

MEBIBYTE = 1024 * 1024
MIN_ARTIFACT_BYTES = 10 * MEBIBYTE


def _verify_name_and_size(path: Path, expected_name: str, minimum_bytes: int) -> int:
    """校验产物名称、存在性和最小体积。

    参数：``path`` 是产物，``expected_name`` 是标准名称，``minimum_bytes`` 是下限。
    返回：有效产物字节数。
    """

    if path.name != expected_name:
        raise ValueError(f"产物名称 {path.name!r}，期望 {expected_name!r}")
    if not path.is_file():
        raise FileNotFoundError(path)
    size = path.stat().st_size
    if size < minimum_bytes:
        raise ValueError(f"产物不完整: {path} 只有 {size} 字节")
    return size


def verify_windows(
    path: Path, version: str, minimum_bytes: int = MIN_ARTIFACT_BYTES
) -> int:
    """校验 Windows 安装程序名称、体积和 PE 签名。

    参数：``path`` 是 EXE，``version`` 是版本，``minimum_bytes`` 是体积下限。
    返回：有效产物字节数。
    """

    size = _verify_name_and_size(
        path,
        f"SZLab-PLC-Acceptance-Setup-Windows-x64-v{version}.exe",
        minimum_bytes,
    )
    with path.open("rb") as artifact:
        if artifact.read(2) != b"MZ":
            raise ValueError(f"Windows 安装包没有 PE 文件头: {path}")
        artifact.seek(0x3C)
        pe_offset_bytes = artifact.read(4)
        if len(pe_offset_bytes) != 4:
            raise ValueError(f"Windows 安装包缺少 PE 偏移量: {path}")
        pe_offset = int.from_bytes(pe_offset_bytes, byteorder="little")
        if pe_offset < 0x40 or pe_offset > size - 4:
            raise ValueError(f"Windows 安装包 PE 偏移量无效: {path}")
        artifact.seek(pe_offset)
        if artifact.read(4) != b"PE\0\0":
            raise ValueError(f"Windows 安装包没有有效 PE 签名: {path}")
    return size


def main() -> int:
    """按平台选择产物校验器。

    参数：无；从命令行读取平台、路径、版本和架构。
    返回：校验成功返回 ``0``。
    """

    parser = argparse.ArgumentParser()
    parser.add_argument("platform", choices=("windows",))
    parser.add_argument("path", type=Path)
    parser.add_argument("--version", required=True)
    arguments = parser.parse_args()
    size = verify_windows(arguments.path, arguments.version)
    print(f"Verified {arguments.path} ({size / MEBIBYTE:.1f} MiB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
