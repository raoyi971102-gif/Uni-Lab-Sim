"""停止仅封闭调度，不伪造物理停止或丢弃未知确认。"""
import pytest
from plc_sim.cosimulation import CoupledSession, CouplingError, PhysicsFrame

class ConfirmedBackend:
    def __init__(self):
        self.calls = 0
        self.fail = False
    def reset(self, token, channels):
        self.calls += 1
        return PhysicsFrame(token, 0, {})
    def step(self, token, commands, channels):
        self.calls += 1
        if self.fail:
            raise TimeoutError('确认未知')
        return PhysicsFrame(token, token.dt_ns, {})

@pytest.mark.parametrize('running', [False, True])
def test_stop_closes_scheduler_without_advancing_or_resetting(running):
    backend = ConfirmedBackend()
    session = CoupledSession(backend, dt_ns=10_000_000, channels={}, writers={})
    session.reset()
    session.step(single=True)
    if running:
        session.resume()
    before = session.token
    calls = backend.calls
    session.stop()
    session.stop()
    assert session.state == 'stopped'
    assert session.token == before and session.pending is None
    assert backend.calls == calls
    for action in (session.resume, session.step, lambda: session.step(single=True)):
        with pytest.raises(CouplingError):
            action()

def test_stop_preserves_unknown_pending_without_claiming_backend_stopped():
    backend = ConfirmedBackend()
    session = CoupledSession(backend, dt_ns=10_000_000, channels={}, writers={})
    session.reset()
    backend.fail = True
    with pytest.raises(TimeoutError):
        session.step(single=True)
    pending = session.pending
    calls = backend.calls
    session.stop()
    assert session.pending == pending and session.tick == 0
    assert backend.calls == calls and session.state == 'stopped'
    with pytest.raises(CouplingError):
        session.resume()


def test_stopped_session_does_not_expose_stale_control_input():
    from test_cosimulation import setup
    session, backend = setup()
    session.reset()
    assert session.observation('q') is not None
    session.stop()
    assert session.observation('q') is None
