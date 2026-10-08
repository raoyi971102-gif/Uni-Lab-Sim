"""真实线程阻塞及墙钟等待不会使已暂停逻辑时刻前进。"""
import threading
import time
import pytest
from plc_sim.cosimulation import CoupledSession, CouplingError, PhysicsFrame

class Backend:
    def reset(self, token, channels):
        return PhysicsFrame(token, 0, {})
    def step(self, token, commands, channels):
        return PhysicsFrame(token, token.dt_ns, {})

def test_paused_disconnection_and_stop_request_leave_logical_time_unchanged():
    session = CoupledSession(Backend(), dt_ns=10_000_000, channels={}, writers={})
    session.reset()
    before = session.token
    time.sleep(.02)
    health = session.health(max_pending_s=.01, link_ok=False)
    assert not health.healthy and health.token == before and health.pending is None
    session.request_stop()
    with pytest.raises(CouplingError):
        session.step(single=True)
    assert session.token == before
    assert session.health(max_pending_s=.01, link_ok=True).stop_requested

def test_health_and_stop_request_are_reachable_during_blocked_backend():
    entered, release, ready = threading.Event(), threading.Event(), threading.Event()
    shared = {}
    class Blocked(Backend):
        def step(self, token, commands, channels):
            entered.set()
            if not release.wait(2):
                raise TimeoutError('测试未释放后端')
            return super().step(token, commands, channels)
    def owner():
        try:
            session = CoupledSession(Blocked(), dt_ns=10_000_000, channels={}, writers={})
            shared['session'] = session
            session.reset()
            ready.set()
            session.step(single=True)
        except BaseException as error:
            shared['error'] = error
    thread = threading.Thread(target=owner)
    thread.start()
    try:
        assert ready.wait(1) and entered.wait(1)
        session = shared['session']
        time.sleep(.02)
        health = session.health(max_pending_s=.01, link_ok=True)
        assert not health.healthy and health.pending.tick == 1 and health.token.tick == 0
        session.request_stop()
        assert session.health(max_pending_s=.01, link_ok=True).stop_requested
    finally:
        release.set()
        thread.join(2)
    assert not thread.is_alive() and 'error' not in shared
    assert session.tick == 1 and session.pending is None and session.state == 'stopped'
    assert session.health(max_pending_s=.01, link_ok=True).token.tick == 1


@pytest.mark.parametrize('phase', ['reset', 'step'])
def test_stop_does_not_erase_latched_backend_failure(phase):
    backend = Backend()
    session = CoupledSession(backend, dt_ns=10, channels={}, writers={})
    session.reset()
    original = getattr(backend, phase)
    def fail(*args):
        raise TimeoutError('明确的后端错误')
    setattr(backend, phase, fail)
    with pytest.raises(TimeoutError):
        session.reset() if phase == 'reset' else session.step(single=True)
    pending = session.pending
    session.stop()
    health = session.health(max_pending_s=100, link_ok=True)
    assert not health.healthy and health.execution_failed and health.pending == pending
    setattr(backend, phase, original)
    session.reset()
    assert session.health(max_pending_s=100, link_ok=True).healthy
