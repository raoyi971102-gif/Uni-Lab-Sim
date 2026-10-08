"""Locked reference source compositions; no SZLab mechanism qualification.

The joint source is a real IsaacStepPort reached through the existing mailbox.
The analytic source uses the shared first-order increment. Both are advanced
only by the caller's CoupledSession; OPC UA transports commands and observations.
"""
from pathlib import Path
from typing import Any, Mapping, Callable
from .cosimulation import CoupledSession, Channel, Sample, StepToken
from .physics_mailbox import FilePhysicsPort
from .process_dynamics import exponential_increment
from .source_barrier import SourceBinding, SourceBarrierPort, SourceResult
from .signal_assembly import SignalAssembly
from .model_loading import ModelSource
from .common import load_csv
from .signal_runtime import compile_signals
from unilabos_sim_contracts import ContractFrame, Quantity


class JointSource:
    """Adapt actual mailbox confirmations without constructing physical evidence."""
    def __init__(self, mailbox: FilePhysicsPort) -> None:
        self.mailbox = mailbox

    def reset(self, token: StepToken, channels: tuple[str, ...]) -> SourceResult:
        frame = self.mailbox.reset(token, channels)
        return SourceResult(frame, frame.samples, units={"q": "rad", "dq": "rad/s"})

    def step(self, token: StepToken, commands: Mapping[str, Any], channels: tuple[str, ...]) -> SourceResult:
        frame = self.mailbox.step(token, commands, channels)
        return SourceResult(frame, frame.samples, units={"q": "rad", "dq": "rad/s"})


class FirstOrderSource:
    """A reference first-order thermal quantity, not a device calibration."""
    SOURCE_ID = "analytic.reference.first-order"
    CONTRACT = "a" * 64

    def __init__(self) -> None:
        self.value = 0.0

    def _result(self, token: StepToken, channels: tuple[str, ...]) -> SourceResult:
        frame = ContractFrame(schema_version="unilabos.sim-frame/1.0", model_version="first-order/1",
            asset_digest=None, session_id=token.session_id, epoch=token.epoch, tick=token.tick,
            sim_time_ns=token.time_ns, request_id="reference.thermal", source_id=self.SOURCE_ID,
            sequence=token.tick, contract_digest=self.CONTRACT,
            payload={"temperature": Quantity(value=self.value, unit="K")}, quality="valid",
            captured_at=token.time_ns, available_at=token.time_ns)
        return SourceResult(frame, {name: Sample(acquired_tick=token.tick, sequence=token.tick, value=self.value)
                                   for name in channels})

    def reset(self, token: StepToken, channels: tuple[str, ...]) -> SourceResult:
        self.value = 0.0
        return self._result(token, channels)

    def step(self, token: StepToken, commands: Mapping[str, Any], channels: tuple[str, ...]) -> SourceResult:
        self.value += exponential_increment(commands["temperature_target"] - self.value, token.dt_ns / 1e9, 1.0)
        return self._result(token, channels)


def build_reference(*, source: ModelSource, mode: str, endpoint: str | None,
                    mailbox_path: Path | None = None, dt_ns: int = 10_000_000,
                    sampling_period_ticks: int = 1, sensor_ttl_ticks: int = 2,
                    trace_sink: Callable[[dict[str, Any]], None] | None = None) -> SignalAssembly:
    """Build a selected source profile; endpoint=None consumes the same contract directly."""
    if mode not in ("analytic", "physics", "mixed"):
        raise ValueError("An explicit analytic, physics or mixed profile is required")
    if (mode != "analytic") != (mailbox_path is not None):
        raise ValueError("Only physics profiles require an explicit worker mailbox")
    if endpoint is not None and not endpoint.startswith("opc.tcp://127.0.0.1:"):
        raise ValueError("The reference endpoint must be isolated on loopback")
    source.verify(__file__)
    csv_path = Path(__file__).with_name("data") / "reference_signals.csv"
    source.verify(str(csv_path))
    physical = mode in ("physics", "mixed")
    analytic = mode in ("analytic", "mixed")
    semantics, channels, bindings = {}, {}, []
    def spec(name: str, role: str, unit: str, kind: str | None, origin: str | None) -> None:
        semantics[name] = dict(role=role, writer="reference.host" if role == "host_command" else "reference.source",
            unit=unit, scale=1, offset=0, minimum=None, maximum=None, period_ns=dt_ns * sampling_period_ticks,
            ttl_ns=dt_ns * sensor_ttl_ticks if role == "sensor_input" else dt_ns * 10000,
            source=kind, source_ref=origin, model_port=name)
        if role == "sensor_input":
            channels[name] = Channel(period_ticks=sampling_period_ticks, ttl_ticks=sensor_ttl_ticks)
    if physical:
        spec("q_target", "host_command", "rad", None, None)
        spec("q", "sensor_input", "rad", "isaac", "isaac.reference.joint")
        spec("dq", "sensor_input", "rad/s", "isaac", "isaac.reference.joint")
        bindings.append(SourceBinding("isaac.reference.joint", "physics", JointSource(FilePhysicsPort(mailbox_path, timeout_s=180)),
            ("q", "dq"), "reference.joint", "b" * 64, ("joint-angle",), units={"q": "rad", "dq": "rad/s"}))
    if analytic:
        spec("temperature_target", "host_command", "K", None, None)
        spec("temperature", "sensor_input", "K", "analytic", FirstOrderSource.SOURCE_ID)
        bindings.append(SourceBinding(FirstOrderSource.SOURCE_ID, "analytic", FirstOrderSource(),
            ("temperature",), "reference.thermal", FirstOrderSource.CONTRACT, ("thermal-state",), units={"temperature": "K"}))
    compile_signals(load_csv(csv_path), semantics)
    holder: dict[str, SignalAssembly] = {}
    def scan(scan) -> dict[str, float]:
        store = holder["assembly"].store
        values = store.snapshot(scan.token.time_ns)["signals"]
        result = {}
        for name in ("q_target", "temperature_target"):
            if name in semantics:
                if values[name]["quality"] != "good":
                    raise ValueError("A required host target is unknown or expired")
                result["q_rad" if name == "q_target" else name] = values[name]["value"]
        return result
    writer_ports = {("q_rad" if name == "q_target" else name): "reference.controller"
                    for name in semantics if semantics[name]["role"] == "host_command"}
    session = CoupledSession(SourceBarrierPort(tuple(bindings)), dt_ns=dt_ns, channels=channels, writers=writer_ports, trace_sink=trace_sink)
    session.register_controller("reference.controller", 1, scan, reset=lambda token: None)
    initial = session.reset()
    def reader(name: str):
        def read(frame):
            return frame.samples[name]
        return read
    assembly = SignalAssembly(session, csv_path=csv_path, semantics=semantics, source=source,
        imported_file=__file__, readers={name: reader(name) for name in channels}, endpoint=endpoint)
    holder["assembly"] = assembly
    # Initialization is a confirmed frame; the caller starts and collects it explicitly.
    assembly.reference_initial = initial
    return assembly
