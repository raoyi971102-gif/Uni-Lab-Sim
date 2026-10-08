"""解析量、速率上限与拒绝无效参数；不依赖设备模型。"""
import math

import pytest

from plc_sim.process_dynamics import exponential_increment


def test_unbounded_increment_matches_one_time_constant():
    assert exponential_increment(20, 10, 10) == pytest.approx(20 * (1 - math.exp(-1)))
    assert exponential_increment(-20, 10, 10) == pytest.approx(-20 * (1 - math.exp(-1)))


def test_rise_and_fall_limits_are_independent():
    assert exponential_increment(100, 2, 1, max_rise_per_s=3, max_fall_per_s=5) == 6
    assert exponential_increment(-100, 2, 1, max_rise_per_s=3, max_fall_per_s=5) == -10


def test_no_elapsed_time_or_zero_rate_preserves_value():
    assert exponential_increment(100, 0, 1) == 0
    assert exponential_increment(100, 10, 1, rate_factor=0) == 0


def test_two_unbounded_steps_match_combined_interval():
    remaining = 50.0
    for dt in [.25, .75]:
        remaining -= exponential_increment(remaining, dt, 2)
    assert remaining == pytest.approx(50 - exponential_increment(50, 1, 2))


@pytest.mark.parametrize("args,kwargs", [
    ((float("nan"), 1, 2), {}), ((1, -1, 2), {}), ((1, 1, 0), {}),
    ((1, 1, -2), {}), ((1, float("inf"), 2), {}), ((True, 1, 2), {}),
    ((1, 1, 2), {"rate_factor": 1.1}), ((1, 1, 2), {"rate_factor": -1}),
    ((1, 1, 2), {"max_rise_per_s": -1}), ((1, 1, 2), {"max_fall_per_s": float("inf")}),
])
def test_invalid_parameters_are_rejected(args, kwargs):
    with pytest.raises(ValueError):
        exponential_increment(*args, **kwargs)
