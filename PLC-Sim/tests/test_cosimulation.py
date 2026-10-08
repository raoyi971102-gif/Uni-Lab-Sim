"""用可控物理端口检查确认屏障；真实Isaac资格由GPU套件单独验证。"""
from dataclasses import replace
from copy import deepcopy
import threading
import pytest
from plc_sim.cosimulation import Channel, CoupledSession, CouplingError, PhysicsFrame, StepToken, Sample


class Backend:
    def __init__(self):self.calls=[];self.fail=None
    def reset(self, token, channels):
        self.calls.append(('reset', token))
        return PhysicsFrame(token, 0, {k:Sample(0,0,0) for k in channels})
    def step(self, token, commands, channels):
        self.calls.append(('step', token, deepcopy(commands), channels))
        if self.fail=='timeout':raise TimeoutError('确认丢失')
        frame=PhysicsFrame(token, token.dt_ns, {k:Sample(token.tick,token.tick,token.tick) for k in channels})
        if self.fail=='epoch':frame=replace(frame, token=replace(token,epoch=token.epoch-1))
        if self.fail=='tick':frame=replace(frame, token=replace(token,tick=token.tick-1))
        if self.fail=='world':frame=replace(frame, token=replace(token,world_id='other'))
        if self.fail=='session':frame=replace(frame, token=replace(token,session_id='other'))
        if self.fail=='duration':frame=replace(frame,elapsed_ns=0)
        if self.fail=='samples':frame=replace(frame,samples={'unknown':True})
        return frame


def setup(channels=None):
    backend=Backend()
    session=CoupledSession(backend,dt_ns=10_000_000,channels=channels or {'q':Channel()},writers={'motor':'plc'})
    return session,backend


def test_pause_step_rate_and_reset_share_confirmed_time():
    session,backend=setup();resets=[]
    session.register_controller('plc',1,lambda scan:{'motor':scan.token.tick},reset=resets.append)
    session.reset();assert session.state=='paused' and session.now()==0
    with pytest.raises(CouplingError):session.step()
    session.step(single=True);assert session.now()==.01
    session.set_rate(4);assert session.wall_delay()==.0025 and session.now()==.01
    session.resume();session.step();assert session.now()==.02
    with pytest.raises(CouplingError):session.reset()
    session.pause();session.reset();assert session.now()==0 and session.epoch==2
    assert [t.epoch for t in resets]==[1,2]
    assert session.observation('q').token.epoch==2


@pytest.mark.parametrize('failure',['timeout','epoch','tick','world','session','duration','samples'])
def test_unconfirmed_step_never_commits_time_or_replays(failure):
    session,backend=setup();session.reset();backend.fail=failure
    with pytest.raises((CouplingError,TimeoutError)):session.step(single=True)
    assert session.state=='faulted' and session.tick==0 and session.pending.tick==1
    assert session.observation('q') is None
    for action in [session.resume,lambda:session.step(single=True),session.step]:
        with pytest.raises(CouplingError):action()
    assert len(backend.calls)==2
    backend.fail=None;session.reset();assert session.epoch==2 and session.state=='paused'


def test_multirate_latency_and_expiration_use_acquisition_tick():
    session,backend=setup({'q':Channel(period_ticks=4,latency_ticks=1,ttl_ticks=2)})
    scans=[];session.register_controller('plc',2,lambda scan:scans.append(scan) or {},reset=lambda token:None)
    session.reset();assert session.observation('q') is None
    session.resume()
    for _ in range(4):session.step()
    assert [scan.token.tick for scan in scans]==[0,2]
    assert session.observation('q') is None
    session.step();value=session.observation('q')
    assert value.token.tick==4 and value.delivered_tick==5 and value.value==4
    assert [c[3] for c in backend.calls if c[0]=='step']==[(),(),(),('q',),()]


def test_unauthorized_output_stops_before_physics():
    session,backend=setup();session.register_controller('other',1,lambda scan:{'motor':True},reset=lambda token:None)
    session.reset()
    with pytest.raises(CouplingError,match='未授权'):session.step(single=True)
    assert len(backend.calls)==1 and session.tick==0


def test_reset_failure_cannot_reuse_old_inputs():
    session,backend=setup();session.reset()
    backend.reset=lambda token,channels:PhysicsFrame(replace(token,epoch=1),0,{'q':Sample(0,0,0)})
    with pytest.raises(CouplingError):session.reset()
    assert session.state=='faulted' and session.observation('q') is None


def test_reset_clears_delayed_old_epoch_samples():
    session,backend=setup({'q':Channel(period_ticks=2,latency_ticks=2,ttl_ticks=3)})
    session.reset();session.step(single=True);session.reset()
    session.step(single=True);session.step(single=True)
    assert session.observation('q').token.epoch==2 and session.observation('q').token.tick==0


def test_reentry_and_foreign_threads_cannot_advance_world():
    session,backend=setup();session.register_controller('plc',1,lambda scan:session.pause(),reset=lambda token:None)
    session.reset()
    with pytest.raises(CouplingError,match='重入'):session.step(single=True)
    errors=[]
    def call():
        try:session.reset()
        except CouplingError as ex:errors.append(str(ex))
    thread=threading.Thread(target=call);thread.start();thread.join()
    assert errors and len(backend.calls)==1


@pytest.mark.parametrize('value',[True,0,-1,float('nan'),float('inf'),'2'])
def test_invalid_clock_rates(value):
    session,_=setup()
    with pytest.raises(ValueError):session.set_rate(value)


@pytest.mark.parametrize('kwargs',[{'period_ticks':0},{'period_ticks':True},{'latency_ticks':-1},{'ttl_ticks':1,'latency_ticks':2}])
def test_invalid_channel_timing(kwargs):
    with pytest.raises(ValueError):Channel(**kwargs)


def test_repeated_old_capture_never_refreshes_ttl():
    session, backend = setup({'q': Channel(ttl_ticks=1)})
    session.reset()
    backend.step = lambda token, commands, channels: PhysicsFrame(token, token.dt_ns, {'q': Sample(0,0,123)})
    session.step(single=True)
    assert session.observation('q').token.tick == 0
    session.step(single=True)
    assert session.observation('q') is None


def test_relabelled_duplicate_capture_is_rejected():
    session, backend = setup();session.reset()
    backend.step = lambda token, commands, channels: PhysicsFrame(token, token.dt_ns, {'q': Sample(1,0,123)})
    with pytest.raises(CouplingError):session.step(single=True)
    assert session.tick == 0 and session.state == 'faulted'


def test_invalid_sensor_reading_stays_unknown():
    session, backend = setup();session.reset()
    backend.step = lambda token, commands, channels: PhysicsFrame(token, token.dt_ns, {'q': Sample(token.tick,token.tick,None,False)})
    session.step(single=True)
    assert session.tick == 1 and session.observation('q') is None


def test_failed_reset_never_reuses_an_issued_epoch():
    session, backend = setup();session.reset()
    original = backend.reset
    def uncertain(token, channels):
        original(token, channels)
        raise TimeoutError('已复位但确认丢失')
    backend.reset = uncertain
    with pytest.raises(TimeoutError):session.reset()
    assert session.epoch == 1 and session.pending.epoch == 2
    backend.reset = original
    session.reset()
    assert session.epoch == 3 and session.pending is None
