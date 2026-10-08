"""共享信号契约及按代际隔离的唯一写入入口；不实现设备完成策略。"""
from __future__ import annotations
from copy import deepcopy
from dataclasses import asdict, dataclass
import math
import secrets
from threading import RLock
from types import MappingProxyType
from typing import Any, Mapping

from .common import NodeDef

ROLES = {'host_command', 'controller_status', 'actuator_output', 'sensor_input'}
SOURCES = {'resource_state', 'analytic', 'isaac', 'telemetry', 'recorded'}


@dataclass(frozen=True)
class Signal:
    name: str
    node_id: str
    data_type: str
    role: str
    writer: str
    unit: str
    scale: float
    offset: float
    minimum: float | None
    maximum: float | None
    period_ns: int
    ttl_ns: int
    source: str | None
    source_ref: str | None
    model_port: str

    def convert(self, raw: Any) -> Any:
        if self.data_type == 'BOOLEAN':
            if type(raw) is not bool: raise ValueError('布尔类型不符')
            return raw
        if self.data_type == 'STRING':
            if not isinstance(raw, str): raise ValueError('字符串类型不符')
            return raw
        limits = {'BYTE': (0,255), 'INT16': (-32768,32767), 'INT32': (-2147483648,2147483647)}
        try:finite=type(raw) in (int,float) and math.isfinite(raw)
        except OverflowError:finite=False
        if not finite: raise ValueError('数值类型或有限性不符')
        maximum_raw=3.4028234663852886e38 if self.data_type=='FLOAT' else 1.7976931348623157e308
        if self.data_type in ('FLOAT','DOUBLE') and abs(raw)>maximum_raw:raise ValueError('原始浮点表示越界')
        if self.data_type in limits:
            low,high=limits[self.data_type]
            if type(raw) is not int or not low <= raw <= high: raise ValueError('原始整数越界')
        try:result=raw*self.scale+self.offset
        except OverflowError as exc:raise ValueError('工程量转换溢出') from exc
        if not math.isfinite(result):raise ValueError('工程量转换结果不是有限数')
        if ((self.minimum is not None and result<self.minimum) or
                (self.maximum is not None and result>self.maximum)): raise ValueError('工程量越界')
        return result

    def raw(self, engineering: Any) -> Any:
        if self.data_type in ('BOOLEAN','STRING'): return self.convert(engineering)
        try:finite=type(engineering) in (int,float) and math.isfinite(engineering)
        except OverflowError:finite=False
        if not finite:raise ValueError('工程量类型不符')
        try:value=(engineering-self.offset)/self.scale
        except OverflowError as exc:raise ValueError('反向换算溢出') from exc
        if not math.isfinite(value):raise ValueError('反向换算结果不是有限数')
        if self.data_type in ('BYTE','INT16','INT32'):
            if not math.isclose(value,round(value),abs_tol=1e-9,rel_tol=0): raise ValueError('工程量无法精确表达为原整数')
            value=round(value)
        self.convert(value)
        return value


def compile_signals(nodes: list[NodeDef], semantics: Mapping[str,Mapping[str,Any]]) -> dict[str,Signal]:
    """CSV 独占地址及原始类型；语义目录只引用 CSV 名称。"""
    by_name={n.name_cn:n for n in nodes}
    if len(by_name)!=len(nodes) or len({n.node_id for n in nodes})!=len(nodes): raise ValueError('重复 CSV 名称或地址')
    result={}
    for name,values in semantics.items():
        if name not in by_name: raise ValueError('未知 CSV 表项: '+name)
        nd=by_name[name];v=dict(values)
        if 'node_id' in v or 'address' in v: raise ValueError('语义文件不能复制地址')
        expected=v.pop('expected_type',nd.data_type)
        if expected!=nd.data_type or nd.data_type not in ('BOOLEAN','STRING','BYTE','INT16','INT32','FLOAT','DOUBLE') or nd.array_len: raise ValueError('映射类型冲突或不支持的数组')
        spec=Signal(name=name,node_id=nd.node_id,data_type=nd.data_type,**v)
        if (spec.role not in ROLES or not spec.writer or not spec.unit or not spec.model_port
                or type(spec.period_ns) is not int or spec.period_ns<=0
                or type(spec.ttl_ns) is not int or spec.ttl_ns<0
                or type(spec.scale) not in (int,float) or not math.isfinite(spec.scale) or spec.scale==0
                or type(spec.offset) not in (int,float) or not math.isfinite(spec.offset)
                or any(v is not None and (type(v) not in (int,float) or not math.isfinite(v)) for v in (spec.minimum,spec.maximum))
                or spec.minimum is not None and spec.maximum is not None and spec.minimum>spec.maximum): raise ValueError('信号语义合同无效')
        if spec.role=='sensor_input':
            if spec.source not in SOURCES or not spec.source_ref: raise ValueError('传感器必须显式选择来源')
        elif spec.source is not None or spec.source_ref is not None: raise ValueError('命令及控制状态不能伪装传感来源')
        if nd.data_type in ('BOOLEAN','STRING') and (spec.scale!=1 or spec.offset!=0): raise ValueError('非数值不可缩放')
        result[name]=spec
    if not result: raise ValueError('空信号合同')
    return result


@dataclass(frozen=True)
class OwnerToken:
    session_id: str
    epoch: int
    writer: str
    secret: str


class SignalStore:
    """原始零值与未知质量分别保存；采集时刻由唯一宿主时钟提供。"""
    def __init__(self, signals: Mapping[str,Signal], *, session_id: str, epoch: int):
        if not session_id or type(epoch) is not int or epoch<0: raise ValueError('会话身份无效')
        self.signals=MappingProxyType(dict(signals));self.session_id=session_id;self.epoch=epoch
        self._tokens={};self._samples={};self._version=0;self._lock=RLock();self.audit=[]
        self._credentials={};self._triggers={};self._rising={};self._listeners=[]

    def observe_changes(self, callback):
        """订阅实际存储变更，协议映像在同一写边界更新。"""
        with self._lock:self._listeners.append(callback)
        def remove():
            with self._lock:
                if callback in self._listeners:self._listeners.remove(callback)
        return remove

    def _notify(self):
        for callback in tuple(self._listeners):callback()

    def grant(self, writer: str) -> OwnerToken:
        with self._lock:
            if writer not in {s.writer for s in self.signals.values()}: raise ValueError('未声明写入者')
            if writer in self._tokens: return self._tokens[writer]
            token=OwnerToken(self.session_id,self.epoch,writer,secrets.token_urlsafe(32))
            self._tokens[writer]=token
            return token

    def check(self, token: OwnerToken, name: str) -> Signal:
        spec=self.signals.get(name)
        reason=None
        if spec is None: reason='unknown_signal'
        elif (not isinstance(token,OwnerToken) or token.session_id!=self.session_id or token.epoch!=self.epoch
                or self._tokens.get(token.writer)!=token): reason='stale_owner'
        elif token.writer!=spec.writer: reason='wrong_writer'
        if reason:
            self.audit.append(dict(event='rejected',reason=reason,signal=name,session=self.session_id,epoch=self.epoch,
                writer=getattr(token,'writer',None)))
            raise PermissionError(reason)
        return spec

    def publish(self, token: OwnerToken, name: str, raw: Any, *, acquired_ns: int, sequence: int,
                source_ref: str | None=None, resource_version: int | None=None, quality: str='good') -> None:
        with self._lock:
            spec=self.check(token,name)
            if quality not in ('good','unknown','fault') or type(acquired_ns) is not int or acquired_ns<0 or type(sequence) is not int or sequence<0: raise ValueError('采样身份或质量无效')
            if resource_version is not None and (type(resource_version) is not int or resource_version<0): raise ValueError('规范版本无效')
            if spec.source_ref!=source_ref: raise ValueError('采样来源不符')
            if spec.source=='resource_state' and (type(resource_version) is not int or resource_version<0): raise ValueError('规范投影缺少提交版本')
            missing = raw is None and spec.role == 'sensor_input' and quality == 'unknown'
            # 缺测保留身份与无效质量，不进行数值转换或沿用上次有效值。
            engineering = None if missing else spec.convert(raw)
            value=dict(raw=deepcopy(raw),value=engineering,acquired_ns=acquired_ns,sequence=sequence,
                source=spec.source,source_ref=source_ref,resource_version=resource_version,quality=quality,
                session_id=self.session_id,epoch=self.epoch,writer=token.writer)
            old=self._samples.get(name)
            if old is not None:
                same={k:v for k,v in old.items() if k!='revision'}
                if sequence==old['sequence']:
                    if value!=same: raise ValueError('重复序号异内容或重标时')
                    return
                if sequence<old['sequence'] or acquired_ns<old['acquired_ns']: raise ValueError('采样乱序')
                if resource_version is not None and old['resource_version'] is not None and resource_version<old['resource_version']: raise ValueError('规范版本回退')
            self._version+=1;value['revision']=self._version;self._samples[name]=value
            if type(raw) is bool and raw is True and old is not None and old['raw'] is False:
                self._rising[name]=self._version
            self.audit.append(dict(event='write',signal=name,writer=token.writer,epoch=self.epoch,
                source_ref=source_ref,revision=self._version,sequence=sequence))
            self._notify()

    def snapshot(self, now_ns: int) -> dict[str,Any]:
        with self._lock:
            if type(now_ns) is not int or now_ns<0: raise ValueError('观察时间无效')
            values={}
            for name,spec in self.signals.items():
                row=deepcopy(self._samples.get(name))
                if row is None:row=dict(value=None,raw=None,quality='unknown',reason='no_sample')
                elif row['acquired_ns']>now_ns:row.update(quality='unknown',reason='future_sample')
                elif now_ns-row['acquired_ns']>spec.ttl_ns:row.update(quality='stale',reason='ttl_expired')
                values[name]=row
            return dict(session_id=self.session_id,epoch=self.epoch,revision=self._version,signals=values)

    def activate(self, epoch: int, *, reason: str) -> None:
        with self._lock:
            if epoch!=self.epoch+1 or not reason: raise ValueError('接管必须记录原因并递增一个代际')
            self.epoch=epoch;self._tokens={};self._samples={};self._triggers={};self._rising={};self._version+=1
            self.audit.append(dict(event='activate',epoch=epoch,reason=reason))
            self._notify()

    def catalogue(self) -> list[dict[str,Any]]:
        return [dict(asdict(spec),session_id=self.session_id,epoch=self.epoch,invalid_policy='unknown_or_stale') for spec in self.signals.values()]

    def latch(self, trigger: str, parameters: tuple[str,...], *, now_ns: int) -> dict[str,Any]:
        """包声明真实触发位。逐写必须均晚于上次锁存，不宣称多节点原子。"""
        with self._lock:
            if any(self.signals[n].role!='host_command' for n in (trigger,*parameters)): raise ValueError('锁存只能读取命令')
            view=self.snapshot(now_ns);rows=view['signals'];prior=self._triggers.get(trigger,0)
            gate=rows[trigger]
            if gate['quality']!='good' or gate['raw'] is not True or gate['revision']<=prior or self._rising.get(trigger)!=gate['revision']: raise ValueError('缺少新的触发沿')
            if any(rows[n]['quality']!='good' or rows[n]['revision']<=prior or rows[n]['revision']>=gate['revision'] for n in parameters): raise ValueError('参数未完整提交到触发边界')
            self._triggers[trigger]=gate['revision']
            return dict(revision=gate['revision'],parameters={n:rows[n]['value'] for n in parameters},epoch=self.epoch)
