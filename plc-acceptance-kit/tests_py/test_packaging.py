from __future__ import annotations

import importlib.util
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

    assert project_version.project_version(KIT_ROOT / "pyproject.toml") == "0.4.0"


def test_windows_installer_verifier_rejects_non_pe_payload(tmp_path: Path) -> None:
    """Windows 安装包校验不得仅凭文件名接受伪造内容。

    参数：``tmp_path`` 是隔离产物目录。
    返回：无；断言错误文件头使校验失败。
    """

    verifier = _load_packaging_module(
        "acceptance_verify_artifact", "verify_artifact.py"
    )
    artifact = tmp_path / "SZLab-PLC-Acceptance-Setup-Windows-x64-v0.3.0.exe"
    payload = bytearray(256)
    payload[:2] = b"MZ"
    payload[0x3C:0x40] = (0x80).to_bytes(4, byteorder="little")
    artifact.write_bytes(payload)

    try:
        verifier.verify_windows(artifact, "0.3.0", minimum_bytes=1)
    except ValueError as exc:
        assert "PE" in str(exc)
    else:
        raise AssertionError("伪造 EXE 必须被拒绝")


def test_windows_installer_verifier_accepts_a_valid_pe_signature(
    tmp_path: Path,
) -> None:
    """Windows 安装包校验必须接受结构完整的最小 PE 载荷。

    参数：``tmp_path`` 是隔离产物目录。
    返回：无；构造最小合法 PE 头并断言结构校验通过。
    """

    verifier = _load_packaging_module(
        "acceptance_verify_windows_artifact",
        "verify_artifact.py",
    )
    artifact = tmp_path / "SZLab-PLC-Acceptance-Setup-Windows-x64-v0.3.0.exe"
    payload = bytearray(256)
    payload[:2] = b"MZ"
    payload[0x3C:0x40] = (0x80).to_bytes(4, byteorder="little")
    payload[0x80:0x84] = b"PE\0\0"
    artifact.write_bytes(payload)

    assert (
        verifier.verify_windows(artifact, "0.3.0", minimum_bytes=1)
        == artifact.stat().st_size
    )


def test_windows_installer_is_per_user_and_launches_after_setup() -> None:
    """Windows 安装器必须无需管理员权限并在安装结束后支持一键启动。

    参数：无。
    返回：无；断言安装目录、系统版本、快捷方式和启动入口均已配置。
    """

    installer = (KIT_ROOT / "packaging" / "windows-installer.iss").read_text(
        encoding="utf-8"
    )

    assert "PrivilegesRequired=lowest" in installer
    assert "DefaultDirName={localappdata}\\Programs\\{#MyAppDirName}" in installer
    assert "MinVersion=10.0.10240" in installer
    assert 'Name: "{group}\\SZLab PLC 自动验收"' in installer
    assert 'Filename: "{app}\\{#MyAppExeName}"' in installer
    assert "postinstall" in installer


def test_installer_workflow_builds_and_smokes_windows_only() -> None:
    """验收包工作流必须只在 Windows 冻结、安装并运行完整冒烟验收。

    参数：无。
    返回：无；断言 Windows Runner、安装态验证和单平台边界均存在。
    """

    workflow = (
        REPOSITORY_ROOT / ".github" / "workflows" / "plc-acceptance-installers.yml"
    ).read_text(encoding="utf-8")

    assert "runs-on: windows-latest" in workflow
    assert "runs-on: ubuntu" not in workflow
    assert "runs-on: macos" not in workflow
    assert "'../PLC-Sim[test]' '.[test]' pyinstaller" in workflow
    assert workflow.count("packaging/smoke_frozen.py") == 2
    assert "验证真实安装目录并卸载" in workflow
    assert "unins000.exe" in workflow
    assert "SZLab-PLC-Acceptance-Setup-Windows-x64" in workflow
    assert ".deb" not in workflow
    assert ".dmg" not in workflow
    assert "--collect-all plc_acceptance" in workflow
    assert "--collect-all plc_sim" in workflow


def test_frozen_smoke_uses_the_versioned_required_case_manifest() -> None:
    """安装态冒烟不得用固定结果数量代替版本化必跑清单完整性。

    参数：无。
    返回：无；断言冻结验证逐项核对报告中的必跑用例身份。
    """

    smoke = (KIT_ROOT / "packaging" / "smoke_frozen.py").read_text(
        encoding="utf-8"
    )

    assert "required_case_ids" in smoke
    assert "required_case_ids <= passed_case_ids" in smoke
    assert '"PASSED": 105' not in smoke
