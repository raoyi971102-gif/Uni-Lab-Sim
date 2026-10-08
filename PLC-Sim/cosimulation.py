"""PLC扫描与物理步的同步协调器；设备行为和配置由调用方注入。

唯一时间权威是确认完成的物理步。未知步结果不重放、不提交后续扫描；
显式世界复位成功后才开启新epoch。此模块不解析场景、不连接设备。
"""
from __future__ import annotations
from collections import deque
from copy import deepcopy
from dataclasses import dataclass, asdict
import math
import threading
import time
from typing import Any, Callable, Mapping
import uuid

from unilabos_sim_contracts import CouplingError, PhysicsFrame, PhysicsPort, Sample, StepToken
from .source_barrier import BarrierFrame, SourceBarrierPort


def _positive(value: int, name: str, *, zero: bool = False) -> int:
    if type(value) is not int or value < (0 if zero else 1):
        raise ValueError(name + "必须是整数且满足下界")
    return value


@dataclass(frozen=True)
class Channel:
    period_ticks: int = 1
    latency_ticks: int = 0
    ttl_ticks: int = 2

    def __post_init__(self):
        _positive(self.period_ticks, "采样周期")
        _positive(self.latency_ticks, "交付延迟", zero=True)
        _positive(self.ttl_ticks, "有效期", zero=True)
        if self.latency_ticks > self.ttl_ticks:
            raise ValueError("交付延迟不能超过有效期")


@dataclass(frozen=True)
class Observation:
    token: StepToken
    delivered_tick: int
    value: Any
    sequence: int
    valid: bool


@dataclass(frozen=True)
class Scan:
    token: StepToken
    inputs: Mapping[str, Observation | None]


@dataclass(frozen=True)
class SessionHealth:
    token: StepToken
    pending: StepToken | None
    state: str
    pending_wall_s: float
    stop_requested: bool
    healthy: bool
    execution_failed: bool


class CoupledSession:
    """一个世界、一个同步调用线程、一个已确认的逻辑时钟。

    控制器返回的命令只能写入属于自己的通道。各扫描读取上一已确认步的
    输入；物理步结束后才按周期采样和交付。倍率只改变墙钟调度间隔。
    backend是隔离仿真端口，不得传入真机驱动。异常后保留pending供审计。
    """
    def __init__(self, backend: PhysicsPort, *, dt_ns: int, channels: Mapping[str, Channel],
                 writers: Mapping[str, str], world_id: str | None = None,
                 trace_sink: Callable[[dict[str, Any]], None] | None = None, session_id: str | None = None):
        if trace_sink is not None and not callable(trace_sink):
            raise ValueError("轨迹出口必须可调用")
        self._trace_sink = trace_sink
        self._trace_sequence = 0
        self.dt_ns = _positive(dt_ns, "物理步长")
        self.world_id = world_id or "world-" + uuid.uuid4().hex
        if not isinstance(self.world_id, str) or not self.world_id:
            raise ValueError("世界身份不能为空")
        self.session_id = session_id if session_id is not None else "session-" + uuid.uuid4().hex
        if not isinstance(self.session_id, str) or not self.session_id:
            raise ValueError("会话身份不能为空")
        self.backend = backend
        self.channels = dict(channels)
        self.writers = dict(writers)
        if (any(not isinstance(k, str) or not k or not isinstance(v, Channel) for k, v in self.channels.items())
                or any(not isinstance(k, str) or not k or not isinstance(v, str) or not v for k, v in self.writers.items())):
            raise ValueError("通道及唯一写入者声明无效")
        self.epoch = 0
        self._issued_epoch = 0
        self.tick = 0
        self.state = "uninitialized"
        self.rate = 1.0
        self.pending: StepToken | None = None
        self._controllers: dict[str, tuple[int, Callable[[Scan], Mapping[str, Any]], Callable[[StepToken], None]]] = {}
        self._commands: dict[str, Any] = {}
        self._latest: dict[str, Observation] = {}
        self._seen: dict[str, tuple[int, int]] = {}
        self._delivery: deque[tuple[int, str, Observation]] = deque()
        self._lock = threading.RLock()
        self._in_step = False
        self._owner_thread = threading.get_ident()
        self._stop_requested = threading.Event()
        self._health_lock = threading.Lock()
        self._pending_wall_started: float | None = None
        self._execution_failed = False
        self._health_boundary = (self.token, self.pending, self.state, None, False)

    @property
    def token(self) -> StepToken:
        return StepToken(self.world_id, self.session_id, self.epoch, self.tick, self.dt_ns)

    def now(self) -> float:
        return self.token.time_ns / 1e9

    def _emit_trace(self, phase: str, **payload: Any) -> None:
        """只发送深拷贝边界记录；宿主负责持久化，不在协调器积攒无界日志。"""
        if self._trace_sink is not None:
            self._trace_sequence += 1
            self._trace_sink(deepcopy({"schema": "plc.scan-trace/1", "sequence": self._trace_sequence,
                                      "phase": phase, "token": asdict(self.token), **payload}))

    def _publish_health(self) -> None:
        # 独立短锁，不能在健康锁内调用后端或控制器。
        with self._health_lock:
            self._health_boundary = (self.token, self.pending, self.state, self._pending_wall_started, self._execution_failed)

    def request_stop(self) -> None:
        """任意宿主线程可请求停止后继扫描；不取消在途步或伪造停稳。"""
        self._stop_requested.set()

    def health(self, *, max_pending_s: float, link_ok: bool) -> SessionHealth:
        """墙钟健康独立于世界锁；连接状态由拥有端点的宿主明确提供。"""
        if (type(max_pending_s) not in (int, float) or not math.isfinite(max_pending_s)
                or max_pending_s <= 0 or type(link_ok) is not bool):
            raise ValueError("健康期限必须为有限正秒数，连接状态必须为布尔值")
        with self._health_lock:
            token, pending, state, started, failed = self._health_boundary
        elapsed = 0.0 if started is None else max(0.0, time.monotonic() - started)
        return SessionHealth(token, pending, state, elapsed, self._stop_requested.is_set(),
                             link_ok and not failed and (pending is None or elapsed <= max_pending_s), failed)

    def set_rate(self, rate: float) -> None:
        if type(rate) not in (float, int) or not math.isfinite(rate) or rate <= 0:
            raise ValueError("倍率必须是有限正数")
        with self._lock:
            self._idle()
            self.rate = float(rate)

    def wall_delay(self) -> float:
        return self.dt_ns / 1e9 / self.rate

    def register_controller(self, name: str, period_ticks: int,
                            scan: Callable[[Scan], Mapping[str, Any]], *,
                            reset: Callable[[StepToken], None]) -> None:
        _positive(period_ticks, "扫描周期")
        with self._lock:
            self._idle()
            if (self.state != "uninitialized" or name in self._controllers or not isinstance(name, str)
                    or not name or not callable(scan) or not callable(reset)):
                raise CouplingError("控制器只能在初始化前唯一登记")
            self._controllers[name] = (period_ticks, scan, reset)

    def _idle(self) -> None:
        if threading.get_ident() != self._owner_thread:
            raise CouplingError("生命周期和步进必须在世界所有者线程执行")
        if self._in_step:
            raise CouplingError("不能在扫描或物理步内重入生命周期操作")

    def _validate_frame(self, frame: PhysicsFrame, token: StepToken,
                        channels: tuple[str, ...], elapsed_ns: int) -> None:
        if (not (isinstance(frame, PhysicsFrame) or
                  (isinstance(self.backend, SourceBarrierPort) and isinstance(frame, BarrierFrame))) or frame.token != token
                or type(frame.elapsed_ns) is not int or frame.elapsed_ns != elapsed_ns
                or not isinstance(frame.samples, Mapping) or set(frame.samples) != set(channels)):
            raise CouplingError("物理步确认、身份、时间或采样集合不符")
        for name, sample in frame.samples.items():
            if not isinstance(sample, Sample) or sample.acquired_tick > token.tick:
                raise CouplingError("采样类型或采集时间不符")
            previous = self._seen.get(name) if token.epoch == self.epoch else None
            if previous and (sample.sequence < previous[0] or sample.acquired_tick < previous[1]
                             or (sample.sequence == previous[0] and sample.acquired_tick != previous[1])):
                raise CouplingError("采样序号或采集时间倒退/重标")

    def reset(self) -> PhysicsFrame:
        """显式重置整个世界，不等同于命令Reset或控制器重启。"""
        with self._lock:
            self._idle()
            if self.state == "running":
                raise CouplingError("运行中不能重置世界，须先暂停")
            self._stop_requested.clear()
            self._issued_epoch += 1
            token = StepToken(self.world_id, self.session_id, self._issued_epoch, 0, self.dt_ns)
            self.pending = token
            self._in_step = True
            self._pending_wall_started = time.monotonic()
            self._publish_health()
            try:
                self._emit_trace("reset_requested", requested=asdict(token))
                frame = self.backend.reset(token, tuple(sorted(self.channels)))
                self._validate_frame(frame, token, tuple(sorted(self.channels)), 0)
                self._emit_trace("reset_confirmed", frame=asdict(frame))
                samples = deepcopy(dict(frame.samples))
                for _, _, reset in self._controllers.values():
                    reset(token)
                self.epoch, self.tick = token.epoch, 0
                self._commands.clear()
                self._latest.clear()
                self._seen.clear()
                self._delivery.clear()
                self._collect(token, samples)
                self._deliver()
                self.pending = None
                self._execution_failed = False
                self.state = "stopped" if self._stop_requested.is_set() else "paused"
                return frame
            except Exception:
                self._execution_failed = True
                self.state = "faulted"
                raise
            finally:
                self._in_step = False
                if self.pending is None:
                    self._pending_wall_started = None
                self._publish_health()

    def pause(self) -> None:
        with self._lock:
            self._idle()
            if self.state not in {"paused", "running"}:
                raise CouplingError("当前世界不能暂停")
            self.state = "paused"
            self._publish_health()

    def resume(self) -> None:
        with self._lock:
            self._idle()
            if self._stop_requested.is_set():
                raise CouplingError("已请求停止；恢复须显式重置")
            if self.state != "paused":
                raise CouplingError("只有已确认暂停的世界可以恢复")
            self.state = "running"
            self._publish_health()

    def stop(self) -> None:
        """封闭调度入口；不代表执行器已停止，也不清除未知确认。

        物理停止和资源释放仍由宿主等待其独立停止证明。本方法不推进世界、
        不提交动作成功。重新启动必须显式 reset；普通 resume 不能绕过停止。
        """
        with self._lock:
            self._idle()
            self._stop_requested.set()
            self.state = "stopped"
            self._publish_health()

    def observation(self, name: str) -> Observation | None:
        with self._lock:
            if name not in self.channels:
                raise KeyError(name)
            value = self._latest.get(name)
            if (self.state in {"uninitialized", "faulted", "stopped"} or value is None
                    or not value.valid or value.token.epoch != self.epoch
                    or self.tick - value.token.tick > self.channels[name].ttl_ticks):
                return None
            return deepcopy(value)

    def _collect(self, token: StepToken, samples: dict[str, Sample]) -> None:
        for name, sample in samples.items():
            previous = self._seen.get(name)
            if previous is not None and sample.sequence == previous[0]:
                continue  # 重复交付不更新采集时间或TTL。
            self._seen[name] = (sample.sequence, sample.acquired_tick)
            acquired = StepToken(token.world_id, token.session_id, token.epoch, sample.acquired_tick, token.dt_ns)
            due = max(token.tick, sample.acquired_tick + self.channels[name].latency_ticks)
            self._delivery.append((due, name, Observation(acquired, due, sample.value, sample.sequence, sample.valid)))
        self._delivery = deque(sorted(self._delivery, key=lambda row: (row[0], row[1])))

    def _deliver(self) -> None:
        while self._delivery and self._delivery[0][0] <= self.tick:
            _, name, value = self._delivery.popleft()
            self._latest[name] = value

    def step(self, *, single: bool = False) -> PhysicsFrame:
        with self._lock:
            self._idle()
            if self._stop_requested.is_set():
                raise CouplingError("已请求停止；禁止后继步骤")
            if type(single) is not bool or self.state != ("paused" if single else "running"):
                raise CouplingError("单步须暂停，连续推进须运行；未知步结果禁止继续")
            self._in_step = True
            token = StepToken(self.world_id, self.session_id, self.epoch, self.tick + 1, self.dt_ns)
            self.pending = token
            self._pending_wall_started = time.monotonic()
            self._publish_health()
            try:
                commands = deepcopy(self._commands)
                snapshot = {name: self.observation(name) for name in self.channels}
                controller_outputs = []
                for writer, (period, callback, _) in self._controllers.items():
                    if self.tick % period:
                        continue
                    scan = Scan(self.token, deepcopy(snapshot))
                    changed = callback(scan)
                    if not isinstance(changed, Mapping) or any(self.writers.get(key) != writer for key in changed):
                        raise CouplingError("控制器写入未授权通道")
                    changed = deepcopy(dict(changed))
                    controller_outputs.append({"writer": writer, "commands": changed})
                    commands.update(changed)
                channels = tuple(sorted(name for name, c in self.channels.items() if token.tick % c.period_ticks == 0))
                self._emit_trace("scan_sealed", requested=asdict(token),
                                 inputs={name: asdict(value) if value is not None else None for name, value in snapshot.items()},
                                 controller_outputs=controller_outputs, held_commands=commands,
                                 sample_channels=channels)
                frame = self.backend.step(token, deepcopy(commands), channels)
                self._validate_frame(frame, token, channels, self.dt_ns)
                self._emit_trace("step_confirmed", frame=asdict(frame))
                samples = deepcopy(dict(frame.samples))
                self.tick = token.tick
                self._commands = commands
                self._collect(token, samples)
                self._deliver()
                self._emit_trace("step_committed", commands=self._commands,
                                 delivered={name: asdict(value) for name, value in self._latest.items()},
                                 queued=[{"due": due, "channel": name, "sample": asdict(value)}
                                         for due, name, value in self._delivery])
                self.pending = None
                if self._stop_requested.is_set():
                    self.state = "stopped"
                return frame
            except Exception:
                self._execution_failed = True
                self.state = "faulted"
                raise
            finally:
                self._in_step = False
                if self.pending is None:
                    self._pending_wall_started = None
                self._publish_health()
