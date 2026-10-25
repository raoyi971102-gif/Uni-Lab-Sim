"""物理后端来源必须明确，不能伪装成 Isaac 或绕过唯一采集来源。"""
import pytest
from plc_sim.common import NodeDef
from plc_sim.signal_runtime import compile_signals, SignalStore


def test_blender_sensor_origin_is_preserved_and_wrong_source_rejected():
    node=NodeDef('contact','contact','VARIABLE','FLOAT','ns=2;s=contact')
    semantics={'contact':dict(role='sensor_input',writer='physics',unit='N',scale=1,offset=0,
        minimum=0,maximum=None,period_ns=50_000_000,ttl_ns=150_000_000,
        source='blender_bullet',source_ref='world/contact',model_port='contact')}
    signals=compile_signals([node],semantics)
    assert signals['contact'].source=='blender_bullet'
    store=SignalStore(signals,session_id='local-bullet',epoch=0)
    writer=store.grant('physics')
    with pytest.raises(ValueError,match='采样来源不符'):
        store.publish(writer,'contact',1.962,acquired_ns=0,sequence=0,source_ref='other/contact')
    store.publish(writer,'contact',1.962,acquired_ns=0,sequence=0,source_ref='world/contact')
    sample=store.snapshot(0)['signals']['contact']
    assert sample['source']=='blender_bullet' and sample['quality']=='good'
    assert sample['value']==1.962
    semantics['contact']['source']='unknown-engine'
    with pytest.raises(ValueError,match='传感器必须显式选择来源'):
        compile_signals([node],semantics)
