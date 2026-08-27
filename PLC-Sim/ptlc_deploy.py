"""PTLC 仿真服务器的 PLC 下载安全态状态机。"""

from __future__ import annotations

from collections.abc import Sequence

try:
    from .ptlc_runtime import DeployCycle, VariableAdapter
except ImportError:
    from ptlc_runtime import DeployCycle, VariableAdapter


def _node(station: str, field: str) -> str:
    """拼接 L2 节点名；参数为工位与字段，返回 BrowseName。"""

    return f"{station}_L2_{field}"


def step_deploy(
    adapter: VariableAdapter,
    stations: Sequence[str],
    previous_start: bool,
    cycle: DeployCycle | None,
    now: float,
    prepare_ms: float,
) -> tuple[bool, DeployCycle | None]:
    """推进一次下载安全态扫描。

    参数为变量端口、工位、上一 Start、活动周期、时钟及准备延时。
    返回新的 Start 基线和活动周期；COMMITTED 后通信失败保持闭锁。
    """

    reset = bool(adapter.read("PLC_Deploy_Reset"))
    start = bool(adapter.read("PLC_Deploy_Start"))
    commit_seq = int(adapter.read("PLC_Deploy_CommitSeq"))
    state = int(adapter.read("PLC_Deploy_State"))
    if reset and not start and commit_seq == 0:
        adapter.write("PLC_Deploy_State", 0)
        adapter.write("PLC_Deploy_ErrorCode", 0)
        cycle = None
    elif start and not previous_start and state == 0:
        seq = int(adapter.read("PLC_Deploy_RequestSeq"))
        adapter.write("PLC_Deploy_AcceptedSeq", seq)
        l2_busy = any(
            int(adapter.read(_node(station, "State"))) == 10 for station in stations
        )
        if l2_busy:
            adapter.write("PLC_Deploy_State", 30)
            adapter.write("PLC_Deploy_ErrorCode", 1)
        elif not bool(adapter.read("PLC_Ready")):
            adapter.write("PLC_Deploy_State", 40)
            adapter.write("PLC_Deploy_ErrorCode", 5)
        else:
            adapter.write("PLC_Deploy_ErrorCode", 0)
            adapter.write("PLC_Deploy_State", 10)
            cycle = DeployCycle(seq, now)
    elif state == 10:
        comm = list(adapter.read("PLC_Axis_CommOperational"))
        if len(comm) != 11 or not all(bool(value) for value in comm):
            adapter.write("PLC_Deploy_State", 40)
            adapter.write("PLC_Deploy_ErrorCode", 5)
            cycle = None
        else:
            started = cycle.preparing_since if cycle else now
            if now - started >= max(float(prepare_ms), 0.0) / 1000.0 - 1e-9:
                adapter.write("PLC_Deploy_State", 20)
    elif state == 20 and start:
        accepted = int(adapter.read("PLC_Deploy_AcceptedSeq"))
        if commit_seq != 0 and commit_seq == accepted:
            adapter.write("PLC_Deploy_State", 25)
    elif state == 25:
        comm = list(adapter.read("PLC_Axis_CommOperational"))
        if len(comm) != 11 or not all(bool(value) for value in comm):
            adapter.write("PLC_Deploy_ErrorCode", 5)
    return start, cycle
