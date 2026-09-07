from __future__ import annotations

from xuse_handshake_agent import MemoryAdapter, XuseHandshakeSimulator


def _sim() -> tuple[MemoryAdapter, XuseHandshakeSimulator]:
    adapter = MemoryAdapter()
    simulator = XuseHandshakeSimulator(adapter, delay_ms=500)
    simulator.initialize()
    return adapter, simulator


def test_package_mode_lists_all_handshake_channels():
    _, simulator = _sim()
    snapshot = simulator.protocol_snapshot()
    assert snapshot["mode"] == "package"
    assert snapshot["channel_count"] >= 8
    bases = set(snapshot["channels"])
    assert any(name.startswith("机械臂") for name in bases)
    assert "开罐" in bases or any("开罐" in name for name in bases)
    assert any("加样" in name for name in bases)


def test_robot_action_rising_edge_completes_and_resets():
    adapter, simulator = _sim()
    assert adapter.read("机械臂空闲_1") is True
    adapter.write("机械臂目标位置代码_1", 1)
    adapter.write("机械臂目标取放代码_1", 3)
    adapter.write("机械臂动作触发_1", True)
    accepted = simulator.step(now=0.0)
    assert any(event.phase == "accepted" for event in accepted)
    completed = simulator.step(now=0.5)
    assert any(event.phase == "completed" for event in completed)
    assert adapter.read("机械臂动作完成_1") is True
    assert adapter.read("ROBOT_1_occupy[3]") is False
    adapter.write("机械臂动作触发_1", False)
    reset = simulator.step(now=0.6)
    assert any(event.phase == "reset" for event in reset)
    assert adapter.read("机械臂动作完成_1") is False


def test_process_and_init_channels():
    adapter, simulator = _sim()
    assert adapter.read("开罐请求加工") is True
    adapter.write("开罐动作控制代码", 1)
    adapter.write("开罐开始加工", True)
    simulator.step(now=0.0)
    simulator.step(now=1.6)
    assert adapter.read("开罐加工完成") is True
    assert adapter.read("开罐占位_上盖") is True

    adapter.write("工站初始化", True)
    simulator.step(now=2.0)
    simulator.step(now=2.7)
    assert adapter.read("工站初始化完成") is True
