"""持久扫描前缀可重算；只验证协调轨迹，不授予物理或物料资格。"""
import json
import pytest
from plc_sim.cosimulation import Channel, CoupledSession, CouplingError, PhysicsFrame, Sample

class Backend:
    def __init__(self): self.q = 0; self.fail = False
    def reset(self, token, channels):
        self.q = 0
        return PhysicsFrame(token, 0, {'q': Sample(0, 0, self.q)})
    def step(self, token, commands, channels):
        if self.fail: raise TimeoutError('未知确认')
        self.q += commands['motor']
        return PhysicsFrame(token, token.dt_ns, {name: Sample(token.tick, token.tick, self.q) for name in channels})

def test_persisted_trace_recomputes_outputs_confirmation_capture_and_delivery(tmp_path):
    path = tmp_path / 'scan.jsonl'
    def sink(record):
        with path.open('a', encoding='utf-8') as out:
            out.write(json.dumps(record) + '\n')
    backend = Backend()
    session = CoupledSession(backend, dt_ns=10, channels={'q': Channel(latency_ticks=1)},
                             writers={'motor':'plc'}, trace_sink=sink)
    seen = []
    session.register_controller('plc', 2, lambda scan: {'motor':scan.token.tick + 1}, reset=lambda token:None)
    session.register_controller('observer', 1, lambda scan:seen.append(scan) or {}, reset=lambda token:None)
    session.reset()
    for _ in range(4): session.step(single=True)
    records = [json.loads(line) for line in path.read_text().splitlines()]
    assert [r['sequence'] for r in records] == list(range(1,len(records)+1))
    q = 0; held = 0
    for tick in range(1,5):
        sealed, confirmed, committed = records[2+(tick-1)*3:2+tick*3]
        assert [r['phase'] for r in (sealed,confirmed,committed)] == ['scan_sealed','step_confirmed','step_committed']
        assert sealed['token']['tick'] == tick-1 and committed['token']['tick'] == tick
        if (tick-1)%2 == 0: held = tick
        assert sealed['held_commands'] == {'motor':held}
        assert [r['writer'] for r in sealed['controller_outputs']] == (['plc','observer'] if (tick-1)%2==0 else ['observer'])
        q += held
        sample = confirmed['frame']['samples']['q']
        assert sample['value'] == q and sample['acquired_tick'] == tick
        assert confirmed['frame']['elapsed_ns'] == 10
        assert committed['delivered']['q']['token']['tick'] == tick-1
        assert committed['delivered']['q']['delivered_tick'] == tick
        assert committed['queued'][0]['due'] == tick+1
    assert q == backend.q
    assert all(s.token.tick == i for i,s in enumerate(seen))

def test_unknown_backend_has_requested_trace_but_no_fake_confirmation():
    records=[]; backend=Backend()
    session=CoupledSession(backend,dt_ns=10,channels={'q':Channel()},writers={},trace_sink=records.append)
    session.reset();backend.fail=True
    with pytest.raises(TimeoutError):session.step(single=True)
    assert records[-1]['phase']=='scan_sealed'
    assert session.pending.tick==1 and session.tick==0
    assert not any(r['phase']=='step_confirmed' for r in records)

def test_trace_write_failure_after_actual_ack_blocks_following_steps():
    def sink(record):
        if record['phase']=='step_confirmed':raise OSError('证据存储故障')
    backend=Backend()
    session=CoupledSession(backend,dt_ns=10,channels={'q':Channel()},writers={'motor':'plc'},trace_sink=sink)
    session.register_controller('plc',1,lambda scan:{'motor':1},reset=lambda token:None)
    session.reset()
    with pytest.raises(OSError):session.step(single=True)
    assert backend.q==1 and session.tick==0 and session.pending.tick==1
    with pytest.raises(CouplingError):session.step(single=True)
