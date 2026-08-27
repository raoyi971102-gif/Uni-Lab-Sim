"""Reject incomplete or incorrectly named native release artifacts."""

from __future__ import annotations

import argparse
import tarfile
from pathlib import Path

MEBIBYTE = 1024 * 1024
MIN_WINDOWS_INSTALLER_BYTES = 10 * MEBIBYTE
MIN_MACOS_DMG_BYTES = 10 * MEBIBYTE
MIN_LINUX_ARCHIVE_BYTES = 10 * MEBIBYTE
MIN_LINUX_DEB_BYTES = 10 * MEBIBYTE


def verify_windows_installer(
    path: Path,
    version: str,
    *,
    minimum_bytes: int = MIN_WINDOWS_INSTALLER_BYTES,
) -> int:
    expected_name = f"PLC-Sim-Setup-Windows-x64-v{version}.exe"
    _verify_name_and_size(path, expected_name, minimum_bytes)
    with path.open("rb") as artifact:
        if artifact.read(2) != b"MZ":
            raise ValueError(f"Windows installer has no PE header: {path}")
    return path.stat().st_size


def verify_macos_dmg(
    path: Path,
    version: str,
    arch: str,
    *,
    minimum_bytes: int = MIN_MACOS_DMG_BYTES,
) -> int:
    if arch not in {"arm64", "x64"}:
        raise ValueError(f"Unsupported macOS architecture: {arch}")
    expected_name = f"PLC-Sim-macOS-{arch}-v{version}.dmg"
    size = _verify_name_and_size(
        path,
        expected_name,
        max(minimum_bytes, 512),
    )
    with path.open("rb") as artifact:
        artifact.seek(size - 512)
        if artifact.read(4) != b"koly":
            raise ValueError(f"macOS installer has no UDIF trailer: {path}")
    return size


def verify_linux_archive(
    path: Path,
    version: str,
    *,
    minimum_bytes: int = MIN_LINUX_ARCHIVE_BYTES,
) -> int:
    """校验 Linux x64 便携包名称、体积和冻结主程序。

    参数：``path`` 是 tar.gz 产物，``version`` 是 Release 版本，
    ``minimum_bytes`` 是允许的最小字节数。
    返回：校验通过后的产物字节数；结构不完整时抛出 ``ValueError``。
    """

    bundle_name = f"PLC-Sim-Linux-x64-v{version}"
    expected_name = f"{bundle_name}.tar.gz"
    size = _verify_name_and_size(path, expected_name, minimum_bytes)
    try:
        with tarfile.open(path, "r:gz") as artifact:
            executable = artifact.getmember(f"{bundle_name}/PLC-Sim")
    except (KeyError, tarfile.TarError) as exc:
        raise ValueError(f"Linux archive has no frozen executable: {path}") from exc
    if not executable.isfile() or executable.mode & 0o111 == 0:
        raise ValueError(f"Linux archive executable is not runnable: {path}")
    return size


def verify_linux_deb(
    path: Path,
    version: str,
    *,
    minimum_bytes: int = MIN_LINUX_DEB_BYTES,
) -> int:
    """校验 Linux x64 DEB 安装包名称、体积和 ar 文件头。

    参数：``path`` 是 DEB 产物，``version`` 是 Release 版本，
    ``minimum_bytes`` 是允许的最小字节数。
    返回：校验通过后的产物字节数；文件头错误时抛出 ``ValueError``。
    """

    expected_name = f"PLC-Sim-Linux-x64-v{version}.deb"
    size = _verify_name_and_size(path, expected_name, minimum_bytes)
    with path.open("rb") as artifact:
        if artifact.read(8) != b"!<arch>\n":
            raise ValueError(f"Linux installer has no DEB ar header: {path}")
    return size


def _verify_name_and_size(
    path: Path,
    expected_name: str,
    minimum_bytes: int,
) -> int:
    if path.name != expected_name:
        raise ValueError(
            f"Unexpected artifact name {path.name!r}; expected {expected_name!r}"
        )
    if not path.is_file():
        raise FileNotFoundError(path)
    size = path.stat().st_size
    if size < minimum_bytes:
        raise ValueError(
            f"Artifact is incomplete: {path} is {size} bytes, "
            f"expected at least {minimum_bytes}"
        )
    return size


def main() -> int:
    """解析平台参数并校验一个原生 Release 产物。

    参数：无；从命令行读取平台、路径、版本与架构。
    返回：校验成功返回 ``0``，无效产物通过异常使进程失败。
    """

    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="platform", required=True)

    windows = subparsers.add_parser("windows")
    windows.add_argument("path", type=Path)
    windows.add_argument("--version", required=True)

    macos = subparsers.add_parser("macos")
    macos.add_argument("path", type=Path)
    macos.add_argument("--version", required=True)
    macos.add_argument("--arch", choices=("arm64", "x64"), required=True)

    linux = subparsers.add_parser("linux")
    linux.add_argument("path", type=Path)
    linux.add_argument("--version", required=True)
    linux.add_argument("--kind", choices=("archive", "deb"), required=True)

    args = parser.parse_args()
    if args.platform == "windows":
        size = verify_windows_installer(args.path, args.version)
    elif args.platform == "macos":
        size = verify_macos_dmg(args.path, args.version, args.arch)
    elif args.kind == "archive":
        size = verify_linux_archive(args.path, args.version)
    else:
        size = verify_linux_deb(args.path, args.version)
    print(f"Verified {args.path} ({size / MEBIBYTE:.1f} MiB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
