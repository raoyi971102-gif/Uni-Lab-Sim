from __future__ import annotations

import importlib.util
import io
import tarfile
from pathlib import Path
from types import ModuleType

import pytest

PACKAGING_DIRECTORY = Path(__file__).parents[1] / "packaging"
VERIFY_ARTIFACT_PATH = PACKAGING_DIRECTORY / "verify_artifact.py"


def _load_verify_artifact() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "plc_sim_verify_artifact",
        VERIFY_ARTIFACT_PATH,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


VERIFY_ARTIFACT = _load_verify_artifact()


def test_verify_windows_installer_checks_name_size_and_header(tmp_path) -> None:
    installer = tmp_path / "PLC-Sim-Setup-Windows-x64-v0.2.5.exe"
    installer.write_bytes(b"MZ" + b"\0" * 30)

    assert VERIFY_ARTIFACT.verify_windows_installer(
        installer,
        "0.2.5",
        minimum_bytes=32,
    ) == 32


def test_verify_windows_installer_rejects_bad_header(tmp_path) -> None:
    installer = tmp_path / "PLC-Sim-Setup-Windows-x64-v0.2.5.exe"
    installer.write_bytes(b"NO" + b"\0" * 30)

    with pytest.raises(ValueError, match="PE header"):
        VERIFY_ARTIFACT.verify_windows_installer(
            installer,
            "0.2.5",
            minimum_bytes=32,
        )


def test_verify_windows_installer_rejects_incomplete_file(tmp_path) -> None:
    installer = tmp_path / "PLC-Sim-Setup-Windows-x64-v0.2.5.exe"
    installer.write_bytes(b"MZ")

    with pytest.raises(ValueError, match="incomplete"):
        VERIFY_ARTIFACT.verify_windows_installer(
            installer,
            "0.2.5",
            minimum_bytes=32,
        )


def test_verify_macos_dmg_checks_name_size_and_udif_trailer(tmp_path) -> None:
    installer = tmp_path / "PLC-Sim-macOS-arm64-v0.2.5.dmg"
    contents = bytearray(1024)
    contents[-512:-508] = b"koly"
    installer.write_bytes(contents)

    assert VERIFY_ARTIFACT.verify_macos_dmg(
        installer,
        "0.2.5",
        "arm64",
        minimum_bytes=1024,
    ) == 1024


def test_verify_macos_dmg_rejects_bad_trailer(tmp_path) -> None:
    installer = tmp_path / "PLC-Sim-macOS-x64-v0.2.5.dmg"
    installer.write_bytes(b"\0" * 1024)

    with pytest.raises(ValueError, match="UDIF trailer"):
        VERIFY_ARTIFACT.verify_macos_dmg(
            installer,
            "0.2.5",
            "x64",
            minimum_bytes=1024,
        )


def test_verify_macos_dmg_rejects_wrong_release_name(tmp_path) -> None:
    installer = tmp_path / "PLC-Sim-macOS-arm64-v0.2.3.dmg"
    installer.write_bytes(b"\0" * 1024)

    with pytest.raises(ValueError, match="Unexpected artifact name"):
        VERIFY_ARTIFACT.verify_macos_dmg(
            installer,
            "0.2.5",
            "arm64",
            minimum_bytes=1024,
        )


def test_verify_linux_archive_checks_name_size_and_executable(tmp_path) -> None:
    """验证 Linux 便携包包含版本化根目录和可执行冻结主程序。

    参数：``tmp_path`` 是 pytest 提供的临时产物目录。
    返回：无；断言合法 tar.gz 返回实际字节数。
    """

    bundle_name = "PLC-Sim-Linux-x64-v0.2.6"
    archive = tmp_path / f"{bundle_name}.tar.gz"
    executable = tarfile.TarInfo(f"{bundle_name}/PLC-Sim")
    executable.mode = 0o755
    executable.size = 4
    with tarfile.open(archive, "w:gz") as artifact:
        artifact.addfile(executable, io.BytesIO(b"ELF\n"))

    assert VERIFY_ARTIFACT.verify_linux_archive(
        archive,
        "0.2.6",
        minimum_bytes=archive.stat().st_size,
    ) == archive.stat().st_size


def test_verify_linux_archive_rejects_missing_executable(tmp_path) -> None:
    """验证缺少冻结主程序的 Linux 便携包被拒绝。

    参数：``tmp_path`` 是 pytest 提供的临时产物目录。
    返回：无；断言结构校验抛出 ``ValueError``。
    """

    archive = tmp_path / "PLC-Sim-Linux-x64-v0.2.6.tar.gz"
    with tarfile.open(archive, "w:gz") as artifact:
        readme = tarfile.TarInfo("PLC-Sim-Linux-x64-v0.2.6/README.txt")
        readme.size = 4
        artifact.addfile(readme, io.BytesIO(b"help"))

    with pytest.raises(ValueError, match="frozen executable"):
        VERIFY_ARTIFACT.verify_linux_archive(
            archive,
            "0.2.6",
            minimum_bytes=archive.stat().st_size,
        )


def test_verify_linux_deb_checks_name_size_and_header(tmp_path) -> None:
    """验证 Linux DEB 安装包名称、体积和 ar 文件头。

    参数：``tmp_path`` 是 pytest 提供的临时产物目录。
    返回：无；断言合法 DEB 返回实际字节数。
    """

    installer = tmp_path / "PLC-Sim-Linux-x64-v0.2.6.deb"
    installer.write_bytes(b"!<arch>\n" + b"\0" * 24)

    assert VERIFY_ARTIFACT.verify_linux_deb(
        installer,
        "0.2.6",
        minimum_bytes=32,
    ) == 32


def test_verify_linux_deb_rejects_bad_header(tmp_path) -> None:
    """验证伪造文件头的 Linux DEB 安装包被拒绝。

    参数：``tmp_path`` 是 pytest 提供的临时产物目录。
    返回：无；断言文件头校验抛出 ``ValueError``。
    """

    installer = tmp_path / "PLC-Sim-Linux-x64-v0.2.6.deb"
    installer.write_bytes(b"not-deb\n" + b"\0" * 24)

    with pytest.raises(ValueError, match="DEB ar header"):
        VERIFY_ARTIFACT.verify_linux_deb(
            installer,
            "0.2.6",
            minimum_bytes=32,
        )
