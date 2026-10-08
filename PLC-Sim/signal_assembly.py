"""把锁定信号目录挂到已有 CoupledSession；不建立另一世界或时钟。"""
from __future__ import annotations
from pathlib import Path
import inspect
from typing import Callable, Mapping, Any
from .common import load_csv
from .cosimulation import CoupledSession, PhysicsFrame, Sample
from .model_loading import ModelSource
from .source_barrier import BarrierFrame, SourceBarrierPort
from .owned_opcua import OwnedOpcuaEndpoint
from .signal_runtime import SignalStore, compile_signals


class SignalAssembly:
    """协议和 direct 共享同一规范信号存储，状态转换由原包控制器负责。"""
    def __init__(self, session: CoupledSession, *, csv_path: Path, semantics: Mapping[str,Mapping[str,Any]],
                 source: ModelSource, imported_file: str,
                 readers: Mapping[str,Callable[[BarrierFrame],Sample]], endpoint: str | None=None):
        if not isinstance(session,CoupledSession) or session.state!='paused' or session.pending is not None:
            raise ValueError('信号装配需要原调度器已确认暂停边界')
        verified=source.verify(imported_file)
        source.verify(str(csv_path))
        specs=compile_signals(load_csv(csv_path),semantics)
        required={name for name,spec in specs.items() if spec.role=='sensor_input'}
        if set(readers)!=required:raise ValueError('缺少或多余的状态源读取适配器')
        if any(specs[name].model_port not in session.channels for name in required):
            raise ValueError('Signal reader port is not a declared session channel')
        if not isinstance(session.backend, SourceBarrierPort):
            raise ValueError('Signal source qualification requires an explicit source barrier')
        for name in required:
            spec=specs[name]
            bindings=[binding for binding in session.backend.bindings if spec.model_port in binding.channels]
            if len(bindings)!=1:
                raise ValueError('Signal port does not have exactly one selected source')
            binding=bindings[0]
            kind='physics' if spec.source=='isaac' else spec.source
            if (binding.source_id!=spec.source_ref or binding.kind!=kind
                    or binding.units.get(spec.model_port)!=spec.unit):
                raise ValueError('Declared source identity, kind or unit conflicts with signal port')
        for reader in readers.values():
            location=inspect.getsourcefile(reader)
            if location is None:raise ValueError('状态源读取器没有可核验来源')
            source.verify(location)
        self.session=session;self.source=source;self.verified_source=verified;self.readers=dict(readers)
        self.store=SignalStore(specs,session_id=session.session_id,epoch=session.epoch)
        self.endpoint=None if endpoint is None else OwnedOpcuaEndpoint(self.store,endpoint=endpoint,now_ns=lambda:session.token.time_ns)
        self._started=False
        self.store.audit.append(dict(event='assembly',source_id=verified.source_id,source_lock=verified.lock_digest,
            attachment='direct' if endpoint is None else 'protocol',catalogue=self.store.catalogue()))

    def start(self):
        self.source.verify(self.verified_source.imported_file)
        if self.endpoint is not None:self.endpoint.start()
        self._started=True
        return self

    def collect(self, frame: BarrierFrame) -> None:
        """原确认步后读取实际帧；拒绝另一世界、旧代际和未确认步。"""
        if not isinstance(frame, BarrierFrame):
            raise ValueError('Expected an explicitly supported confirmed frame')
        if not self._started or frame.token!=self.session.token or self.session.pending is not None:
            raise ValueError('信号来源不是原会话已确认帧')
        if self.store.epoch!=frame.token.epoch:raise ValueError('重新组装前禁止新代际帧')
        for name,reader in self.readers.items():
            spec=self.store.signals[name]
            if spec.model_port not in frame.samples:
                continue  # A confirmed non-sampling tick retains the original sample/TTL.
            sample=reader(frame)
            if isinstance(frame, BarrierFrame):
                sources=[entry for entry in frame.source_evidence if spec.model_port in entry['channels']]
                if len(sources)!=1 or sources[0]['source_id']!=spec.source_ref:
                    raise ValueError('Selected signal port has no unique matching source')
                selected=sources[0]
                expected_kind='physics' if spec.source=='isaac' else spec.source
                if selected['kind']!=expected_kind:
                    raise ValueError('Signal source kind conflicts with confirmed source')
                evidence=selected['evidence']
                unit=selected.get('units',{}).get(spec.model_port)
                if selected['kind']=='analytic':
                    quantity=evidence.get('payload',{}).get(spec.model_port)
                    if isinstance(quantity,dict) and quantity.get('unit')!=unit:
                        raise ValueError('Actual analytic quantity unit conflicts with source declaration')
                elif selected['kind']=='resource_state' and evidence.get('units',{}).get(spec.model_port)!=unit:
                    raise ValueError('Actual canonical observation unit conflicts with source declaration')
                if unit!=spec.unit:
                    raise ValueError('Confirmed source unit conflicts with signal port')
            if not isinstance(sample,Sample) or sample.acquired_tick>frame.token.tick:raise ValueError('来源未提供有效采样身份')
            payload=sample.value
            # 资源版本和量值由所选来源共同提交；其他来源不伪造资源提交版本。
            if spec.source=='resource_state':
                if not isinstance(payload,dict) or set(payload)!={'raw','resource_version'}:raise ValueError('规范状态源缺少量值或版本')
                raw,version=payload['raw'],payload['resource_version']
            else:raw,version=payload,None
            args=dict(acquired_ns=sample.acquired_tick*frame.token.dt_ns,sequence=sample.sequence,
                source_ref=spec.source_ref,resource_version=version,quality='good' if sample.valid else 'unknown')
            token=self.store.grant(spec.writer)
            if self.endpoint is None:self.store.publish(token,name,raw,**args)
            else:self.endpoint.publish(token,name,raw,**args)

    def close(self):
        if self.endpoint is not None:self.endpoint.close()
        self._started=False
