from __future__ import annotations

from typing import Any

import pytest

from ptlc_behavior import load_behavior_contracts
from ptlc_handshake_agent import OUTPUT_DEFAULTS, PtlcHandshakeSimulator
from ptlc_runtime import MODELED_ACTIONS, STATIONS


class MemoryAdapter:
    def __init__(self, values: dict[str, Any]) -> None:
        self.values = values

    def read(self, name: str) -> Any:
        return self.values[name]

    def write(self, name: str, value: Any) -> None:
        self.values[name] = value


def _values(station: str, code: int) -> dict[str, Any]:
    values: dict[str, Any] = {
        "PLC_Ready": True,
        "PLC_Deploy_State": 0,
        f"{station}_L2_ActionCode": code,
        f"{station}_L2_RequestSeq": 1,
        f"{station}_L2_Start": False,
        f"{station}_L2_Reset": False,
        "IX8": 0,
        "IX9": 0,
        "IX10": 0,
        "IX11": 0,
        "IX12": 0,
        "collect_count": 1,
        "collect_forward_instructions": "P10",
        "Collect_BottleLocate_Target": True,
        "Expand_Target_Tank": 1,
        "Expand_forward_instructions": "P10",
        "Expand_rinse_count": 1,
        "Expand_up_liquid_count": 1,
        "Expand_Waste_Empty_G1": True,
        "Expand_Waste_Empty_G2": True,
        "Tank_State": [0] * 8,
        "Tank_Drain_Enable": [False] * 8,
        "Tank_Drain_Done": [False] * 8,
        "Tank_Drain_CapHit": [False] * 8,
        "Tank_Drain_S": 0.1,
        "Tank_Drain_Cap_S": 0.2,
        "Tank_Blow_S": 0.1,
        "Tank_Dry_S": 0.1,
        "Tank_Suction_Settle_S": 0.1,
        "Tank_Suction_Empty_S": 0.1,
        "Tank_Suction_Blow_S": 0.1,
        "Tank_Suction_Cap_S": 1.0,
        "Sampling_clean_count": 1,
        "Sampling_clean_mode": 0,
        "Sampling_sample_instructions": ["P100", ""],
        "Sampling_rinse_mix_instructions": ["A0", "A10", "A20", "A30"],
        "Sampling_rinse_mix_count": 1,
        "Sampling_band_run_instruction": "A0R",
        "Sampling_band_dry_cycles": 1,
        "Sampling_band_end_position": 0,
        "Sampling_4X_Target": 10.0,
        "Sampling_3Y_Target": 20.0,
        "Sampling_5Z_Target": 5.0,
        "Sampling_4X_WashTarget": 3.0,
        "Sampling_4X_ActPos": 0.0,
        "Sampling_3Y_ActPos": 0.0,
        "Sampling_5Z_ActPos": 0.0,
        "Spot_6X_StartTarget": 10.0,
        "Spot_6X_EndTarget": 20.0,
        "Spot_7Y_Target": 30.0,
        "Spot_6X_ActPos": 0.0,
        "Spot_7Y_ActPos": 0.0,
        "FeedLift_1Z_ActPos": 0.0,
        "FeedLift_2Z_ActPos": 0.0,
        "FeedLift_1Z_SearchLowTarget": 0.0,
        "FeedLift_1Z_SearchHighTarget": 600.0,
        "FeedLift_2Z_SearchLowTarget": 0.0,
        "FeedLift_2Z_SearchHighTarget": 600.0,
        "FeedLift_DebugAxis": 1,
        "FeedLift_DebugExpectedFinal": False,
        "Photo_8Y_Target": 40.0,
        "Photo_8Y_ActPos": 0.0,
        "PhotoScrape_8Y_ActPos": 0.0,
        "PhotoScrape_9X_ActPos": 0.0,
        "PhotoScrape_10Z_ActPos": 0.0,
        "PhotoScrape_Align_TargetX": 0.0,
        "PhotoScrape_Align_TargetY": 0.0,
        "PhotoScrape_Align_TargetZ": 0.0,
        "PhotoScrape_CamLocate_Target": True,
        "PhotoScrape_CamPress_Target": True,
        "PhotoScrape_PowderCollectorLocate_Target": True,
        "Pump_Vacuum_On": False,
        "Rail_Target_Position": 1,
        "Rail_Current_Position": 0,
        "Rail_Pos_Target": [100.0, 200.0, 300.0, 400.0, 500.0, 600.0],
        "Rail_ActPos": 0.0,
        "Rail_Homed": True,
        "StagingA_LocatorA_Target": True,
        "StagingA_LocatorB_Target": True,
    }
    values.update(
        {f"{station}_L2_{key}": value for key, value in OUTPUT_DEFAULTS.items()}
    )
    if station == "Collect" and code == 23:
        values["IX8"] = 1 << 1
    if station == "FeedLift" and code == 22:
        # A22 的前置门需要废料仓已有物料。
        values["FeedLift_2Z_ActPos"] = 490.0
    return values


def test_modeled_action_matrix_exactly_matches_dispatchers() -> None:
    contracts = load_behavior_contracts()
    assert set(MODELED_ACTIONS) == set(STATIONS)
    assert sum(len(codes) for codes in MODELED_ACTIONS.values()) == 55
    for station, contract in contracts.items():
        assert MODELED_ACTIONS[station] == frozenset(contract.accepts)


@pytest.mark.parametrize(
    ("station", "code"),
    [
        (station, code)
        for station, codes in MODELED_ACTIONS.items()
        for code in sorted(codes)
    ],
)
def test_every_legal_plc_action_reaches_a_terminal_state(
    station: str, code: int
) -> None:
    values = _values(station, code)
    config: dict[str, Any] = {
        "stations": [station],
        "plant": {
            "phase_s": 0.01,
            "cylinder_s": 0.01,
            "cnc_s": 0.01,
            "jog_speed_mm_s": 1000.0,
            "feedlift_calibration": {
                "feed": {"z_empty_mm": 500.0, "pitch_mm": 2.5},
                "waste": {"z_empty_mm": 500.0, "pitch_mm": 2.5},
            },
        },
        "motion_speed": {station: 1000.0},
        "process": {"material": {"feed_count": 12, "waste_count": 1, "capacity": 30}},
    }
    simulator = PtlcHandshakeSimulator(
        MemoryAdapter(values), config=config, delay_s=0.01
    )
    simulator.initialize()
    values[f"{station}_L2_Start"] = True
    events = simulator.step(now=0.0)
    events.extend(simulator.step(now=1000.0))
    assert events[0].phase == "accepted"
    assert events[-1].phase in {"completed", "rejected", "error", "interrupted"}
    assert values[f"{station}_L2_State"] in {20, 30, 40, 50}
    assert station not in simulator.snapshot()["active_cycles"]


def test_world_patch_synthesizes_only_plc_input_facts() -> None:
    values = _values("Pump", 10)
    simulator = PtlcHandshakeSimulator(
        MemoryAdapter(values), config={"stations": ["Pump"]}, delay_s=0
    )
    simulator.initialize()
    simulator.plant.apply_world_patch(
        {
            "feed_count": 4,
            "waste_count": 2,
            "sensors": {
                "bottle_present": True,
                "rack_occupied": [True] * 12,
                "robot_pose": [1, 2, 3],
            },
            "robot_pose": [1, 2, 3],
        }
    )
    snapshot = simulator.plant.snapshot()
    assert snapshot["feed_count"] == 4
    assert snapshot["waste_count"] == 2
    assert "robot_pose" not in snapshot
    assert "robot_pose" not in snapshot["sensors"]
    assert values["IX8"] & (1 << 1)
    assert values["IX11"] == 0xFF
    assert values["IX12"] & 0x0F == 0x0F
