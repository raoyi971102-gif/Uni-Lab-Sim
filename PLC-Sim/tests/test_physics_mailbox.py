"""跨进程邮箱协议不能把缺失确认、重启或旧身份转成成功。"""
from contextlib import contextmanager
from dataclasses import asdict, replace
import json
import threading
import time
import pytest
from plc_sim.cosimulation import CoupledSession, Channel, CouplingError, PhysicsFrame, Sample, StepToken
from plc_sim.physics_mailbox import FilePhysicsPort, PhysicsMailboxWorker


class Backend:
    def __init__(self): self.calls = []; self.wrong = False
    def reset(self, token, channels):
        self.calls.append(("reset", token))
        return PhysicsFrame(token, 0, {name: Sample(0, 0, 0) for name in channels})
    def step(self, token, commands, channels):
        self.calls.append(("step", token, commands))
        frame_token = replace(token, epoch=token.epoch-1) if self.wrong else token
        return PhysicsFrame(frame_token, token.dt_ns, {name: Sample(token.tick, token.tick, commands) for name in channels})


@contextmanager
def worker_loop(path, backend):
    stopped = threading.Event()
    def serve():
        worker = PhysicsMailboxWorker(path, backend)
        while not stopped.is_set():
            if not worker.serve_once(): stopped.wait(.001)
    thread = threading.Thread(target=serve)
    thread.start()
    try: yield
    finally: stopped.set(); thread.join(2)


def test_confirmed_roundtrip_and_output_mapping(tmp_path):
    backend = Backend()
    with worker_loop(tmp_path, backend):
        session = CoupledSession(FilePhysicsPort(tmp_path, timeout_s=1), dt_ns=10_000_000,
                                 channels={"q": Channel()}, writers={"drive": "plc"})
        session.register_controller("plc", 1, lambda scan: {"drive": scan.token.tick}, reset=lambda token: None)
        session.reset()
        session.step(single=True)
        assert session.now() == .01 and session.observation("q").value == {"drive": 0}
        assert len(backend.calls) == 2


def test_timeout_faults_session_and_preserves_one_unreplayed_request(tmp_path):
    session = CoupledSession(FilePhysicsPort(tmp_path, timeout_s=.01), dt_ns=1, channels={}, writers={})
    with pytest.raises(TimeoutError): session.reset()
    assert session.state == "faulted" and session.pending.epoch == 1
    assert len(list(tmp_path.glob("*.request.json"))) == 1
    with pytest.raises(CouplingError): session.step(single=True)
    backend = Backend()
    with worker_loop(tmp_path, backend):
        session.backend.timeout_s = 1
        session.reset()
    assert session.epoch == 2 and [row[1].epoch for row in backend.calls] == [1, 2]


def test_started_request_after_crash_is_never_executed_again(tmp_path):
    token = StepToken("world", "session", 1, 1, 10)
    request = {"schema": "lab.physics-mailbox/v1", "request_id": "0001", "operation": "step",
               "token": asdict(token), "commands": {}, "channels": []}
    (tmp_path / "0001.request.json").write_text(json.dumps(request))
    (tmp_path / "0001.started").write_text("")
    backend = Backend()
    worker = PhysicsMailboxWorker(tmp_path, backend)
    assert worker.serve_once()
    assert not backend.calls
    assert "error" in json.loads((tmp_path / "0001.response.json").read_text())
    assert worker.serve_once() is False


def test_wrong_physical_epoch_does_not_commit_host_clock(tmp_path):
    backend = Backend()
    with worker_loop(tmp_path, backend):
        session = CoupledSession(FilePhysicsPort(tmp_path, timeout_s=1), dt_ns=10, channels={}, writers={})
        session.reset(); backend.wrong = True
        with pytest.raises(CouplingError): session.step(single=True)
    assert session.tick == 0 and session.state == "faulted"


@pytest.mark.parametrize("change", [{"operation": "shell"}, {"request_id": "other"}, {"channels": ["q", "q"]}, {"commands": {"drive": 1}}])
def test_invalid_requests_rejected_before_backend(tmp_path, change):
    request = {"schema": "lab.physics-mailbox/v1", "request_id": "0001", "operation": "reset",
               "token": asdict(StepToken("world", "session", 1, 0, 10)), "commands": {}, "channels": []}
    request.update(change)
    (tmp_path / "0001.request.json").write_text(json.dumps(request))
    backend = Backend()
    assert PhysicsMailboxWorker(tmp_path, backend).serve_once()
    assert not backend.calls and "error" in json.loads((tmp_path / "0001.response.json").read_text())
