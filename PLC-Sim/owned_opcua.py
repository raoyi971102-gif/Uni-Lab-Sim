"""选定信号合同的共享 OPC UA 端点；原协议数据报不添加身份字段。"""
from __future__ import annotations
from copy import deepcopy
import secrets
from threading import RLock, Event, Thread
from typing import Callable
from opcua import Server, ua
from opcua.server.internal_server import InternalServer, InternalSession
from .common import VTYPE_MAP
from .signal_runtime import SignalStore


class OwnedOpcuaEndpoint:
    """每个实例持有一个 SignalStore。登录身份在服务端冻结到原 owner 令牌。"""
    _active={}
    _lock=RLock()

    def __init__(self, store: SignalStore, *, endpoint: str, now_ns: Callable[[],int]):
        if not endpoint.startswith('opc.tcp://127.0.0.1:'): raise ValueError('仿真端点只允许本机回环')
        self.store,self.endpoint,self.now_ns=store,endpoint,now_ns
        self.credentials={};self.nodes={};self._sequence={};self._started=False
        self._stop=Event();self._thread=None;self._quality_error=None;self._mirrored={};self._last_raw={}
        owner=self
        class Session(InternalSession):
            def read(self,params):
                with owner.store._lock:
                    owner.refresh_quality()
                    return super().read(params)

            def write(self,params):
                result=[]
                for item in params.NodesToWrite:
                    name=owner._names.get(item.NodeId.to_string())
                    try:
                        if item.AttributeId!=ua.AttributeIds.Value or name is None: raise PermissionError('unknown_attribute')
                        token=getattr(self,'signal_owner',None)
                        spec=owner.store.check(token,name)
                        if spec.role=='sensor_input': raise PermissionError('sensor_requires_versioned_source_adapter')
                        if item.Value.Value.VariantType!=VTYPE_MAP[spec.data_type]: raise ValueError('wire_type_mismatch')
                        owner.publish(token,name,item.Value.Value.Value)
                        result.append(ua.StatusCode())
                    except (PermissionError,ValueError) as exc:
                        owner.store.audit.append(dict(event='protocol_rejected',signal=name,reason=str(exc),epoch=owner.store.epoch))
                        result.append(ua.StatusCode(ua.StatusCodes.BadUserAccessDenied if isinstance(exc,PermissionError) else ua.StatusCodes.BadTypeMismatch))
                return result
        internal=InternalServer();internal.session_cls=Session
        self.server=Server(iserver=internal);self.server.set_endpoint(endpoint)
        self.server.set_security_IDs(['Username','Anonymous'])
        def authenticate(session,username,password):
            pair=owner.credentials.get(username)
            if pair is None or not secrets.compare_digest(pair[0],password):return False
            session.signal_owner=pair[1]
            return True
        self.server.user_manager.set_user_manager(authenticate)
        max_ns=max(ua.NodeId.from_string(s.node_id).NamespaceIndex for s in store.signals.values())
        while len(self.server.get_namespace_array())<=max_ns:self.server.register_namespace('urn:plc-signals:'+str(len(self.server.get_namespace_array())))
        for name,spec in store.signals.items():
            default=False if spec.data_type=='BOOLEAN' else '' if spec.data_type=='STRING' else 0
            node=self.server.get_objects_node().add_variable(ua.NodeId.from_string(spec.node_id),name,default,varianttype=VTYPE_MAP[spec.data_type])
            node.set_writable();self.nodes[name]=node;self._last_raw[name]=default
        self._names={node.nodeid.to_string():name for name,node in self.nodes.items()}
        self._unsubscribe=self.store.observe_changes(self.refresh_quality)
        self.refresh_quality()

    def credential(self,writer: str) -> tuple[str,str]:
        token=self.store.grant(writer);username=writer+'-'+str(token.epoch);password=secrets.token_urlsafe(32)
        self.credentials[username]=(password,token)
        return username,password

    def publish(self,token,name,raw,**sample):
        """内部状态源与协议写入共同经过 owner 校验，随后更新协议映像。"""
        with self.store._lock:
            if self._quality_error is not None:raise RuntimeError('质量投影不可用') from self._quality_error
            key=(self.store.epoch,name)
            sequence=sample.pop('sequence',self._sequence.get(key,0)+1)
            acquired=sample.pop('acquired_ns',self.now_ns())
            self.store.publish(token,name,raw,sequence=sequence,acquired_ns=acquired,**sample)
            self._sequence[key]=sequence

    def refresh_quality(self):
        """更新真实地址空间，因此 Read 与已有/新订阅使用同一质量。"""
        with self.store._lock:
            view=self.store.snapshot(self.now_ns())['signals']
            for name,row in view.items():
                raw=row['raw'] if row['raw'] is not None else self._last_raw[name]
                quality=row['quality']
                code=ua.StatusCodes.Good if quality=='good' else ua.StatusCodes.BadNoData if quality=='unknown' else ua.StatusCodes.BadOutOfService
                if self._mirrored.get(name)==(raw,code):continue
                value=ua.DataValue(ua.Variant(raw,VTYPE_MAP[self.store.signals[name].data_type]))
                value.StatusCode=ua.StatusCode(code)
                self._publish_datavalue(name,value)
                self._last_raw[name]=deepcopy(raw);self._mirrored[name]=(deepcopy(raw),code)

    def _publish_datavalue(self,name,value):
        # python-opcua 当前 AddressSpace 只在 Variant 改变时通知，遗漏仅状态码变化。
        # 仅对本端点地址空间补发标准 DataValue 通知，不修改厂家节点或全局库。
        aspace=self.server.iserver.aspace;node=self.nodes[name]
        with aspace._lock:
            attribute=aspace._nodes[node.nodeid].attributes[ua.AttributeIds.Value]
            old=attribute.value
            callbacks=(list(attribute.datachange_callbacks.items())
                if old.Value==value.Value and old.StatusCode!=value.StatusCode else [])
            node.set_value(value)
        for handle,callback in callbacks:
            callback(handle,deepcopy(value))

    def _watch_quality(self):
        # 此线程只观察宿主已有时钟，墙钟等待不推进仿真时间或生成采样。
        while not self._stop.wait(.01):
            try:self.refresh_quality()
            except Exception as exc:
                self._quality_error=exc
                self.store.audit.append(dict(event='quality_projection_failed',reason=str(exc)))
                self.server.stop()
                return

    def start(self):
        with self._lock:
            if self.endpoint in self._active:raise ValueError('共享端点已存在，请复用原实例')
            if self._quality_error is not None:raise RuntimeError('质量投影曾失败，必须重建端点') from self._quality_error
            self._stop.clear()
            if self._unsubscribe is None:self._unsubscribe=self.store.observe_changes(self.refresh_quality)
            self.refresh_quality();self.server.start();self._active[self.endpoint]=self;self._started=True
            self._thread=Thread(target=self._watch_quality,name="signal-quality",daemon=True);self._thread.start()
        return self

    def close(self):
        with self._lock:
            if self._unsubscribe is not None:
                self._unsubscribe();self._unsubscribe=None
            if self._started:
                self._stop.set()
                if self._thread is not None:self._thread.join(timeout=2)
                self.server.stop();self._active.pop(self.endpoint,None);self._started=False
