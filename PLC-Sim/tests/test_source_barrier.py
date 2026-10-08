"""原解析工具实际积分；缺来源确认不得让协调时刻前进。"""
import math
import pytest
from unilabos_sim_contracts import ContractFrame, Quantity, Sample, PhysicsFrame
from plc_sim.cosimulation import Channel, CoupledSession, CouplingError
from plc_sim.process_dynamics import exponential_increment
from plc_sim.source_barrier import SourceResult, SourceBinding, SourceBarrierPort, BarrierFrame

DIGEST='a'*64
class Analytic:
    def __init__(self, source='heat'): self.value=0.; self.calls=0; self.source=source
    def result(self, token, channels):
        evidence=ContractFrame(schema_version='unilabos.sim-frame/1.0',model_version='first-order/1',asset_digest=None,
            session_id=token.session_id,epoch=token.epoch,tick=token.tick,sim_time_ns=token.time_ns,
            request_id='request',source_id=self.source,sequence=token.tick,contract_digest=DIGEST,
            payload={'temperature':Quantity(value=self.value,unit='K')},quality='valid',captured_at=token.time_ns,available_at=token.time_ns)
        return SourceResult(evidence,{name:Sample(token.tick,token.tick,self.value) for name in channels})
    def reset(self,token,channels):self.value=0.;return self.result(token,channels)
    def step(self,token,commands,channels):
        self.calls+=1
        self.value+=exponential_increment(10-self.value,token.dt_ns/1e9,1)
        return self.result(token,channels)

def binding(provider, source='heat', channels=('temperature',), authorities=('energy',)):
    return SourceBinding(source,'analytic',provider,channels,'request',DIGEST,authorities,units={name:'K' for name in channels})

def test_no_physics_direct_uses_actual_analytic_increment_and_contract_ack():
    model=Analytic();records=[]
    session=CoupledSession(SourceBarrierPort((binding(model),)),dt_ns=100_000_000,
        channels={'temperature':Channel()},writers={},trace_sink=records.append)
    frame=session.reset()
    assert isinstance(frame,BarrierFrame) and not isinstance(frame,PhysicsFrame)
    for _ in range(4):session.step(single=True)
    assert session.now()==.4 and model.value==pytest.approx(10*(1-math.exp(-.4)))
    assert session.observation('temperature').value==model.value
    source_evidence=[r['frame']['source_evidence'] for r in records if r['phase']=='step_confirmed']
    assert len(source_evidence)==4 and all(row[0]['kind']=='analytic' for row in source_evidence)

def test_missing_required_mixed_confirmation_retains_pending_without_replay():
    first=Analytic();second=Analytic('other')
    port=SourceBarrierPort((binding(first),binding(second,'other',(),('other-energy',))))
    session=CoupledSession(port,dt_ns=100_000_000,channels={'temperature':Channel()},writers={})
    session.reset()
    second.step=lambda *args:None
    with pytest.raises(CouplingError):session.step(single=True)
    assert session.tick==0 and session.pending.tick==1 and first.calls==1
    with pytest.raises(CouplingError):session.step(single=True)
    assert first.calls==1

def test_duplicate_coupled_quantity_authorities_fail_before_any_source_executes():
    first=Analytic();second=Analytic('other')
    with pytest.raises(ValueError,match='唯一更新权威'):
        SourceBarrierPort((binding(first),binding(second,'other',())))
    assert first.calls==second.calls==0

def test_nonphysical_source_cannot_substitute_physics_ack():
    model=Analytic()
    port=SourceBarrierPort((binding(model),))
    session=CoupledSession(port,dt_ns=100,channels={'temperature':Channel()},writers={})
    session.reset()
    model.step=lambda token,commands,channels:SourceResult(PhysicsFrame(token,token.dt_ns,{'temperature':Sample(token.tick,token.tick,1)}),{'temperature':Sample(token.tick,token.tick,1)},units={'temperature':'K'})
    with pytest.raises(CouplingError,match='非物理解析来源'):
        session.step(single=True)
    assert session.tick==0


def test_same_contract_cannot_change_declared_engineering_unit():
    from dataclasses import replace
    model=Analytic();session=CoupledSession(SourceBarrierPort((binding(model),)),dt_ns=10,
        channels={'temperature':Channel()},writers={})
    session.reset();original=model.step
    def wrong(token,commands,channels):
        result=original(token,commands,channels)
        evidence=result.evidence.model_copy(update={'payload':{'temperature':Quantity(value=model.value,unit='mL')}})
        return replace(result,evidence=evidence)
    model.step=wrong
    with pytest.raises(CouplingError,match='工程单位'):
        session.step(single=True)
    assert session.tick==0 and session.pending.tick==1


@pytest.mark.parametrize('unit,valid',[('1',True),('mL',False)])
def test_analytic_boolean_retains_signal_type_with_explicit_dimensionless_unit(unit,valid):
    from dataclasses import replace
    class Boolean(Analytic):
        def result(self,token,channels):
            numeric=super().result(token,channels)
            evidence=numeric.evidence.model_copy(update={'payload':{'temperature':True}})
            return SourceResult(evidence,{name:Sample(token.tick,token.tick,True) for name in channels},units={'temperature':unit})
    model=Boolean()
    selected=SourceBinding('heat','analytic',model,('temperature',),'request',DIGEST,units={'temperature':'1'})
    session=CoupledSession(SourceBarrierPort((selected,)),dt_ns=10,channels={'temperature':Channel()},writers={})
    if not valid:
        with pytest.raises(CouplingError,match='工程单位'):session.reset()
        return
    session.reset();session.step(single=True)
    assert session.observation('temperature').value is True
