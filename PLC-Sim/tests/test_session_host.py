"""实际墙钟只控制调度节拍；所选原解析积分的逻辑轨迹保持一致。"""
import time
import pytest
from plc_sim.cosimulation import Channel, CoupledSession, CouplingError
from plc_sim.source_barrier import SourceBarrierPort
from plc_sim.session_host import SessionHost
from test_source_barrier import Analytic, binding


@pytest.mark.parametrize('rate', [.5, 1., 4.])
def test_real_paced_host_pause_resume_no_catchup_and_single_step(rate):
    model=Analytic()
    session=CoupledSession(SourceBarrierPort((binding(model),)), dt_ns=20_000_000,
                           channels={'temperature':Channel()},writers={})
    session.reset()
    frames=[];stopped=[]
    host=SessionHost(session,collect=frames.append,allowed_rates=(.5,1.,4.),on_stop=lambda:stopped.append(1))
    host.set_rate(rate);host.resume()
    start=time.monotonic()
    while session.tick<2:
        host.poll();time.sleep(.0005)
    elapsed=time.monotonic()-start
    assert elapsed>=2*session.wall_delay()
    host.pause();snapshot=(session.token,model.value,model.calls,len(frames))
    time.sleep(.05)
    assert host.poll() is None
    assert snapshot==(session.token,model.value,model.calls,len(frames))
    host.resume()
    assert host.poll() is None  # 暂停经过时间不能立刻补算。
    time.sleep(session.wall_delay()*3)
    host.poll()
    assert session.tick==3 and host.poll() is None  # 迟到一次只推进一次。
    host.pause();host.step()
    assert session.tick==4 and session.state=='paused'
    import math
    assert model.value==pytest.approx(10*(1-math.exp(-.08)))
    assert len(frames)==4
    host.request_stop();assert host.poll() is None
    assert session.state=='stopped' and stopped==[1]
    assert host.health(max_pending_s=1,link_ok=False).healthy is False
    with pytest.raises(CouplingError):host.resume()


def test_runtime_rate_admission_rejects_direct_session_bypass():
    model=Analytic()
    session=CoupledSession(SourceBarrierPort((binding(model),)),dt_ns=10,
                           channels={'temperature':Channel()},writers={})
    session.reset()
    host=SessionHost(session,collect=lambda frame:None,allowed_rates=(1.,),on_stop=lambda:None)
    with pytest.raises(ValueError):host.set_rate(4)
    session.set_rate(4)
    with pytest.raises(CouplingError):host.step()
    assert session.tick==0 and model.calls==0


def test_stop_arriving_during_confirmed_step_prevents_followup_action_dispatch():
    model=Analytic()
    session=CoupledSession(SourceBarrierPort((binding(model),)),dt_ns=10,
                           channels={'temperature':Channel()},writers={})
    session.reset()
    collected=[];stopped=[]
    host=SessionHost(session,collect=collected.append,allowed_rates=(1.,),on_stop=lambda:stopped.append(1))
    original=model.step
    def requested(token,commands,channels):
        host.request_stop()
        return original(token,commands,channels)
    model.step=requested
    host.step()
    assert session.tick==1 and session.state=='stopped'
    assert collected==[] and stopped==[1]
