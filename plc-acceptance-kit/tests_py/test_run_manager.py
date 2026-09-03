from __future__ import annotations

from pathlib import Path

import pytest
from plc_acceptance import run_manager as run_manager_module
from plc_acceptance.models import RunResult
from plc_acceptance.resources import default_kit_root
from plc_acceptance.run_manager import AcceptanceRunManager


@pytest.mark.parametrize(
    ("mode", "environment_name", "evidence_level"),
    [
        ("bench", "bench", "L3 真机台架证据"),
        ("fat_sat", "fat-sat", "L4 FAT/SAT 现场证据"),
    ],
)
def test_real_device_modes_load_their_own_environment_and_publish_evidence(
    tmp_path: Path,
    monkeypatch,
    mode: str,
    environment_name: str,
    evidence_level: str,
) -> None:
    """GUI 运行管理器不得再把 L3/L4 降级装载成 L2 软 PLC。"""

    captured: dict[str, object] = {}
    real_load_bundle = run_manager_module.load_bundle

    def capture_bundle(*args, **kwargs):  # type: ignore[no-untyped-def]
        captured.update(kwargs)
        return real_load_bundle(*args, **kwargs)

    def fake_run_acceptance(bundle, **kwargs):  # type: ignore[no-untyped-def]
        captured["evidence_metadata"] = kwargs["evidence_metadata"]
        return RunResult(
            run_id=f"plc-{mode}-unit",
            project_id=bundle.project_id,
            protocol_version=bundle.protocol_version,
            environment_id=bundle.environment.environment_id,
            evidence_level=bundle.environment.evidence_level,
            status="PASSED",
            started_at="2026-09-03T00:00:00+00:00",
            ended_at="2026-09-03T00:00:01+00:00",
        )

    monkeypatch.setattr(run_manager_module, "load_bundle", capture_bundle)
    monkeypatch.setattr(run_manager_module, "run_acceptance", fake_run_acceptance)
    manager = AcceptanceRunManager(
        kit_root=default_kit_root(),
        output_root=tmp_path,
    )

    manager._run(
        mode=mode,
        endpoint="opc.tcp://192.168.1.20:4840/",
        namespace_uri="urn:szlab:real-plc",
        confirm_safe_test_mode=True,
        plc_artifact=tmp_path / "candidate.zip",
        evidence_metadata={
            "supervisor": "供应商张工",
            "test_location": "SZLab 现场",
        },
    )

    snapshot = manager.snapshot()
    assert captured["environment_name"] == environment_name
    assert captured["namespace_uri_override"] == "urn:szlab:real-plc"
    assert captured["evidence_metadata"] == {
        "supervisor": "供应商张工",
        "test_location": "SZLab 现场",
    }
    assert snapshot["state"] == "PASSED"
    assert snapshot["report"]["evidence_level"] == evidence_level
    assert "现场关闭" in snapshot["message"]
