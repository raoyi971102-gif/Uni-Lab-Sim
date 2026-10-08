"""所选来源的确认屏障；只组合既有确认，不创建第二个时钟。"""
from __future__ import annotations
from copy import deepcopy
from dataclasses import dataclass, asdict, replace
from types import MappingProxyType
from typing import get_args
from unilabos_sim_contracts.frames import Unit
from typing import Any, Callable, Mapping
from unilabos_sim_contracts import (
    ContractFrame, ResourceEffectFrame, PhysicsFrame, Sample, StepToken, Quantity,
    CouplingError, validate_frame_context,
)

@dataclass(frozen=True)
class SourceResult:
    evidence: ContractFrame | PhysicsFrame | Mapping[str, Any]
    samples: Mapping[str, Sample]
    # 外部数据库 schema 无来源时间时必须明确 None，不能从到达墙钟推算。
    source_time: int | None = None
    units: Mapping[str, str] | None = None

@dataclass(frozen=True)
class SourceBinding:
    source_id: str
    kind: str
    provider: Any
    channels: tuple[str, ...]
    request_id: str
    contract_digest: str
    quantity_authorities: tuple[str, ...] = ()
    verify_canonical: Callable[[Mapping[str, Any], Mapping[str, Sample]], bool] | None = None
    max_confirmation_age_ns: int | None = None
    resource_progress: str | None = None
    units: Mapping[str, str] | None = None

@dataclass(frozen=True)
class BarrierFrame:
    token: StepToken
    elapsed_ns: int
    samples: Mapping[str, Sample]
    source_evidence: tuple[dict[str, Any], ...]

class SourceBarrierPort:
    """按显式登记顺序调用来源，全部必需确认合格后返回非物理聚合帧。

    接收端仍由 CoupledSession 管理单一 tick。部分来源已执行而另一来源失败时，
    异常直接传回使会话保留 pending；不回滚、不重放先前来源。
    """
    def __init__(self, bindings: tuple[SourceBinding, ...]):
        if not isinstance(bindings, tuple) or not bindings:
            raise ValueError('至少声明一个必需来源')
        identities, channels, authorities = set(), set(), set()
        frozen_bindings = []
        for binding in bindings:
            if (not binding.source_id or binding.source_id in identities
                    or binding.kind not in {'physics', 'analytic', 'resource_state'}):
                raise ValueError('来源身份重复或来源种类不支持')
            if len(set(binding.channels)) != len(binding.channels) or channels.intersection(binding.channels):
                raise ValueError('采样通道必须有唯一来源')
            if (len(set(binding.quantity_authorities)) != len(binding.quantity_authorities)
                    or authorities.intersection(binding.quantity_authorities)):
                raise ValueError('耦合量必须有唯一更新权威')
            if binding.kind == 'resource_state' and not callable(binding.verify_canonical):
                raise ValueError('规范状态来源必须绑定当前规范回执验证器')
            if binding.kind == 'resource_state' and (type(binding.max_confirmation_age_ns) is not int or binding.max_confirmation_age_ns < 0):
                raise ValueError('规范来源必须显式声明确认有效期')
            if binding.kind == 'resource_state' and binding.resource_progress not in {'effect','observation'}:
                raise ValueError('规范来源须显式区分效果阶段和状态观察')
            units = dict(binding.units or {})
            if set(units) != set(binding.channels) or any(unit not in get_args(Unit) for unit in units.values()):
                raise ValueError('每个来源通道必须显式声明受支持的工程单位')
            frozen_bindings.append(replace(binding, units=MappingProxyType(units)))
            identities.add(binding.source_id); channels.update(binding.channels)
            authorities.update(binding.quantity_authorities)
        self.bindings = tuple(frozen_bindings)
        self.channels = frozenset(channels)
        self._seen: dict[str, int] = {}
        self._effects: set[tuple[str, str]] = set()

    def reset(self, token: StepToken, channels: tuple[str, ...]) -> BarrierFrame:
        return self._advance(token, {}, channels, initial=True)

    def step(self, token: StepToken, commands: Mapping[str, Any], channels: tuple[str, ...]) -> BarrierFrame:
        return self._advance(token, commands, channels, initial=False)

    def _advance(self, token, commands, channels, *, initial):
        if not set(channels) <= self.channels:
            raise CouplingError('扫描需要未声明的来源通道')
        samples, source_evidence, sequences, effects = {}, [], {}, set()
        for binding in self.bindings:
            selected = tuple(name for name in channels if name in binding.channels)
            result = (binding.provider.reset(token, selected) if initial
                      else binding.provider.step(token, deepcopy(dict(commands)), selected))
            if not isinstance(result, SourceResult) or set(result.samples) != set(selected):
                raise CouplingError('必需来源未返回所选采样集合')
            evidence = result.evidence
            if binding.kind == 'analytic' and isinstance(evidence, ContractFrame):
                units = {name: (value.unit if isinstance(value, Quantity) else (result.units or {}).get(name))
                         for name in selected for value in (evidence.payload.get(name),)}
            elif binding.kind == 'resource_state' and isinstance(evidence, Mapping):
                units = evidence.get('units', {})
            else:
                units = result.units or {}
            if not isinstance(units, Mapping) or any(units.get(name) != binding.units[name] for name in selected):
                raise CouplingError('来源确认的工程单位与固定通道声明不符')
            if binding.kind == 'physics':
                if (not isinstance(evidence, PhysicsFrame) or evidence.token != token
                        or evidence.elapsed_ns != (0 if initial else token.dt_ns)
                        or evidence.samples != result.samples):
                    raise CouplingError('实际物理确认不符合所请求边界')
                record = asdict(evidence)
            elif binding.kind == 'resource_state':
                if not isinstance(evidence, Mapping) or result.source_time is not None or evidence.get('source_time') is not None:
                    raise CouplingError('规范凭据保留原映射及未知来源时间，不得伪造时刻或物理帧')
                request = evidence.get('request', {})
                if ((request.get('session_id'), request.get('epoch'), request.get('request_id'))
                        != (token.session_id, token.epoch, binding.request_id)):
                    raise CouplingError('规范凭据请求或代际不符')
                if not isinstance(evidence.get('resource_versions'), Mapping) or not evidence['resource_versions']:
                    raise CouplingError('规范凭据缺资源版本')
                if not initial and binding.resource_progress == 'effect':
                    effect_id = evidence.get('effect_id')
                    times = [evidence.get(name) for name in
                             ('freshness_lower_bound_ns', 'receipt_received_at_ns', 'observed_at_ns')]
                    if (not isinstance(effect_id, str) or not effect_id or any(type(t) is not int for t in times)
                            or not 0 <= times[0] <= times[1] <= times[2] <= token.time_ns
                            or token.time_ns - times[0] > binding.max_confirmation_age_ns):
                        raise CouplingError('规范效果缺保守新鲜度证据或已过期')
                    key = (binding.source_id, 'effect:' + effect_id)
                    if key in self._effects:
                        raise CouplingError('重复规范效果不能推进第二阶段')
                    effects.add(key)
                if not initial and binding.resource_progress == 'observation':
                    observed = evidence.get('observed_at_ns')
                    query_id = evidence.get('query_id')
                    if (not isinstance(query_id, str) or not query_id or type(observed) is not int
                            or not 0 <= observed <= token.time_ns
                            or token.time_ns - observed > binding.max_confirmation_age_ns):
                        raise CouplingError('当前状态观察缺实际查询身份或已过期')
                    key = (binding.source_id, 'query:' + query_id)
                    if key in self._effects:
                        raise CouplingError('重复查询回执不能冒充新观察')
                    effects.add(key)
                for sample in result.samples.values():
                    if sample.acquired_tick * token.dt_ns != evidence.get('observed_at_ns'):
                        raise CouplingError('规范样本必须保留原查询时刻')
                if binding.verify_canonical(deepcopy(dict(evidence)), deepcopy(dict(result.samples))) is not True:
                    raise CouplingError('当前规范记录或资源版本不能确认')
                record = deepcopy(dict(evidence))
            else:
                if not isinstance(evidence, ContractFrame) or isinstance(evidence, ResourceEffectFrame):
                    raise CouplingError('非物理解析来源必须提供自身模型确认')
                validate_frame_context(evidence, token=token, request_id=binding.request_id,
                    source_id=binding.source_id, contract_digest=binding.contract_digest,
                    previous_sequence=None if initial else self._seen.get(binding.source_id))
                if evidence.quality != 'valid':
                    raise CouplingError('必需来源确认质量无效')
                for name, sample in result.samples.items():
                    quantity = evidence.payload.get(name)
                    expected = quantity.value if isinstance(quantity, Quantity) else quantity
                    if quantity is None:
                        # 来源已确认但明确缺测；无效空值不能冒充数值或有效传感器。
                        valid_type = sample.value is None and sample.valid is False
                    elif isinstance(quantity, Quantity):
                        valid_type = type(sample.value) in (int, float)
                    else:
                        valid_type = (type(quantity) in (bool, str) and type(sample.value) is type(quantity)
                                      and units.get(name) == '1')
                    if (not valid_type or name not in evidence.payload or sample.value != expected
                            or sample.acquired_tick * token.dt_ns != evidence.captured_at):
                        raise CouplingError('解析样本与实际确认值或采集边界不符')
                sequences[binding.source_id] = evidence.sequence
                record = evidence.model_dump(mode='json')
            samples.update(deepcopy(dict(result.samples)))
            source_evidence.append({'source_id':binding.source_id, 'kind':binding.kind,
                                  'source_time':result.source_time, 'channels':binding.channels, 'units':dict(binding.units), 'evidence':record,
                                  'stage_advanced':binding.kind == 'resource_state' and not initial and binding.resource_progress == 'effect'})
        if initial:
            self._seen.clear(); self._effects.clear()
        self._seen.update(sequences); self._effects.update(effects)
        return BarrierFrame(token, 0 if initial else token.dt_ns, samples, tuple(source_evidence))
