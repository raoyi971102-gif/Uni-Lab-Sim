"""XUSE 设备包协议事件到通用仿真运行时的 Adapter。"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

try:
    from .common import load_yaml
    from .package_simulation import PackageSimulationRuntime, SimulationClock, WorldState
except ImportError:
    from common import load_yaml
    from package_simulation import PackageSimulationRuntime, SimulationClock, WorldState

PACKAGE_ID = "community.xuse_solid_lab"


def default_package_config_path() -> Path:
    return Path(__file__).resolve().with_name("config") / "xuse_package.yaml"


class XusePackageRuntime:
    def __init__(
        self,
        *,
        config_path: str | Path | None = None,
        scenario: str | None = None,
        time_scale: float | None = None,
    ) -> None:
        path = Path(config_path or default_package_config_path())
        config = load_yaml(str(path)) if path.is_file() else {
            "schema": "unilab.package_simulation/v1",
            "package_id": PACKAGE_ID,
            "world": {},
            "history_limit": 500,
            "time_scale": 1.0,
        }
        world_config = dict(config.get("world", {}))
        world = WorldState(
            sites=dict(world_config.get("sites", {})),
            quantities=dict(world_config.get("quantities", {})),
            devices=dict(world_config.get("devices", {})),
            flags=dict(world_config.get("flags", {})),
        )
        world.set_flag("scenario", str(scenario or config.get("scenario", "ready")))
        self.runtime = PackageSimulationRuntime(
            PACKAGE_ID,
            clock=SimulationClock(float(time_scale if time_scale is not None else config.get("time_scale", 1.0))),
            world=world,
            history_limit=int(config.get("history_limit", 500)),
        )

    def initialize_protocol(self, values: Mapping[str, Any]) -> None:
        self.runtime.world.update_device("xuse_plc", state="ready", initialized_nodes=len(values))

    def observe(self, event: Any) -> None:
        action = str(event.action)
        phase = str(event.phase)
        detail = dict(event.detail)
        try:
            self.runtime.record(action, action, phase, detail)
        except Exception:
            return
        if phase == "completed":
            occupancy = dict(detail.get("occupancy") or {})
            for name, value in occupancy.items():
                self.runtime.world.set_flag(f"opc:{name}", bool(value))

    def snapshot(self, protocol_snapshot: Mapping[str, Any] | None = None) -> dict[str, Any]:
        snapshot = self.runtime.snapshot()
        snapshot["protocol"] = dict(protocol_snapshot or {})
        return snapshot

    def stop(self) -> None:
        self.runtime.stop()
