from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from package_simulation import (
    PackageSimulationRuntime,
    SimulationClock,
    WorldState,
    WorldStateError,
    coverage_from_groups,
    write_snapshot_atomic,
)
from szlab_package_runtime import SzlabPackageRuntime, load_szlab_coverage


class FakeMonotonic:
    def __init__(self) -> None:
        self.value = 100.0

    def __call__(self) -> float:
        return self.value


def test_clock_changes_rate_without_jumping_simulated_time() -> None:
    source = FakeMonotonic()
    clock = SimulationClock(2.0, source=source)
    source.value += 3.0
    assert clock.now() == pytest.approx(6.0)

    clock.rate = 4.0
    assert clock.now() == pytest.approx(6.0)
    source.value += 2.0
    assert clock.now() == pytest.approx(14.0)
    assert clock.real_delay(20.0) == pytest.approx(5.0)


def test_world_state_enforces_unique_placement_and_nonnegative_quantity() -> None:
    world = WorldState(
        sites={"S03:L1B1": "beaker-1", "S06": None},
        quantities={"reagent-1.ml": 100.0},
    )
    assert world.move("S03:L1B1", "S06") == "beaker-1"
    assert world.adjust_quantity("reagent-1.ml", -25.5) == pytest.approx(74.5)

    with pytest.raises(WorldStateError, match="已位于"):
        world.occupy("S04:1", "beaker-1")
    with pytest.raises(WorldStateError, match="不得小于"):
        world.adjust_quantity("reagent-1.ml", -100.0)

    snapshot = world.snapshot()
    assert snapshot["sites"] == {"S03:L1B1": None, "S06": "beaker-1"}
    assert snapshot["quantities"]["reagent-1.ml"] == pytest.approx(74.5)


def test_runtime_tracks_one_device_run_and_keeps_bounded_ordered_events() -> None:
    source = FakeMonotonic()
    runtime = PackageSimulationRuntime(
        "community.szlab_poly_studio",
        clock=SimulationClock(source=source),
        history_limit=3,
    )
    accepted = runtime.record("s06", "pump.add", "accepted", {"volume_ml": 5})
    source.value += 1
    runtime.record("s06", "pump.add", "running")
    source.value += 1
    completed = runtime.record("s06", "pump.add", "completed")
    source.value += 1
    reset = runtime.record("s06", "pump.add", "reset")

    snapshot = runtime.snapshot()
    assert accepted.run_id == completed.run_id == reset.run_id
    assert snapshot["sequence"] == 4
    assert [event["sequence"] for event in snapshot["events"]] == [2, 3, 4]
    assert snapshot["active_runs"] == []
    assert snapshot["recent_runs"][0]["state"] == "SUCCEEDED"
    assert snapshot["world"]["devices"]["s06"]["phase"] == "reset"


def test_runtime_rejects_overlapping_runs_on_the_same_device() -> None:
    runtime = PackageSimulationRuntime("pkg")
    runtime.record("robot", "robot.pick", "accepted")
    with pytest.raises(RuntimeError, match="已有活动运行"):
        runtime.record("robot", "robot.place", "accepted")
    with pytest.raises(RuntimeError, match="活动动作是"):
        runtime.record("robot", "robot.place", "completed")

    runtime.record("robot", "robot.pick", "reset")
    assert runtime.snapshot()["recent_runs"][0]["state"] == "CANCELED"


def test_coverage_is_fail_closed_and_snapshot_is_atomic(tmp_path: Path) -> None:
    coverage = coverage_from_groups(
        modeled=["robot.pick"],
        query=["plc.status"],
        external=["s1.login"],
    )
    assert coverage.status("robot.pick") == "modeled"
    assert coverage.status("missing.action") == "unsupported"
    with pytest.raises(ValueError, match="分类重复"):
        coverage_from_groups(modeled=["same"], query=["same"])

    runtime = PackageSimulationRuntime("pkg", coverage=coverage)
    target = tmp_path / "runtime" / "state.json"
    write_snapshot_atomic(target, runtime.snapshot())
    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["schema"] == "unilab.package_simulation/v1"
    assert payload["coverage"]["counts"]["modeled"] == 1
    assert not list(target.parent.glob("*.tmp"))


def test_szlab_catalog_snapshot_classifies_all_real_actions() -> None:
    coverage = load_szlab_coverage()
    snapshot = coverage.snapshot()
    assert snapshot["total"] == 105
    assert snapshot["counts"] == {
        "delegated": 10,
        "external": 16,
        "modeled": 62,
        "query": 17,
        "unsupported": 0,
    }
    assert coverage.status("szlab_mixer_robot.submit_place_to_s06") == "modeled"
    assert coverage.status("s1_workstation.login") == "external"


def test_szlab_adapter_projects_protocol_events_into_one_package_session() -> None:
    class Event:
        def __init__(self, phase: str, detail: dict | None = None) -> None:
            self.action = "szlab_mixer_robot.place"
            self.phase = phase
            self.detail = detail or {}

    runtime = SzlabPackageRuntime(time_scale=5)
    runtime.observe(Event("accepted", {"task_number": 11}))
    runtime.observe(
        Event(
            "completed",
            {
                "task_number": 11,
                "sensor": "传感器状态_上位机[3].NO[1]",
                "occupied": True,
                "site_witness_enabled": True,
                "tool_holding": False,
                "tool_witness_enabled": True,
            },
        )
    )
    runtime.observe(Event("reset", {"task_number": 11}))
    snapshot = runtime.snapshot()

    assert snapshot["time_scale"] == 5
    assert snapshot["active_runs"] == []
    assert snapshot["recent_runs"][0]["state"] == "SUCCEEDED"
    assert snapshot["world"]["flags"]["opc:传感器状态_上位机[3].NO[1]"] is True
    assert snapshot["world"]["devices"]["szlab_mixer_robot"]["tool_holding"] is False


def test_optional_szlab_catalog_has_not_drifted() -> None:
    root_value = os.environ.get("SZLAB_REFERENCE_ROOT")
    if not root_value:
        pytest.skip("set SZLAB_REFERENCE_ROOT to enable cross-repository drift check")
    reference = Path(root_value)
    python = reference / ".venv" / "bin" / "python"
    if not python.is_file():
        pytest.skip(f"SZLab reference Python 3.11 environment not found: {python}")
    root = Path(__file__).parents[1]
    result = subprocess.run(
        [
            str(python),
            str(root / "tools" / "snapshot_szlab_profile.py"),
            str(reference),
            "--behavior",
            str(root / "config" / "szlab_behavior.yaml"),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
