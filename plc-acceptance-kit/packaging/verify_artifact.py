"""拒绝不完整或命名错误的原生验收安装包。"""

from __future__ import annotations

import argparse
import tarfile
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
    """校验 Windows 安装程序名称、体积和 PE 文件头。

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
    return size


def verify_linux_archive(
    path: Path, version: str, minimum_bytes: int = MIN_ARTIFACT_BYTES
) -> int:
    """校验 Linux 便携包名称、体积和冻结可执行文件。

    参数：``path`` 是 tar.gz，``version`` 是版本，``minimum_bytes`` 是体积下限。
    返回：有效产物字节数。
    """

    bundle = f"SZLab-PLC-Acceptance-Linux-x64-v{version}"
    size = _verify_name_and_size(path, f"{bundle}.tar.gz", minimum_bytes)
    try:
        with tarfile.open(path, "r:gz") as artifact:
            executable = artifact.getmember(f"{bundle}/SZLab-PLC-Acceptance")
    except (KeyError, tarfile.TarError) as exc:
        raise ValueError(f"Linux 便携包缺少冻结主程序: {path}") from exc
    if not executable.isfile() or executable.mode & 0o111 == 0:
        raise ValueError(f"Linux 便携包主程序不可执行: {path}")
    return size


def verify_linux_deb(
    path: Path, version: str, minimum_bytes: int = MIN_ARTIFACT_BYTES
) -> int:
    """校验 Linux DEB 名称、体积和 ar 文件头。

    参数：``path`` 是 DEB，``version`` 是版本，``minimum_bytes`` 是体积下限。
    返回：有效产物字节数。
    """

    size = _verify_name_and_size(
        path,
        f"SZLab-PLC-Acceptance-Linux-x64-v{version}.deb",
        minimum_bytes,
    )
    with path.open("rb") as artifact:
        if artifact.read(8) != b"!<arch>\n":
            raise ValueError(f"Linux 安装包没有 DEB ar 文件头: {path}")
    return size


def verify_macos(
    path: Path,
    version: str,
    arch: str,
    minimum_bytes: int = MIN_ARTIFACT_BYTES,
) -> int:
    """校验 macOS DMG 名称、体积和 UDIF 尾标。

    参数：``path`` 是 DMG，``version`` 是版本，``arch`` 是架构，
    ``minimum_bytes`` 是体积下限。
    返回：有效产物字节数。
    """

    if arch not in {"arm64", "x64"}:
        raise ValueError(f"不支持的 macOS 架构: {arch}")
    size = _verify_name_and_size(
        path,
        f"SZLab-PLC-Acceptance-macOS-{arch}-v{version}.dmg",
        max(minimum_bytes, 512),
    )
    with path.open("rb") as artifact:
        artifact.seek(size - 512)
        if artifact.read(4) != b"koly":
            raise ValueError(f"macOS 安装包没有 UDIF 尾标: {path}")
    return size


def main() -> int:
    """按平台选择产物校验器。

    参数：无；从命令行读取平台、路径、版本和架构。
    返回：校验成功返回 ``0``。
    """

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "platform", choices=("windows", "linux-archive", "linux-deb", "macos")
    )
    parser.add_argument("path", type=Path)
    parser.add_argument("--version", required=True)
    parser.add_argument("--arch", choices=("arm64", "x64"))
    arguments = parser.parse_args()
    if arguments.platform == "windows":
        size = verify_windows(arguments.path, arguments.version)
    elif arguments.platform == "linux-archive":
        size = verify_linux_archive(arguments.path, arguments.version)
    elif arguments.platform == "linux-deb":
        size = verify_linux_deb(arguments.path, arguments.version)
    else:
        if not arguments.arch:
            parser.error("macos 校验必须提供 --arch")
        size = verify_macos(arguments.path, arguments.version, arguments.arch)
    print(f"Verified {arguments.path} ({size / MEBIBYTE:.1f} MiB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
