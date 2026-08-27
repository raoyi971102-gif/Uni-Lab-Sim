from __future__ import annotations

from pathlib import Path

import pytest

from ino_mcp.project_versions import ProjectVersionRepo


def test_project_version_snapshot_restore_and_deploy_ledger(tmp_path: Path) -> None:
    project = tmp_path / "demo.project"
    project.write_bytes(b"version-one")
    repo = ProjectVersionRepo(project, tmp_path / "history")

    first = repo.snapshot_if_changed("baseline")
    assert first is not None and first["rev"] == "000001"
    assert repo.snapshot_if_changed("no change") is None

    project.write_bytes(b"version-two")
    second = repo.snapshot_if_changed("edited")
    assert second is not None and second["rev"] == "000002"
    deployed = repo.mark_deployed(second["sha256"])
    assert deployed["deployed_at"]

    restored = repo.restore("000001")
    assert restored["restored"] is True
    assert project.read_bytes() == b"version-one"
    # 恢复前的 version-two 已有快照，不会产生重复内容版本。
    assert len(repo.history()) == 2


def test_project_version_detects_tampered_snapshot(tmp_path: Path) -> None:
    project = tmp_path / "demo.project"
    project.write_bytes(b"safe")
    repo = ProjectVersionRepo(project, tmp_path / "history")
    item = repo.snapshot_if_changed("baseline")
    assert item is not None
    repo.version_path(item["rev"]).write_bytes(b"tampered")
    with pytest.raises(ValueError, match="校验失败"):
        repo.restore(item["rev"])

