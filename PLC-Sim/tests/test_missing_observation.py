"""缺测确认保留无效采样身份；不得伪造数值或恢复 Good。"""
from dataclasses import replace
import socket
import sys
from pathlib import Path

import pytest
from opcua import Client, ua
from plc_sim.source_barrier import SourceResult, SourceBinding, SourceBarrierPort
from plc_sim.signal_assembly import SignalAssembly
from plc_sim.model_loading import ModelSource, load_symbol
from unilabos_sim_contracts import ContractFrame, Sample, StepToken, CouplingError
from test_signal_runtime import store
import test_signal_assembly as fixture


class Source:
    def __init__(self, fault=None):
        self.fault = fault

    def reset(self, token, selected):
        payload = {} if self.fault == 'payload_absent' else {'sensor': None}
        evidence = ContractFrame(schema_version='unilabos.sim-frame/1.0', model_version='missing/1',
            asset_digest=None, session_id=token.session_id, epoch=token.epoch, tick=token.tick,
            sim_time_ns=token.time_ns, request_id='r', source_id='s', sequence=token.tick,
            contract_digest='a'*64, payload=payload, quality='invalid' if self.fault=='envelope' else 'valid',
            captured_at=token.time_ns, available_at=token.time_ns)
        sample = Sample(token.tick, token.tick, None, True if self.fault=='valid' else False)
        samples = {} if self.fault=='omitted' else {name:sample for name in selected}
        return SourceResult(evidence, samples, units={'sensor':'K' if self.fault=='unit' else 'g'})


def test_explicit_missing_has_unit_and_invalid_sample():
    token = StepToken('w','s',0,0,10)
    port = SourceBarrierPort((SourceBinding('s','analytic',Source(),('sensor',),'r','a'*64,units={'sensor':'g'}),))
    frame = port.reset(token, ('sensor',))
    assert frame.samples['sensor'] == Sample(0,0,None,False)
    assert frame.source_evidence[0]['units'] == {'sensor':'g'}


@pytest.mark.parametrize('fault',['valid','envelope','omitted','payload_absent','unit'])
def test_missing_does_not_weaken_required_confirmation(fault):
    port = SourceBarrierPort((SourceBinding('s','analytic',Source(fault),('sensor',),'r','a'*64,units={'sensor':'g'}),))
    with pytest.raises(CouplingError):port.reset(StepToken('w','s',0,0,10),('sensor',))


def test_sensor_missing_identity_repeat_order_and_recovery():
    s=store(); token=s.grant('source')
    def publish(raw, tick, sequence, quality='unknown'):
        s.publish(token,'sensor',raw,acquired_ns=tick,sequence=sequence,source_ref='model@1',quality=quality)
    publish(None,0,0)
    assert s.snapshot(0)['signals']['sensor']['raw'] is None
    assert s.snapshot(0)['signals']['sensor']['value'] is None
    assert s.snapshot(0)['signals']['sensor']['quality']=='unknown'
    publish(7.,1,1,'good'); publish(None,2,2)
    saved=s.snapshot(2);audit=len(s.audit)
    publish(None,2,2)
    assert s.snapshot(2)==saved and len(s.audit)==audit
    for tick,sequence in ((3,2),(1,3),(2,1)):
        with pytest.raises(ValueError):publish(None,tick,sequence)
    assert s.snapshot(2)==saved
    publish(8.,3,3,'good')
    assert s.snapshot(3)['signals']['sensor']['value']==8.
    assert s.snapshot(3)['signals']['sensor']['quality']=='good'


@pytest.mark.parametrize('name,writer,quality',[('amount','host','unknown'),('done','controller','unknown'),
    ('sensor','source','good'),('sensor','source','fault')])
def test_none_remains_forbidden_for_commands_status_and_valid_values(name,writer,quality):
    s=store()
    with pytest.raises((ValueError,TypeError)):
        s.publish(s.grant(writer),name,None,acquired_ns=0,sequence=0,
                  source_ref='model@1' if name=='sensor' else None,quality=quality)
    assert s.snapshot(0)['revision']==0


def test_actual_assembly_and_opc_missing_transition(tmp_path,monkeypatch):
    text=fixture.FACTORY.replace('class Port:', 'class Port:\n    value=None')
    text=text.replace("Quantity(value=0.,unit='mL')", "None if self.value is None else Quantity(value=self.value,unit='mL')")
    text=text.replace('Sample(token.tick,token.tick,0.)', 'Sample(token.tick,token.tick,self.value,self.value is not None)')
    text=text.replace('for n in channels})', "for n in channels},units={'sensor':'mL'})")
    text=text.replace('    session.reset()', '    session.initial_frame=session.reset()')
    monkeypatch.setattr(fixture,'FACTORY',text)
    root,_,lock=fixture.prepare(tmp_path)
    sys.path.insert(0,str(root))
    client=None;assembly=None
    try:
        source=ModelSource(lock,root,lock.commit,None)
        original=load_symbol('fixture_signals:build',source=source)(source)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0));address='opc.tcp://127.0.0.1:'+str(sock.getsockname()[1])
        assembly=SignalAssembly(original.session,csv_path=root/'signals.csv',semantics={
            'sensor':dict(role='sensor_input',writer='model',unit='mL',scale=1,offset=0,minimum=0,maximum=100,
                period_ns=10,ttl_ns=20,source='analytic',source_ref='model@1',model_port='sensor')},
            source=source,imported_file=root/'fixture_signals.py',readers=original.readers,endpoint=address)
        assembly.start();assembly.collect(assembly.session.initial_frame)
        client=Client(address);client.connect();node=client.get_node('ns=2;s=sensor')
        def read():
            request=ua.ReadParameters(); item=ua.ReadValueId()
            item.NodeId=node.nodeid;item.AttributeId=ua.AttributeIds.Value
            request.NodesToRead=[item]
            return client.uaclient.read(request)[0]
        assert read().StatusCode.value==ua.StatusCodes.BadNoData
        assert assembly.store.snapshot(0)['signals']['sensor']['raw'] is None
        provider=assembly.session.backend.bindings[0].provider
        for value,quality,code in ((7.,'good',ua.StatusCodes.Good),(None,'unknown',ua.StatusCodes.BadNoData),(8.,'good',ua.StatusCodes.Good)):
            provider.value=value
            frame=assembly.session.step(single=True);assembly.collect(frame)
            row=assembly.store.snapshot(frame.token.time_ns)['signals']['sensor']
            assert row['quality']==quality and row['raw']==value and row['value']==value
            actual=read();assert actual.StatusCode.value==code
            if value is None:assert actual.Value.Value==7.  # 协议旧数值仅作占位且状态为 BadNoData。
            else:assert actual.Value.Value==value
            revision=assembly.store.snapshot(frame.token.time_ns)['revision']
            assembly.collect(frame)
            assert assembly.store.snapshot(frame.token.time_ns)['revision']==revision
    finally:
        if client is not None:client.disconnect()
        if assembly is not None:assembly.close()
        sys.path.remove(str(root));sys.modules.pop('fixture_signals',None)
