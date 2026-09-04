"""Reject incomplete or incorrectly named Modbus-Sim Windows installers."""

from __future__ import annotations

import argparse
from pathlib import Path

MEBIBYTE = 1024 * 1024
MIN_WINDOWS_INSTALLER_BYTES = 10 * MEBIBYTE


def verify_windows_installer(
    path: Path, version: str, *, minimum_bytes: int = MIN_WINDOWS_INSTALLER_BYTES
) -> int:
    expected_name = f"Modbus-Sim-Setup-Windows-x64-v{version}.exe"
    if path.name != expected_name:
        raise ValueError(
            f"Unexpected artifact name {path.name!r}; expected {expected_name!r}"
        )
    if not path.is_file():
        raise FileNotFoundError(path)
    size = path.stat().st_size
    if size < minimum_bytes:
        raise ValueError(
            f"Artifact is incomplete: {path} is {size} bytes, expected at least {minimum_bytes}"
        )
    with path.open("rb") as artifact:
        if artifact.read(2) != b"MZ":
            raise ValueError(f"Windows installer has no PE header: {path}")
    return size


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    parser.add_argument("--version", required=True)
    args = parser.parse_args()
    size = verify_windows_installer(args.path, args.version)
    print(f"Verified {args.path} ({size / MEBIBYTE:.1f} MiB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
