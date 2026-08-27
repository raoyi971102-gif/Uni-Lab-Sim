"""二进制 ``.project`` 工程的内容寻址快照、部署台账与安全恢复。"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class ProjectVersionRepo:
    """每个工程独立保存不可变全量快照，元数据写入采用原子替换。"""

    def __init__(self, project: Path, root: Path) -> None:
        self.project = project.expanduser().resolve()
        project_key = hashlib.sha256(str(self.project).casefold().encode("utf-8")).hexdigest()[:16]
        self.root = root.expanduser().resolve() / project_key
        self.snapshots = self.root / "snapshots"
        self.meta_path = self.root / "history.json"

    def _load(self) -> list[dict[str, Any]]:
        try:
            payload = json.loads(self.meta_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return []
        if not isinstance(payload, list):
            raise ValueError(f"PLC 工程历史损坏: {self.meta_path}")
        return payload

    def _save(self, history: list[dict[str, Any]]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix="history-", suffix=".json", dir=self.root)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(history, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_name, self.meta_path)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)

    def history(self) -> list[dict[str, Any]]:
        return [dict(item) for item in self._load()]

    def current_sha256(self) -> str:
        if not self.project.is_file():
            raise FileNotFoundError(self.project)
        return file_sha256(self.project)

    def snapshot_if_changed(self, message: str = "") -> dict[str, Any] | None:
        sha = self.current_sha256()
        history = self._load()
        if history and history[-1].get("sha256") == sha:
            return None
        self.snapshots.mkdir(parents=True, exist_ok=True)
        rev = f"{len(history) + 1:06d}"
        destination = self.snapshots / f"{rev}-{sha}.project"
        shutil.copy2(self.project, destination)
        item: dict[str, Any] = {
            "rev": rev,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "sha256": sha,
            "size": destination.stat().st_size,
            "message": str(message),
            "deployed_at": None,
            "snapshot": destination.name,
            "project": str(self.project),
        }
        history.append(item)
        self._save(history)
        return dict(item)

    def mark_deployed(self, sha256: str) -> dict[str, Any]:
        history = self._load()
        for item in reversed(history):
            if item.get("sha256") == sha256:
                item["deployed_at"] = datetime.now(timezone.utc).isoformat()
                self._save(history)
                return dict(item)
        raise KeyError(f"没有与部署哈希匹配的工程快照: {sha256}")

    def _entry(self, rev: str) -> dict[str, Any]:
        for item in self._load():
            if str(item.get("rev")) == str(rev):
                return item
        raise KeyError(rev)

    def version_path(self, rev: str) -> Path:
        item = self._entry(rev)
        path = (self.snapshots / str(item["snapshot"])).resolve()
        if path.parent != self.snapshots.resolve() or not path.is_file():
            raise FileNotFoundError(path)
        if file_sha256(path) != item["sha256"]:
            raise ValueError(f"PLC 工程快照校验失败: rev={rev}")
        return path

    def restore(self, rev: str) -> dict[str, Any]:
        source = self.version_path(rev)
        self.snapshot_if_changed(message=f"恢复 rev {rev} 前自动快照")
        self.project.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(
            prefix=f".{self.project.name}.", suffix=".restore", dir=self.project.parent
        )
        os.close(fd)
        try:
            shutil.copy2(source, temp_name)
            os.replace(temp_name, self.project)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)
        return {
            "restored": True,
            "rev": str(rev),
            "sha256": self.current_sha256(),
            "project": str(self.project),
        }

