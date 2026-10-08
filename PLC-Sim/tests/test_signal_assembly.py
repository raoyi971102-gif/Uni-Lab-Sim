import hashlib
import json
import subprocess
import sys
from pathlib import Path
import pytest
from plc_sim import cli
from plc_sim.model_loading import ModelSource
from unilabos_sim_contracts import SourceLock, SourceFile

FACTORY = """
from pathlib import Path
from plc_sim.cosimulation import CoupledSession,Channel,Sample
from plc_sim.signal_assembly import SignalAssembly
from plc_sim.source_barrier import SourceBinding,SourceBarrierPort,SourceResult
from unilabos_sim_contracts import ContractFrame,Quantity
class Port:
    def result(self,token,channels):
        frame=ContractFrame(schema_version='unilabos.sim-frame/1.0',model_version='fixture.v1',asset_digest=None,
            session_id=token.session_id,epoch=token.epoch,tick=token.tick,sim_time_ns=token.time_ns,
            request_id='request',source_id='model@1',sequence=token.tick,contract_digest='a'*64,
            payload={'sensor':Quantity(value=0.,unit='mL')},quality='valid',captured_at=token.time_ns,available_at=token.time_ns)
        return SourceResult(frame, {n:Sample(token.tick,token.tick,0.) for n in channels})
    def reset(self,token,channels):return self.result(token,channels)
    def step(self,token,commands,channels):return self.result(token,channels)
def build(source):
    port=SourceBarrierPort((SourceBinding('model@1','analytic',Port(),('sensor',),'request','a'*64,units={'sensor':'mL'}),))
    session=CoupledSession(port,dt_ns=10,channels={'sensor':Channel()},writers={})
    session.reset()
    semantics={'sensor':dict(role='sensor_input',writer='model',unit='mL',scale=1,offset=0,minimum=0,maximum=100,period_ns=10,ttl_ns=20,source='analytic',source_ref='model@1',model_port='sensor')}
    return SignalAssembly(session,csv_path=Path(__file__).with_name('signals.csv'),semantics=semantics,source=source,imported_file=__file__,readers={'sensor':lambda f:f.samples['sensor']})
"""


def prepare(tmp_path, datatype="DOUBLE"):
    root=tmp_path/'package';root.mkdir()
    (root/'fixture_signals.py').write_text(FACTORY)
    (root/'signals.csv').write_text(f'Name,EnglishName,NodeType,DataType,NodeLanguage,NodeId\nsensor,sensor,VARIABLE,{datatype},English,ns=2;s=sensor\n')
    subprocess.run(['git','init','-q'],cwd=root,check=True)
    subprocess.run(['git','add','.'],cwd=root,check=True)
    subprocess.run(['git','-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','-qm','fixture'],cwd=root,check=True)
    commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,text=True).strip()
    lock=SourceLock(source_id='fixture',commit=commit,dirty_digest=None,files=tuple(SourceFile(path=p.name,sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for p in [root/'fixture_signals.py',root/'signals.csv']))
    path=tmp_path/'source-lock.json';path.write_text(lock.model_dump_json())
    return root,path,lock


def test_formal_cli_uses_locked_factory_and_original_session(tmp_path):
    root,path,_=prepare(tmp_path)
    assert cli.main(['signals','--factory','fixture_signals:build','--source-root',str(root),'--source-lock',str(path),'--steps','2'])==0
    sys.modules.pop('fixture_signals',None)

@pytest.mark.parametrize('fault',['csv','commit'])
def test_source_refusal_before_factory_execution(tmp_path,fault):
    root,path,lock=prepare(tmp_path)
    if fault=='csv':(root/'signals.csv').write_text('corrupted')
    else:
        data=json.loads(path.read_text());data['commit']='0'*40;path.write_text(json.dumps(data))
    with pytest.raises((ValueError,ImportError)):
        cli.main(['signals','--factory','fixture_signals:build','--source-root',str(root),'--source-lock',str(path),'--steps','1'])
    assert 'fixture_signals' not in sys.modules


def test_actual_csv_hash_and_old_frame_rejected(tmp_path):
    root,_,lock=prepare(tmp_path)
    source=ModelSource(lock,root,lock.commit,None)
    sys.path.insert(0,str(root))
    try:
        from plc_sim.model_loading import load_symbol
        assembly=load_symbol('fixture_signals:build',source=source)(source)
        assembly.start()
        frame=assembly.session.step(single=True);assembly.collect(frame)
        assert assembly.store.snapshot(10)['signals']['sensor']['value']==0
        assembly.session.step(single=True)
        with pytest.raises(ValueError):assembly.collect(frame)
        assembly.close()
        (root/'signals.csv').write_text('tamper')
        with pytest.raises(ValueError):assembly.start()
    finally:
        sys.path.remove(str(root));sys.modules.pop('fixture_signals',None)


@pytest.mark.parametrize("kind", ["analytic_bool", "physics_scalar"])
def test_typed_barrier_units_and_source_are_required(tmp_path, monkeypatch, kind):
    from copy import deepcopy
    from dataclasses import replace
    from plc_sim.cosimulation import PhysicsFrame
    text = FACTORY
    datatype = "DOUBLE"
    if kind == "analytic_bool":
        datatype = "BOOLEAN"
        text = text.replace("Quantity(value=0.,unit='mL')", "True")
        text = text.replace("Sample(token.tick,token.tick,0.)", "Sample(token.tick,token.tick,True)")
        text = text.replace("return SourceResult(frame, {n:Sample(token.tick,token.tick,True) for n in channels})", "return SourceResult(frame, {n:Sample(token.tick,token.tick,True) for n in channels}, units={'sensor':'1'})")
        text = text.replace("unit='mL'", "unit='1'").replace("units={'sensor':'mL'}", "units={'sensor':'1'}")
    else:
        start = text.index("class Port:")
        end = text.index("def build(source):")
        text = text[:start] + """from plc_sim.cosimulation import PhysicsFrame
class Port:
    def result(self,token,channels):
        samples={n:Sample(token.tick,token.tick,0.) for n in channels}
        return SourceResult(PhysicsFrame(token,0 if token.tick==0 else token.dt_ns,samples),samples,units={'sensor':'mL'})
    def reset(self,token,channels):return self.result(token,channels)
    def step(self,token,commands,channels):return self.result(token,channels)
""" + text[end:]
        text = text.replace("'model@1','analytic',Port()", "'model@1','physics',Port()")
        text = text.replace("source='analytic'", "source='isaac'")
    monkeypatch.setattr(sys.modules[__name__], "FACTORY", text)
    root, _, lock = prepare(tmp_path, datatype)
    sys.path.insert(0, str(root))
    try:
        from plc_sim.model_loading import load_symbol
        source = ModelSource(lock, root, lock.commit, None)
        assembly = load_symbol('fixture_signals:build', source=source)(source)
        assembly.start(); frame = assembly.session.step(single=True)
        for fault in ('unit', 'kind', 'source'):
            evidence = deepcopy(frame.source_evidence)
            if fault == 'unit': evidence[0]['units']['sensor'] = 'K'
            elif fault == 'kind': evidence[0]['kind'] = 'resource_state'
            else: evidence[0]['source_id'] = 'foreign'
            with pytest.raises(ValueError): assembly.collect(replace(frame, source_evidence=evidence))
            assert assembly.store.snapshot(10)['revision'] == 0
        with pytest.raises(ValueError): assembly.collect(PhysicsFrame(frame.token, frame.elapsed_ns, frame.samples))
        assembly.collect(frame)
        assert assembly.store.snapshot(10)['signals']['sensor']['quality'] == 'good'
        assembly.close()
    finally:
        sys.path.remove(str(root)); sys.modules.pop('fixture_signals', None)
