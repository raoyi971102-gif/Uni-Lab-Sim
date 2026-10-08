from dataclasses import asdict
import os
from pathlib import Path
import socket
import pytest
from opcua import Client, ua
from plc_sim.reference_signals import build_reference
import plc_sim.reference_signals as reference
from plc_sim.signal_assembly import SignalAssembly
from plc_sim.model_loading import ModelSource
from unilabos_sim_contracts import SourceLock


def source():
    root = Path(reference.__file__).resolve().parent
    lock = SourceLock.model_validate_json(Path(os.environ['PLC_REFERENCE_SOURCE_LOCK']).read_text())
    return ModelSource(lock, root, lock.commit, lock.dirty_digest)


def endpoint():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return f'opc.tcp://127.0.0.1:{sock.getsockname()[1]}'


def raw_read(client, node):
    value = ua.ReadValueId(); value.NodeId = node.nodeid; value.AttributeId = ua.AttributeIds.Value
    params = ua.ReadParameters(); params.NodesToRead = [value]
    return client.uaclient.read(params)[0]


def test_real_protocol_zero_ttl_and_original_acquisition():
    assembly = build_reference(source=source(), mode='analytic', endpoint=endpoint(),
                               sampling_period_ticks=4, sensor_ttl_ticks=1)
    assembly.start(); assembly.collect(assembly.reference_initial)
    user, password = assembly.endpoint.credential('reference.host')
    client = Client(assembly.endpoint.endpoint); client.set_user(user); client.set_password(password); client.connect()
    try:
        sensor = client.get_node(assembly.store.signals['temperature'].node_id)
        command = client.get_node(assembly.store.signals['temperature_target'].node_id)
        assert raw_read(client, sensor).Value.Value == 0. and raw_read(client, sensor).StatusCode.is_good()
        command.set_value(10., ua.VariantType.Double)
        original = assembly.store.snapshot(0)['signals']['temperature']
        assembly.collect(assembly.session.step(single=True))
        assert raw_read(client, sensor).StatusCode.is_good()
        assembly.collect(assembly.session.step(single=True))
        stale = raw_read(client, sensor)
        assert stale.StatusCode.value == ua.StatusCodes.BadOutOfService and stale.Value.Value == 0.
        assert assembly.store.snapshot(assembly.session.token.time_ns)['signals']['temperature']['acquired_ns'] == original['acquired_ns']
        assembly.collect(assembly.session.step(single=True))
        assembly.collect(assembly.session.step(single=True))
        assert raw_read(client, sensor).StatusCode.is_good() and sensor.get_value() > 0
    finally:
        client.disconnect(); assembly.close()


@pytest.mark.parametrize('mode,mailbox', [('physics',None),('mixed',None),('analytic',Path('/not-a-selected-source'))])
def test_missing_or_excess_source_rejected_before_worker_or_endpoint(mode, mailbox):
    with pytest.raises(ValueError):
        build_reference(source=source(), mode=mode, endpoint=endpoint(), mailbox_path=mailbox)


def test_missing_reader_rejected_before_endpoint_start():
    selected = source()
    original = build_reference(source=selected, mode='analytic', endpoint=endpoint())
    semantics = {}
    for name, spec in original.store.signals.items():
        values = asdict(spec)
        for key in ('name','node_id','data_type'): values.pop(key)
        semantics[name] = values
    with pytest.raises(ValueError, match='读取适配器'):
        SignalAssembly(original.session, csv_path=Path(reference.__file__).parent/'data/reference_signals.csv',
            semantics=semantics, source=selected, imported_file=reference.__file__, readers={}, endpoint=endpoint())
    assert not original.endpoint._started
    original.close()


def test_direct_same_source_factory_without_protocol_server():
    assembly = build_reference(source=source(), mode='analytic', endpoint=None)
    assert assembly.endpoint is None
    assembly.start(); assembly.collect(assembly.reference_initial)
    token = assembly.store.grant('reference.host')
    assembly.store.publish(token, 'temperature_target', 10., acquired_ns=0, sequence=1)
    assembly.collect(assembly.session.step(single=True))
    result = assembly.store.snapshot(assembly.session.token.time_ns)['signals']['temperature']
    assert result['quality'] == 'good' and result['source_ref'] == 'analytic.reference.first-order'
    assert result['value'] > 0
    assembly.close()
