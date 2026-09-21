"""防止仿真场景静默漂移的守护测试。

覆盖两件曾经把整条单样品链卡死的事：

- ``/api/state`` 必须上报正在运行的代理生效的 ``workflow``。不报它，运维就看不出
  当前 PLC 世界是按哪个场景种下的；从 GUI 重启代理会静默换成下拉框默认的 ``all``，
  把 S05/S06/S08/S09 种成「工位已占料」，需要放料进 S061 的单样品链随即被 PLC 拒单。
- ``Robot_任务允许写入`` 是仿真器代表 PLC 持有的输出，空闲态必须为 ``True``。
  被在线改写留成 ``False`` 时，Edge 永远拿不到运动许可，第一个机器人动作就超时。
"""

from __future__ import annotations

from typing import Any

import szlab_handshake_agent as handshake


class MemoryAdapter:
    """只保存被读写过的 PLC 变量，未初始化的读取按缺省零值处理。"""

    def __init__(self, values: dict[str, Any] | None = None) -> None:
        self.values: dict[str, Any] = dict(values or {})

    def read(self, name: str) -> Any:
        return self.values.get(name, 0)

    def write(self, name: str, value: Any) -> None:
        self.values[name] = value


def _simulator(adapter: MemoryAdapter, workflow: str) -> Any:
    return handshake.WorkflowHandshakeSimulator(
        adapter,
        workflow=workflow,
        package_mode=True,
    )


def test_single_sample_scenarios_seed_s06_empty_but_all_does_not() -> None:
    """单样品场景必须把 S06 加液位种成空位；``all`` 场景则相反。

    这条差异就是故障根因：``all`` 是给单工位调试用的世界，S06 起步就有烧杯，
    与「把烧杯放进 S061」的单样品链互斥。
    """

    single = _simulator(MemoryAdapter(), handshake.DUAL_TASK_ROBOT_ATOMIC_WORKFLOW)
    everything = _simulator(MemoryAdapter(), "all")

    assert single.initialization_values()[handshake.S06_BEAKER_SENSOR] is False
    assert everything.initialization_values()[handshake.S06_BEAKER_SENSOR] is True


def test_idle_robot_restores_externally_cleared_write_permission() -> None:
    """空闲态运动许可被外部置为 False 时，仿真器必须自愈并显式上报。"""

    adapter = MemoryAdapter(
        {
            handshake.ROBOT_WRITE_ALLOWED: False,
            handshake.ROBOT_WRITE_DONE: False,
            handshake.ROBOT_TASK_NUMBER: 0,
            handshake.ROBOT_HOME: True,
            handshake.ROBOT_TASK_COMPLETE: 0,
        }
    )
    simulator = _simulator(adapter, handshake.DUAL_TASK_ROBOT_ATOMIC_WORKFLOW)

    events = simulator._step_robot(0.0)

    assert adapter.read(handshake.ROBOT_WRITE_ALLOWED) is True
    assert simulator.restored_write_permissions == 1
    # 自愈不得伪造一次动作周期：设备包运行时只用 accepted/rejected 开启运行，
    # 凭空的其他阶段会让扫描抛错。
    assert events == []

    # 恢复后不得反复触发，否则日志被淹没。
    assert simulator._step_robot(0.0) == []
    assert simulator.restored_write_permissions == 1


def test_idle_robot_does_not_touch_permission_while_edge_is_writing_a_task() -> None:
    """Edge 正在写入任务时不得改动运动许可，避免和接单流程互相打断。"""

    adapter = MemoryAdapter(
        {
            handshake.ROBOT_WRITE_ALLOWED: False,
            handshake.ROBOT_WRITE_DONE: True,
            handshake.ROBOT_TASK_NUMBER: 0,
            handshake.ROBOT_HOME: True,
            handshake.ROBOT_TASK_COMPLETE: 0,
        }
    )
    simulator = _simulator(adapter, handshake.DUAL_TASK_ROBOT_ATOMIC_WORKFLOW)

    assert simulator._step_robot(0.0) == []
    assert adapter.read(handshake.ROBOT_WRITE_ALLOWED) is False
    assert simulator.restored_write_permissions == 0


def test_agent_state_snapshot_reports_the_running_scenario() -> None:
    """``/api/state`` 必须回报正在运行的代理场景，供运维和部署钩子复核。"""

    from gui.backend_state import AppState

    state = AppState()
    assert state.snapshot()["agent"]["workflow"] is None

    class _AliveProcess:
        pid = 4321

        @staticmethod
        def poll() -> None:
            return None

    state.agent_proc = _AliveProcess()
    state.agent_profile = "szlab"
    state.agent_options = {
        "workflow": handshake.DUAL_TASK_ROBOT_ATOMIC_WORKFLOW,
        "pump": 1,
    }

    agent = state.snapshot()["agent"]
    assert agent["running"] is True
    assert agent["workflow"] == handshake.DUAL_TASK_ROBOT_ATOMIC_WORKFLOW
    assert agent["options"]["pump"] == 1
