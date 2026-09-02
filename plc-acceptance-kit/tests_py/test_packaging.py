from __future__ import annotations

import importlib.util
import io
import tarfile
from pathlib import Path

KIT_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = KIT_ROOT.parent


def _load_packaging_module(name: str, filename: str):
    """从未安装的 packaging 目录加载一个构建模块。

    参数：``name`` 是隔离模块名，``filename`` 是目录内文件名。
    返回：已执行的 Python 模块对象。
    """

    path = KIT_ROOT / "packaging" / filename
    specification = importlib.util.spec_from_file_location(name, path)
    if specification is None or specification.loader is None:
        raise RuntimeError(f"无法加载打包模块: {path}")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def test_project_version_matches_the_acceptance_distribution() -> None:
    """安装器版本必须与验收 Python 分发版本完全一致。

    参数：无。
    返回：无；断言 pyproject 读取结果为当前发布版本。
    """

    project_version = _load_packaging_module(
        "acceptance_project_version",
        "project_version.py",
    )

    assert project_version.project_version(KIT_ROOT / "pyproject.toml") == "0.2.0"


def test_windows_installer_verifier_rejects_non_pe_payload(tmp_path: Path) -> None:
    """Windows 安装包校验不得仅凭文件名接受伪造内容。

    参数：``tmp_path`` 是隔离产物目录。
    返回：无；断言错误文件头使校验失败。
    """

    verifier = _load_packaging_module(
        "acceptance_verify_artifact", "verify_artifact.py"
    )
    artifact = tmp_path / "SZLab-PLC-Acceptance-Setup-Windows-x64-v0.2.0.exe"
    artifact.write_bytes(b"NO" + b"\0" * 32)

    try:
        verifier.verify_windows(artifact, "0.2.0", minimum_bytes=1)
    except ValueError as exc:
        assert "PE" in str(exc)
    else:
        raise AssertionError("伪造 EXE 必须被拒绝")


def test_linux_archive_verifier_requires_the_frozen_executable(tmp_path: Path) -> None:
    """Linux 便携包必须包含版本化目录和可执行冻结主程序。

    参数：``tmp_path`` 是隔离产物目录。
    返回：无；构造最小合法归档并断言结构校验通过。
    """

    verifier = _load_packaging_module(
        "acceptance_verify_linux_artifact",
        "verify_artifact.py",
    )
    bundle = "SZLab-PLC-Acceptance-Linux-x64-v0.2.0"
    artifact = tmp_path / f"{bundle}.tar.gz"
    executable = tarfile.TarInfo(f"{bundle}/SZLab-PLC-Acceptance")
    executable.size = 4
    executable.mode = 0o755
    with tarfile.open(artifact, "w:gz") as archive:
        archive.addfile(executable, io.BytesIO(b"ELF!"))

    assert (
        verifier.verify_linux_archive(
            artifact,
            "0.2.0",
            minimum_bytes=1,
        )
        == artifact.stat().st_size
    )


def test_installer_workflow_builds_and_smokes_all_supported_platforms() -> None:
    """安装包工作流必须在三类系统冻结应用并运行完整冒烟验收。

    参数：无。
    返回：无；断言原生 Runner、两个分发包和冻结冒烟入口均存在。
    """

    workflow = (
        REPOSITORY_ROOT / ".github" / "workflows" / "plc-acceptance-installers.yml"
    ).read_text(encoding="utf-8")

    assert "runs-on: windows-latest" in workflow
    assert "runs-on: ubuntu-22.04" in workflow
    assert "runner: macos-15" in workflow
    assert "../PLC-Sim . pyinstaller" in workflow
    assert workflow.count("packaging/smoke_frozen.py") == 3
    assert "--collect-all plc_acceptance" in workflow
    assert "--collect-all plc_sim" in workflow
