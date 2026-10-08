from dataclasses import replace
import socket
import pytest
from opcua import Client,ua
from plc_sim.common import NodeDef
from plc_sim.signal_runtime import compile_signals,SignalStore
from plc_sim.owned_opcua import OwnedOpcuaEndpoint


def setup():
    nodes=[NodeDef(name_cn=n,name_en=n,node_type='VARIABLE',data_type=t,node_id='ns=2;s='+n) for n,t in [('amount','INT16'),('start','BOOLEAN'),('done','BOOLEAN'),('sensor','DOUBLE'),('other','INT16')]]
    specs={n.name_cn:dict(role='host_command',writer='host',unit='1',scale=1,offset=0,minimum=None,maximum=None,period_ns=1,ttl_ns=10,source=None,source_ref=None,model_port=n.name_cn) for n in nodes}
    specs['done'].update(role='controller_status',writer='controller')
    specs['sensor'].update(role='sensor_input',writer='source',source='analytic',source_ref='model@1')
    specs['other'].update(writer='otherhost')
    return nodes,specs


def store():
    nodes,specs=setup();return SignalStore(compile_signals(nodes,specs),session_id='session',epoch=1)


def test_zero_ttl_duplicate_and_disordered_samples():
    s=store();token=s.grant('source')
    assert s.snapshot(0)['signals']['sensor']['quality']=='unknown'
    s.publish(token,'sensor',0.,acquired_ns=0,sequence=1,source_ref='model@1')
    assert s.snapshot(0)['signals']['sensor']['value']==0
    s.publish(token,'sensor',0.,acquired_ns=0,sequence=1,source_ref='model@1')
    assert s.snapshot(11)['signals']['sensor']['quality']=='stale'
    with pytest.raises(ValueError):s.publish(token,'sensor',0.,acquired_ns=11,sequence=1,source_ref='model@1')
    with pytest.raises(ValueError):s.publish(token,'sensor',0.,acquired_ns=0,sequence=0,source_ref='model@1')


def test_owner_epoch_isolation_and_source_ref():
    s=store();token=s.grant('host')
    with pytest.raises(PermissionError):s.publish(token,'other',1,acquired_ns=0,sequence=1)
    second=store()
    with pytest.raises(PermissionError):second.publish(token,'amount',1,acquired_ns=0,sequence=1)
    s.activate(2,reason='controlled takeover')
    with pytest.raises(PermissionError):s.publish(token,'amount',1,acquired_ns=0,sequence=1)
    assert len([r for r in s.audit if r['event']=='rejected'])==2


def test_complete_parameter_latch_and_held_start():
    s=store();t=s.grant('host')
    s.publish(t,'start',True,acquired_ns=0,sequence=1)
    with pytest.raises(ValueError):s.latch('start',('amount',),now_ns=0)
    s.publish(t,'amount',3,acquired_ns=1,sequence=1)
    with pytest.raises(ValueError):s.latch('start',('amount',),now_ns=1)
    s.publish(t,'start',False,acquired_ns=2,sequence=2)
    s.publish(t,'start',True,acquired_ns=2,sequence=3)
    assert s.latch('start',('amount',),now_ns=2)['parameters']=={'amount':3}
    with pytest.raises(ValueError):s.latch('start',('amount',),now_ns=2)


@pytest.mark.parametrize('fault',['unknown','address','type','duplicate','source'])
def test_compile_rejects_invalid_map(fault):
    nodes,specs=setup()
    if fault=='unknown':specs['absent']=specs['amount']
    if fault=='address':specs['amount']['node_id']='ns=2;s=fake'
    if fault=='type':specs['amount']['expected_type']='DOUBLE'
    if fault=='duplicate':nodes.append(nodes[0])
    if fault=='source':specs['sensor']['source']=None
    with pytest.raises(ValueError):compile_signals(nodes,specs)


def test_engineering_boundaries_and_inverse():
    nodes,specs=setup();specs['amount'].update(scale=.5,offset=2,minimum=2,maximum=12)
    sig=compile_signals(nodes,specs)['amount']
    assert sig.convert(20)==12 and sig.raw(12)==20
    for value in [-1,21,32768]:
        with pytest.raises(ValueError):sig.convert(value)
    with pytest.raises(ValueError):sig.raw(2.1)


def test_real_shared_opcua_clients_cannot_write_feedback_or_after_takeover():
    s=store();clock=[0]
    sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1];sock.close()
    endpoint=OwnedOpcuaEndpoint(s,endpoint=f'opc.tcp://127.0.0.1:{port}/',now_ns=lambda:clock[0])
    clients=[]
    try:
        for writer in ['host','otherhost']:
            username,password=endpoint.credential(writer)
            c=Client(endpoint.endpoint);c.set_user(username);c.set_password(password);clients.append(c)
        endpoint.start()
        for c in clients:c.connect()
        clients[0].get_node('ns=2;s=amount').set_value(3,ua.VariantType.Int16)
        clients[1].get_node('ns=2;s=other').set_value(4,ua.VariantType.Int16)
        assert s.snapshot(0)['signals']['amount']['value']==3
        assert clients[1].get_node('ns=2;s=amount').get_value()==3
        clock[0]=11
        with pytest.raises(ua.UaStatusCodeError):clients[1].get_node('ns=2;s=amount').get_value()
        clock[0]=0
        for name,raw,kind in [('done',True,ua.VariantType.Boolean),('other',4,ua.VariantType.Int16)]:
            with pytest.raises(ua.UaStatusCodeError):clients[0].get_node('ns=2;s='+name).set_value(raw,kind)
        s.activate(2,reason='takeover while clients remain connected')
        with pytest.raises(ua.UaStatusCodeError):clients[0].get_node('ns=2;s=amount').set_value(5,ua.VariantType.Int16)
        assert s.snapshot(0)['signals']['amount']['quality']=='unknown'
    finally:
        for c in clients:
            try:c.disconnect()
            except Exception:pass
        endpoint.close()


@pytest.mark.parametrize('source',['resource_state','analytic','isaac','telemetry','recorded'])
def test_selected_source_contract_and_missing_or_old_version(source):
    nodes,specs=setup();specs['sensor'].update(source=source,source_ref=source+'@1')
    s=SignalStore(compile_signals(nodes,specs),session_id='direct-no-server',epoch=1)
    token=s.grant('source');kw={'resource_version':3} if source=='resource_state' else {}
    s.publish(token,'sensor',0.,acquired_ns=0,sequence=1,source_ref=source+'@1',**kw)
    assert s.snapshot(0)['signals']['sensor']['source']==source
    with pytest.raises(ValueError):s.publish(token,'sensor',0.,acquired_ns=1,sequence=2,source_ref='other@1',**kw)
    if source=='resource_state':
        with pytest.raises(ValueError):s.publish(token,'sensor',0.,acquired_ns=1,sequence=2,source_ref=source+'@1')
        with pytest.raises(ValueError):s.publish(token,'sensor',0.,acquired_ns=1,sequence=2,source_ref=source+'@1',resource_version=2)


def test_held_start_does_not_create_second_request():
    s=store();t=s.grant('host')
    for n,v,seq in [('start',False,1),('amount',3,1),('start',True,2)]:s.publish(t,n,v,acquired_ns=seq,sequence=seq)
    s.latch('start',('amount',),now_ns=2)
    s.publish(t,'amount',4,acquired_ns=3,sequence=2)
    s.publish(t,'start',True,acquired_ns=3,sequence=3)
    with pytest.raises(ValueError):s.latch('start',('amount',),now_ns=3)


def test_real_controller_can_write_only_its_declared_status():
    s=store();sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1];sock.close()
    endpoint=OwnedOpcuaEndpoint(s,endpoint=f'opc.tcp://127.0.0.1:{port}/',now_ns=lambda:0)
    username,password=endpoint.credential('controller');client=Client(endpoint.endpoint);client.set_user(username);client.set_password(password)
    try:
        endpoint.start();client.connect()
        client.get_node('ns=2;s=done').set_value(True,ua.VariantType.Boolean)
        assert s.snapshot(0)['signals']['done']['value'] is True
        with pytest.raises(ua.UaStatusCodeError):client.get_node('ns=2;s=amount').set_value(1,ua.VariantType.Int16)
        assert s.snapshot(0)['signals']['amount']['quality']=='unknown'
    finally:
        try:client.disconnect()
        except Exception:pass
        endpoint.close()


def test_existing_and_new_subscriptions_receive_unknown_stale_and_epoch_invalidation():
    from threading import Condition
    import time
    class Observer:
        def __init__(self):self.values=[];self.changed=Condition()
        def datachange_notification(self,node,value,data):
            with self.changed:
                self.values.append((value,data.monitored_item.Value.StatusCode.value));self.changed.notify_all()
        def wait_for(self,code,after=0):
            deadline=time.monotonic()+3
            with self.changed:
                while not any(v[1]==code for v in self.values[after:]):
                    left=deadline-time.monotonic()
                    assert left>0,self.values
                    self.changed.wait(left)
    store_=store();clock=[0]
    sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1];sock.close()
    endpoint=OwnedOpcuaEndpoint(store_,endpoint=f'opc.tcp://127.0.0.1:{port}/',now_ns=lambda:clock[0])
    client=Client(endpoint.endpoint);subscriptions=[]
    try:
        endpoint.start();client.connect();node=client.get_node('ns=2;s=sensor')
        observer=Observer();sub=client.create_subscription(10,observer);subscriptions.append(sub);sub.subscribe_data_change(node)
        observer.wait_for(ua.StatusCodes.BadNoData)
        token=store_.grant('source')
        endpoint.publish(token,'sensor',0.,acquired_ns=0,sequence=1,source_ref='model@1')
        observer.wait_for(ua.StatusCodes.Good)
        mark=len(observer.values);clock[0]=11
        # 不做 Read，现有订阅也必须由宿主时钟到期获得失效。
        observer.wait_for(ua.StatusCodes.BadOutOfService,after=mark)
        late=Observer();sub2=client.create_subscription(10,late);subscriptions.append(sub2);sub2.subscribe_data_change(node)
        late.wait_for(ua.StatusCodes.BadOutOfService)
        endpoint.publish(token,'sensor',0.,acquired_ns=11,sequence=2,source_ref='model@1')
        observer.wait_for(ua.StatusCodes.Good,after=mark)
        mark=len(observer.values);store_.activate(2,reason='subscriber remains connected')
        observer.wait_for(ua.StatusCodes.BadNoData,after=mark)
        new=Observer();sub3=client.create_subscription(10,new);subscriptions.append(sub3);sub3.subscribe_data_change(node)
        new.wait_for(ua.StatusCodes.BadNoData)
        assert not any(code==ua.StatusCodes.Good for _,code in new.values)
    finally:
        for sub in subscriptions:
            try:sub.delete()
            except Exception:pass
        try:client.disconnect()
        except Exception:pass
        endpoint.close()


@pytest.mark.parametrize('kind,scale,offset,raw',[('DOUBLE',1e308,0,2.),('DOUBLE',1,1e308,1e308),('DOUBLE',1,0,10**400),('FLOAT',1,0,3.5e38)])
def test_conversion_overflow_rejects_without_store_change(kind,scale,offset,raw):
    nodes,semantics=setup();nodes=[replace(n,data_type=kind) if n.name_cn=='sensor' else n for n in nodes]
    semantics['sensor'].update(scale=scale,offset=offset)
    s=SignalStore(compile_signals(nodes,semantics),session_id='overflow',epoch=1)
    before=s.snapshot(0)
    with pytest.raises(ValueError):s.publish(s.grant('source'),'sensor',raw,acquired_ns=0,sequence=1,source_ref='model@1')
    assert s.snapshot(0)==before


@pytest.mark.parametrize('kind,scale,offset,value',[('DOUBLE',1e-308,0,1e308),('DOUBLE',1,-1e308,1e308),('FLOAT',1,0,3.5e38),('DOUBLE',1,0,10**400)])
def test_inverse_conversion_overflow_rejected(kind,scale,offset,value):
    nodes,specs=setup();nodes=[replace(n,data_type=kind) if n.name_cn=='sensor' else n for n in nodes]
    specs['sensor'].update(scale=scale,offset=offset)
    signal=compile_signals(nodes,specs)['sensor']
    with pytest.raises(ValueError):signal.raw(value)


def test_close_removes_listener_and_restart_restarts_quality_watcher():
    import time
    s=store();clock=[0]
    sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1];sock.close()
    endpoint=OwnedOpcuaEndpoint(s,endpoint=f'opc.tcp://127.0.0.1:{port}/',now_ns=lambda:clock[0])
    try:
        endpoint.start();endpoint.close();assert s._listeners==[]
        endpoint.start();assert endpoint._thread.is_alive() and len(s._listeners)==1
        endpoint.publish(s.grant('source'),'sensor',0.,sequence=1,acquired_ns=0,source_ref='model@1')
        clock[0]=11
        deadline=time.monotonic()+2
        while endpoint.server.iserver.aspace.get_attribute_value(endpoint.nodes['sensor'].nodeid,ua.AttributeIds.Value).StatusCode.value!=ua.StatusCodes.BadOutOfService:
            assert time.monotonic()<deadline
            time.sleep(.01)
    finally:endpoint.close()
    assert s._listeners==[]
