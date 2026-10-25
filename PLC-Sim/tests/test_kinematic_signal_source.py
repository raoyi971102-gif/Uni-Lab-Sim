"""运动学反馈的来源必须明确保存，不能标为接触物理。"""
import pytest
from plc_sim.common import NodeDef
from plc_sim.signal_runtime import compile_signals, SignalStore


def test_kinematic_origin_and_wrong_reference():
    """语义在位保留来源名称，并拒绝错误采集引用。"""
    node=NodeDef('attached','attached','VARIABLE','BOOLEAN','ns=2;s=attached')
    semantics={'attached':dict(role='sensor_input',writer='scene',unit='1',scale=1,offset=0,
        minimum=None,maximum=None,period_ns=50_000_000,ttl_ns=150_000_000,
        source='blender_kinematic',source_ref='world/attached',model_port='attached')}
    signals=compile_signals([node],semantics)
    store=SignalStore(signals,session_id='kinematic',epoch=0)
    writer=store.grant('scene')
    with pytest.raises(ValueError,match='采样来源不符'):
        store.publish(writer,'attached',True,acquired_ns=0,sequence=0,source_ref='world/contact')
    store.publish(writer,'attached',True,acquired_ns=0,sequence=0,source_ref='world/attached')
    sample=store.snapshot(0)['signals']['attached']
    assert sample['source']=='blender_kinematic' and sample['value'] is True
