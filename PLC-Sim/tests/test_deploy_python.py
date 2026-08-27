from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest


requires_unix_shell = pytest.mark.skipif(
    os.name == "nt",
    reason="ensure_deploy_venv.sh 是 Unix 部署脚本，Windows 由 setup_venv.bat 覆盖",
)


PROJECT_DIRECTORY = Path(__file__).parents[1]
REPOSITORY_DIRECTORY = PROJECT_DIRECTORY.parent
MIGRATION_SCRIPT = PROJECT_DIRECTORY / "scripts" / "ensure_deploy_venv.sh"


def test_deploy_entrypoints_use_shared_python311_migration() -> None:
    github_workflow = (
        REPOSITORY_DIRECTORY / ".github" / "workflows" / "deploy.yml"
    ).read_text(encoding="utf-8")
    cnb_script = (PROJECT_DIRECTORY / "scripts" / "cnb_post_deploy.sh").read_text(
        encoding="utf-8"
    )

    assert 'bash scripts/ensure_deploy_venv.sh "$REMOTE_DIR"' in github_workflow
    assert 'bash scripts/ensure_deploy_venv.sh "$ROOT"' in cnb_script


def test_deploy_entrypoints_use_plc_sim_paths_and_service() -> None:
    """验证双端部署使用统一的远程目录与 systemd 单元名称。"""

    github_workflow = (
        REPOSITORY_DIRECTORY / ".github" / "workflows" / "deploy.yml"
    ).read_text(encoding="utf-8")
    cnb_script = (PROJECT_DIRECTORY / "scripts" / "cnb_post_deploy.sh").read_text(
        encoding="utf-8"
    )

    assert "REMOTE_DIR: /www/wwwroot/PLC-Sim" in github_workflow
    assert "ROOT=/www/wwwroot/PLC-Sim" in cnb_script
    assert "systemctl restart plcsim-gui" in cnb_script


def _write_fake_python(path: Path, *, version: str, accept_version_check: bool) -> None:
    check_status = 0 if accept_version_check else 1
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "--version" ]; then\n'
        f"  echo 'Python {version}'\n"
        "  exit 0\n"
        "fi\n"
        'if [ "$1" = "-c" ]; then\n'
        f"  exit {check_status}\n"
        "fi\n"
        "exit 42\n",
        encoding="utf-8",
    )
    path.chmod(0o755)


@requires_unix_shell
def test_deploy_migrates_an_old_venv_atomically(tmp_path: Path) -> None:
    (tmp_path / "requirements.txt").write_text("", encoding="utf-8")
    old_python = tmp_path / ".venv" / "bin" / "python"
    _write_fake_python(old_python, version="3.10.12", accept_version_check=False)
    (tmp_path / ".venv" / "legacy-marker").write_text("keep", encoding="utf-8")

    result = subprocess.run(
        ["bash", str(MIGRATION_SCRIPT), str(tmp_path)],
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, "PLCSIM_PYTHON": sys.executable},
    )

    subprocess.run(
        [
            str(tmp_path / ".venv" / "bin" / "python"),
            "-c",
            "import sys; assert sys.version_info[:2] == (3, 11)",
        ],
        check=True,
    )
    backups = list(tmp_path.glob(".venv-before-python311-*"))
    assert len(backups) == 1
    assert (backups[0] / "legacy-marker").read_text(encoding="utf-8") == "keep"
    assert "Previous environment retained temporarily" in result.stdout


@requires_unix_shell
def test_deploy_preserves_old_venv_when_staging_fails(tmp_path: Path) -> None:
    (tmp_path / "requirements.txt").write_text("", encoding="utf-8")
    old_python = tmp_path / ".venv" / "bin" / "python"
    _write_fake_python(old_python, version="3.10.12", accept_version_check=False)
    (tmp_path / ".venv" / "legacy-marker").write_text("keep", encoding="utf-8")
    broken_python = tmp_path / "fake-python311"
    _write_fake_python(broken_python, version="3.11.9", accept_version_check=True)

    result = subprocess.run(
        ["bash", str(MIGRATION_SCRIPT), str(tmp_path)],
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "PLCSIM_PYTHON": str(broken_python)},
    )

    assert result.returncode != 0
    assert (tmp_path / ".venv" / "legacy-marker").read_text(encoding="utf-8") == "keep"
    assert not list(tmp_path.glob(".venv-before-python311-*"))
    assert not list(tmp_path.glob(".venv-python311-new.*"))
