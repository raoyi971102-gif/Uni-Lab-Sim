"""由宿主事件循环驱动既有会话；墙钟只限速，不产生逻辑时刻。"""
from __future__ import annotations

from dataclasses import replace
import math
import time
from typing import Callable

from .cosimulation import CoupledSession, CouplingError


class SessionHost:
    """所有生命周期调用属于会话所有者线程，外部线程只可 request_stop/health。

    每次 poll 至多推进一帧；迟到不补算，下一期限从本次完成墙钟开始。
    暂停不收集外部事实；恢复后等一个新周期，单步显式收集当前来源。
    """

    def __init__(self, session: CoupledSession, *, collect: Callable,
                 allowed_rates: tuple[float, ...], on_stop: Callable[[], None]) -> None:
        if (not isinstance(allowed_rates, tuple) or not allowed_rates
                or any(type(rate) not in (int, float) or not math.isfinite(rate) or rate <= 0
                       for rate in allowed_rates)):
            raise ValueError("必须声明已准入的有限正倍率集合")
        if not callable(collect) or not callable(on_stop):
            raise ValueError("必须绑定来源收集和本请求停止接口")
        if session.rate not in allowed_rates:
            raise ValueError("现有会话倍率不在已准入范围")
        self.session = session
        self._collect = collect
        self._on_stop = on_stop
        self.allowed_rates = allowed_rates
        self._due = None
        self._failed = False
        self._stopped = False
        self.last_timing = None

    def _check(self) -> None:
        self.session._idle()
        if self._failed or self._stopped:
            raise CouplingError("宿主已停止或来源交付失败")
        if self.session.rate not in self.allowed_rates:
            raise CouplingError("现有会话倍率超出宿主准入范围")

    def resume(self) -> None:
        self._check()
        self.session.resume()
        self._due = time.monotonic() + self.session.wall_delay()

    def pause(self) -> None:
        self._check()
        self.session.pause()
        self._due = None

    def set_rate(self, rate: float) -> None:
        self._check()
        if type(rate) not in (int, float) or rate not in self.allowed_rates:
            raise ValueError("该组合未获此倍率资格")
        self.session.set_rate(rate)
        self._due = (time.monotonic() + self.session.wall_delay()
                     if self.session.state == "running" else None)

    def _advance(self, *, single: bool):
        try:
            frame = self.session.step(single=single)
            if self.session._stop_requested.is_set():
                # 在途来源可已确认，但停止请求之后不再派发包内动作阶段。
                self.stop()
                return frame
            self._collect(frame)
            return frame
        except Exception:
            self._failed = True
            self.session.request_stop()
            raise

    def step(self):
        self._check()
        return self._advance(single=True)

    def poll(self):
        self.session._idle()
        if self.session._stop_requested.is_set():
            self.stop()
            return None
        if self._stopped:
            return None
        self._check()
        if self.session.state != "running":
            return None
        if self._due is None:
            raise CouplingError("请经宿主 resume 建立运行节拍")
        started = time.monotonic()
        if started < self._due:
            return None
        lateness = started - self._due
        frame = self._advance(single=False)
        finished = time.monotonic()
        self.last_timing = dict(tick=self.session.tick, lateness_s=lateness,
                                execution_wall_s=finished-started, target_interval_s=self.session.wall_delay())
        self._due = finished + self.session.wall_delay()
        return frame

    def stop(self) -> None:
        self.session._idle()
        if self._stopped:
            return
        self.session.stop()
        self._stopped = True
        self._due = None
        self._on_stop()

    def request_stop(self) -> None:
        self.session.request_stop()

    def health(self, *, max_pending_s: float, link_ok: bool):
        result = self.session.health(max_pending_s=max_pending_s, link_ok=link_ok)
        return replace(result, healthy=False) if self._failed else result
