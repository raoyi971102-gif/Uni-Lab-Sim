from plc_sim import cosimulation
import unilabos_sim_contracts as contracts

def test_single_authority():
    for name in ("StepToken", "Sample", "PhysicsFrame", "PhysicsPort"):
        assert getattr(cosimulation, name) is getattr(contracts, name)
