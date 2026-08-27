from __future__ import annotations

from typing import Any

from ptlc_behavior import load_behavior_contracts
from ptlc_handshake_agent import OUTPUT_DEFAULTS, PtlcHandshakeSimulator
from ptlc_plant import PtlcPlant
from ptlc_sensors import PtlcSensorEngine


class MemoryAdapter:
    def __init__(self, values: dict[str, Any]) -> None:
        self.values = values

    def read(self, name: str) -> Any:
        return self.values[name]

    def write(self, name: str, value: Any) -> None:
        self.values[name] = value


def _world(**overrides: Any) -> dict[str, Any]:
    world: dict[str, Any] = {
        "feed_count": 12,
        "waste_count": 0,
        "capacity": 30,
        "feed_homed": True,
        "waste_homed": True,
        "cylinders": {},
        "cylinder_feedback": {},
        "sensors": {},
        "feedlift_calibration": {
            "feed": {"z_empty_mm": 500.0, "pitch_mm": 2.5},
            "waste": {"z_empty_mm": 500.0, "pitch_mm": 2.5},
        },
    }
    world.update(overrides)
    return world


def _input_values() -> dict[str, Any]:
    return {
        "IX8": 0,
        "IX9": 0,
        "IX10": 0,
        "IX11": 0,
        "IX12": 0,
        "FeedLift_1Z_ActPos": 0.0,
        "FeedLift_2Z_ActPos": 0.0,
    }


def _collect_values(code: int) -> dict[str, Any]:
    values = {
        **_input_values(),
        "PLC_Ready": True,
        "PLC_Deploy_State": 0,
        "Collect_L2_ActionCode": code,
        "Collect_L2_RequestSeq": 1,
        "Collect_L2_Start": False,
        "Collect_L2_Reset": False,
        "collect_count": 1,
        "collect_forward_instructions": "P10",
        "Collect_BottleLocate_Target": True,
    }
    values.update(
        {f"Collect_L2_{key}": value for key, value in OUTPUT_DEFAULTS.items()}
    )
    return values


def test_cylinder_command_and_feedback_use_separate_clocks() -> None:
    values = _input_values()
    world = _world()
    engine = PtlcSensorEngine(MemoryAdapter(values), world, {"cylinder_s": 0.2})

    engine.schedule_cylinder("photoscrape_rotate", True, 10.0)

    assert world["cylinders"]["photoscrape_rotate"] is True
    assert engine.cylinder_feedback("photoscrape_rotate") is False
    assert values["IX9"] & (1 << 6)
    assert not values["IX9"] & (1 << 7)
    engine.advance(10.19)
    assert engine.cylinder_feedback("photoscrape_rotate") is False
    engine.advance(10.2)
    assert engine.cylinder_feedback("photoscrape_rotate") is True
    assert not values["IX9"] & (1 << 6)
    assert values["IX9"] & (1 << 7)


def test_external_material_events_are_idempotent_and_synthesize_rack_bits() -> None:
    values = _input_values()
    world = _world()
    engine = PtlcSensorEngine(MemoryAdapter(values), world)
    event = {
        "event_id": "robot-transfer-1",
        "kind": "material_transfer",
        "source": "external",
        "target": "rack_09",
    }

    assert engine.apply_external_event(event) is True
    assert engine.site_present("rack_09") is True
    assert values["IX12"] & 0b0001
    assert (
        engine.apply_external_event(
            {
                "event_id": "operator-clear-2",
                "kind": "site_set",
                "site": "rack_09",
                "present": False,
            }
        )
        is True
    )
    assert engine.apply_external_event(event) is False
    assert engine.site_present("rack_09") is False
    assert not values["IX12"] & 0b0001
    assert engine.snapshot()["recent_events"][-1]["status"] == "duplicate"


def test_standalone_collect_gate_auto_places_and_removes_bottle() -> None:
    place_values = _collect_values(23)
    place = PtlcHandshakeSimulator(
        MemoryAdapter(place_values),
        config={
            "stations": ["Collect"],
            "plant": {
                "sensor_mode": "standalone",
                "external_transition_s": 0.1,
                "cylinder_s": 0.01,
                "sensors": {"bottle_present": False},
            },
        },
        delay_s=0.2,
    )
    place.initialize()
    place_values["Collect_L2_Start"] = True
    assert [event.phase for event in place.step(now=0.0)] == ["accepted"]
    assert [event.phase for event in place.step(now=0.2)] == ["completed"]
    assert place_values["IX8"] & (1 << 1)

    remove_values = _collect_values(22)
    remove = PtlcHandshakeSimulator(
        MemoryAdapter(remove_values),
        config={
            "stations": ["Collect"],
            "plant": {
                "sensor_mode": "standalone",
                "external_transition_s": 0.1,
                "cylinder_s": 0.01,
                "sensors": {"bottle_present": True},
            },
        },
        delay_s=0.2,
    )
    remove.initialize()
    remove_values["Collect_L2_Start"] = True
    assert [event.phase for event in remove.step(now=0.0)] == ["accepted"]
    assert remove.step(now=0.1) == []
    assert not remove_values["IX8"] & (1 << 1)
    assert [event.phase for event in remove.step(now=0.3)] == ["completed"]


def test_federated_collect_requires_an_external_material_event() -> None:
    missing_values = _collect_values(23)
    missing = PtlcHandshakeSimulator(
        MemoryAdapter(missing_values),
        config={
            "stations": ["Collect"],
            "plant": {
                "sensor_mode": "federated",
                "sensors": {"bottle_present": False},
            },
        },
        delay_s=0.2,
    )
    missing.initialize()
    missing_values["Collect_L2_Start"] = True
    missing.step(now=0.0)
    assert [event.phase for event in missing.step(now=0.2)] == ["error"]

    values = _collect_values(23)
    simulator = PtlcHandshakeSimulator(
        MemoryAdapter(values),
        config={
            "stations": ["Collect"],
            "plant": {
                "sensor_mode": "federated",
                "sensors": {"bottle_present": False},
            },
        },
        delay_s=0.2,
    )
    simulator.initialize()
    values["Collect_L2_Start"] = True
    simulator.step(now=0.0)
    simulator.plant.apply_world_patch(
        {
            "events": [
                {
                    "event_id": "robot-place-1",
                    "kind": "material_transfer",
                    "source": "external",
                    "target": "collect_bottle",
                }
            ]
        }
    )

    assert [event.phase for event in simulator.step(now=0.2)] == ["completed"]


def test_standalone_feedlift_updates_counts_but_federated_mode_does_not() -> None:
    values = {
        **_input_values(),
        "FeedLift_1Z_SearchLowTarget": 0.0,
        "FeedLift_1Z_SearchHighTarget": 600.0,
        "FeedLift_2Z_SearchLowTarget": 0.0,
        "FeedLift_2Z_SearchHighTarget": 600.0,
    }
    contracts = load_behavior_contracts()
    standalone = PtlcPlant(
        MemoryAdapter(dict(values)),
        contracts,
        {
            "plant": {"sensor_mode": "standalone"},
            "process": {"material": {"feed_count": 2, "waste_count": 0}},
        },
    )
    standalone.finish(standalone.begin("FeedLift", 12, 0.0, 0.0))
    standalone.finish(standalone.begin("FeedLift", 21, 1.0, 0.0))
    assert standalone.world["feed_count"] == 1
    assert standalone.world["waste_count"] == 1

    federated = PtlcPlant(
        MemoryAdapter(dict(values)),
        contracts,
        {
            "plant": {"sensor_mode": "federated"},
            "process": {"material": {"feed_count": 2, "waste_count": 0}},
        },
    )
    federated.finish(federated.begin("FeedLift", 12, 0.0, 0.0))
    federated.finish(federated.begin("FeedLift", 21, 1.0, 0.0))
    assert federated.world["feed_count"] == 2
    assert federated.world["waste_count"] == 0


def test_develop_empty_sensor_follows_rinse_and_suction_process() -> None:
    values = {
        **_input_values(),
        "Expand_Target_Tank": 1,
        "Expand_forward_instructions": "P10",
        "Expand_rinse_count": 1,
        "Expand_up_liquid_count": 1,
        "Expand_Waste_Empty_G1": True,
        "Expand_Waste_Empty_G2": True,
        "Tank_Suction_Settle_S": 0.1,
        "Tank_Suction_Empty_S": 0.2,
        "Tank_Suction_Blow_S": 0.1,
    }
    plant = PtlcPlant(
        MemoryAdapter(values),
        load_behavior_contracts(),
        {"plant": {"sensor_mode": "federated", "cylinder_s": 0.01}},
    )

    rinse = plant.begin("Develop", 21, 0.0, 0.0)
    plant.finish(rinse)
    assert values["Expand_Waste_Empty_G1"] is False
    suction = plant.begin("Develop", 26, 1.0, 0.0)
    assert suction.outcome == "done"
    plant.advance(1.29)
    assert values["Expand_Waste_Empty_G1"] is False
    plant.advance(1.3)
    assert values["Expand_Waste_Empty_G1"] is True
