"""无设备参数、状态或时钟的一阶过程计算。"""
from __future__ import annotations

import math

class ModelError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _finite(value: object, name: str, minimum: float | None = None, maximum: float | None = None) -> float:
    if isinstance(value, bool):
        raise ModelError(f"invalid_{name}")
    try:
        result = float(value)
    except (ValueError, TypeError, OverflowError):
        raise ModelError(f"invalid_{name}") from None
    if not math.isfinite(result):
        raise ModelError(f"invalid_{name}")
    if minimum is not None and result < minimum:
        raise ModelError(f"invalid_{name}")
    if maximum is not None and result > maximum:
        raise ModelError(f"invalid_{name}")
    return result


def exponential_increment(
    difference: float,
    dt_s: float,
    time_constant_s: float,
    *,
    rate_factor: float = 1.0,
    max_rise_per_s: float | None = None,
    max_fall_per_s: float | None = None,
) -> float:
    """返回给定时间内的一阶变化量，可按单位时间的上下限裁剪。

    时间单位为秒，其余单位由调用者保持一致。参数不包含设备默认值；
    外部模型决定目标、故障、观测和状态写入，本函数不推进任何世界。
    """
    difference = _finite(difference, "difference")
    dt_s = _finite(dt_s, "dt_s", minimum=0)
    time_constant_s = _finite(time_constant_s, "time_constant_s")
    if time_constant_s <= 0:
        raise ValueError("时间常数必须大于零")
    rate_factor = _finite(rate_factor, "rate_factor", minimum=0, maximum=1)
    rise = None if max_rise_per_s is None else _finite(max_rise_per_s, "max_rise_per_s", minimum=0)
    fall = None if max_fall_per_s is None else _finite(max_fall_per_s, "max_fall_per_s", minimum=0)
    delta = difference * -math.expm1(-dt_s * rate_factor / time_constant_s)
    if fall is not None:
        delta = max(-fall * dt_s, delta)
    if rise is not None:
        delta = min(rise * dt_s, delta)
    return delta
