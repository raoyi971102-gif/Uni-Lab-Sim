---
goal: Deliver a package-wide SZLab simulation runtime on the PLC-SIM PTLC branch
version: 1.0
date_created: 2026-08-20
last_updated: 2026-08-20
owner: PLC-SIM maintainers
status: 'In progress'
tags: [architecture, simulation, szlab, ptlc, refactor]
---

# Introduction

![Status: In progress](https://img.shields.io/badge/status-In_progress-yellow)

Replace workflow-selected SZLab handshake processes with one package-wide simulation session. The session keeps every SZLab protocol family active, owns one shared world state, reports deterministic action feedback, and allows every workflow composed from modeled device actions to run without a simulator change.

## 1. Requirements & Constraints

- **REQ-001**: One `szlab-handshake serve` process MUST keep Robot and S04-S09 protocol families active without requiring a workflow selection.
- **REQ-002**: Workflow selection MUST NOT control which device protocol handlers are enabled; legacy `--workflow` input MAY select an initial scenario only.
- **REQ-003**: The runtime MUST expose a JSON-serializable state snapshot containing session identity, device states, world state, action counters, recent events, and behavior coverage.
- **REQ-004**: S04 positions 1 through 6 MUST be independently accepted and timed in one session.
- **REQ-005**: Action completion timing MUST use action parameters when a physical duration is available and a configurable fallback otherwise.
- **REQ-006**: Robot resource ownership MUST serialize robot tasks while independent station protocols MAY progress concurrently.
- **REQ-007**: Unsupported protocol actions MUST fail closed or be reported as unsupported; they MUST NOT receive fabricated completion feedback.
- **REQ-008**: The GUI MUST start SZLab in package mode by default and show package runtime state through a backend API.
- **REQ-009**: Existing CLI flags and source-level `WorkflowHandshakeSimulator` imports MUST remain compatible during migration.
- **REQ-010**: Existing SZLab workflow handshake regressions MUST remain green unless a test asserts the deprecated workflow-gating behavior.
- **SEC-001**: Simulation MUST only write simulator-owned PLC output nodes during initialization, action feedback, and cleanup.
- **SEC-002**: The package runtime MUST NOT import or start Uni-Lab hardware drivers inside the handshake process.
- **CON-001**: Runtime Python support remains exactly Python 3.11.x.
- **CON-002**: `unilabos` MUST NOT become a required PLC-SIM runtime dependency.
- **CON-003**: Package and workflow catalog snapshots MUST be loadable without access to the private SZLab repository.
- **GUD-001**: Generic simulation lifecycle, clock, journal, and world state belong to PLC-SIM; SZLab protocol semantics belong to the SZLab adapter module.
- **GUD-002**: Sensors MUST be projected from shared world state where the current PLC protocol exposes enough identity; scenario overrides MUST be explicit.
- **PAT-001**: Follow the PTLC SimStack pattern of an isolated clock, event stream, state snapshot, fault/scenario seam, and in-memory test adapter.
- **PAT-002**: Use a deep `PackageSimulationRuntime` module with a small public interface and transport adapters at real replacement seams.

## 2. Implementation Steps

### Implementation Phase 1

- GOAL-001: Establish the package-runtime contracts and deterministic in-memory core.

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-001 | Add `PLC-Sim/package_simulation.py` with `SimulationClock`, `SimulationEvent`, `ActionRun`, `WorldState`, `BehaviorCoverage`, and `PackageSimulationRuntime`; expose start, record, snapshot, reset, and stop operations. | Yes | 2026-08-20 |
| TASK-002 | Add unit tests in `PLC-Sim/tests/test_package_simulation.py` for clock scaling, event ordering, bounded history, world-state invariants, and JSON snapshots. | Yes | 2026-08-20 |
| TASK-003 | Add `PLC-Sim/config/szlab_package.yaml` with package session defaults, initial world state, witness policy, timing parameters, and failure behavior. | Yes | 2026-08-20 |

### Implementation Phase 2

- GOAL-002: Adapt all existing SZLab PLC protocol families to one package session.

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-004 | Add `PLC-Sim/szlab_package_runtime.py` as the SZLab adapter owning Robot, S04-S09 behavior coverage, shared state projection, and action-event translation. | Yes | 2026-08-20 |
| TASK-005 | Refactor `PLC-Sim/szlab_handshake_agent.py` so serve mode always enables every component, supports all six S04 cycles, and treats `--workflow` as a deprecated scenario selector rather than a handler gate. | Yes | 2026-08-20 |
| TASK-006 | Add a `--state-file` option and atomically publish the runtime snapshot after initialization and every state transition. | Yes | 2026-08-20 |
| TASK-007 | Extend `PLC-Sim/tests/test_szlab_handshake_agent.py` and OPC UA integration tests with mixed-workflow action sequences in one simulator instance. | Yes | 2026-08-20 |

### Implementation Phase 3

- GOAL-003: Make behavior coverage catalog-driven and auditable.

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-008 | Add `PLC-Sim/config/szlab_behavior.yaml` with device protocol families, modeled primitives, aliases, query actions, external adapters, and unsupported actions. | Yes | 2026-08-20 |
| TASK-009 | Add `PLC-Sim/tools/snapshot_szlab_profile.py` to generate the behavior/catalog snapshot from an optional Uni-Lab-SZLab checkout without making it a runtime dependency. | Yes | 2026-08-20 |
| TASK-010 | Add an optional contract test comparing the packaged snapshot with a checkout selected by `SZLAB_REFERENCE_ROOT`. | Yes | 2026-08-20 |

### Implementation Phase 4

- GOAL-004: Expose the package runtime through the current GUI lifecycle.

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-011 | Extend `PLC-Sim/gui/backend_state.py` and `PLC-Sim/gui/agent_routes.py` with a generic agent state file and `GET /api/agent/szlab/state`. | Yes | 2026-08-20 |
| TASK-012 | Update `PLC-Sim/gui/static/index.html` and `PLC-Sim/gui/static/simulation.js` so SZLab starts in package mode and workflow selection is labeled as an optional legacy initial scenario. | Yes | 2026-08-20 |
| TASK-013 | Update `PLC-Sim/tests/test_gui_agent_config.py` for package-mode command construction and state reporting. | Yes | 2026-08-20 |

### Implementation Phase 5

- GOAL-005: Complete external-service and workflow-runner adapters.

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-014 | Add an isolated S1 HTTP stand-in adapter covering authentication, material, order, scheduler, status, wash, and fill endpoints. | Yes | 2026-08-20 |
| TASK-015 | Define an optional Uni-Lab sandbox-runner process adapter that executes package Action and Workflow definitions against the same simulated OPC UA and HTTP endpoints. |  |  |
| TASK-016 | Add end-to-end tests proving a workflow created only from existing modeled actions requires no PLC-SIM code change. |  |  |

### Implementation Phase 6

- GOAL-006: Remove workflow-specific runtime ownership after compatibility evidence is complete.

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-017 | Replace `WORKFLOW_COMPONENTS` runtime use with catalog metadata and retain workflow IDs only for list/check compatibility. |  |  |
| TASK-018 | Convert SZLab embedded simulation devices into thin package-runtime adapters or remove duplicated implementations after Uni-Lab-SZLab migration. |  |  |
| TASK-019 | Update README and release documentation, run the full test suite, and remove deprecated flags in the next major release only. |  |  |

## 3. Alternatives

- **ALT-001**: Start one existing handshake subprocess per workflow. Rejected because subprocesses would race on the same PLC output nodes and preserve duplicated workflow knowledge.
- **ALT-002**: Add more `if workflow == ...` branches to `WorkflowHandshakeSimulator`. Rejected because the current source already drifts from the package catalog and cannot provide package-wide state.
- **ALT-003**: Reimplement the Uni-Lab Workflow DAG engine inside PLC-SIM. Rejected because workflow scheduling remains owned by Uni-Lab Core and a second implementation would diverge.

## 4. Dependencies

- **DEP-001**: The existing `VariableAdapter` interface and OPC UA server remain the production protocol seam.
- **DEP-002**: The packaged `data/szlab_plc_0810.csv` remains the node model until catalog-owned node metadata is available.
- **DEP-003**: Uni-Lab-SZLab is an optional build-time reference for snapshot generation and contract verification.
- **DEP-004**: FastAPI GUI routes read state snapshots written by the isolated agent subprocess.

## 5. Files

- **FILE-001**: `PLC-Sim/package_simulation.py` contains generic package simulation contracts and state.
- **FILE-002**: `PLC-Sim/szlab_package_runtime.py` contains SZLab protocol-to-runtime adaptation.
- **FILE-003**: `PLC-Sim/szlab_handshake_agent.py` remains the compatible CLI and OPC UA process boundary.
- **FILE-004**: `PLC-Sim/config/szlab_package.yaml` defines package-session defaults.
- **FILE-005**: `PLC-Sim/config/szlab_behavior.yaml` is the packaged action coverage snapshot.
- **FILE-006**: `PLC-Sim/gui/agent_routes.py` owns package-agent lifecycle and state API.
- **FILE-007**: `PLC-Sim/gui/static/simulation.js` owns package-mode GUI requests.
- **FILE-008**: `PLC-Sim/tests/test_package_simulation.py` validates the generic runtime.

## 6. Testing

- **TEST-001**: Run `python -m pytest -q tests/test_package_simulation.py` and require zero failures.
- **TEST-002**: Run `python -m pytest -q tests/test_szlab_handshake_agent.py tests/test_szlab_handshake_opcua_integration.py` and require zero failures.
- **TEST-003**: Run `python -m pytest -q tests/test_gui_agent_config.py` and require zero failures.
- **TEST-004**: Run `python -m pytest -q` from `PLC-Sim` and require zero failures.
- **TEST-005**: Start one package agent, execute S04 at two positions plus Robot, S06, S07, S08, and S09 cycles without restart, and verify ordered feedback.

## 7. Risks & Assumptions

- **RISK-001**: Existing workflows rely on mutually incompatible initial sensor assumptions; explicit package scenarios and world-state projections are required to prevent accidental false guards.
- **RISK-002**: OPC UA node writes do not always identify the originating high-level Action; events may report a protocol primitive while Edge retains the exact workflow-node identity.
- **RISK-003**: Full S1 behavior requires an HTTP stand-in and cannot be achieved by the OPC UA adapter alone.
- **ASSUMPTION-001**: Uni-Lab Edge remains the authoritative Workflow executor in production integration mode.
- **ASSUMPTION-002**: Robot tasks are globally serialized by the real protocol, while independent process stations can run concurrently.
- **ASSUMPTION-003**: The package catalog may grow; snapshot drift tests will detect additions before a release claims complete coverage.

## 8. Related Specifications / Further Reading

[PLC-SIM SZLab handshake implementation](../PLC-Sim/szlab_handshake_agent.py)

[PLC-SIM PTLC runtime contracts](../PLC-Sim/ptlc_runtime.py)

[SZLab package catalog tests](../../Uni-Lab-SZLab/tests/test_package_catalog.py)
