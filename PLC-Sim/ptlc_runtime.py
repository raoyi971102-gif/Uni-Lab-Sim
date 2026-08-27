"""PTLC 握手代理共享的 OPC 适配器、状态对象和故障注入模型。"""

from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

STATIONS = (
    "Sampling", "Collect", "Develop", "PhotoScrape",
    "FeedLift", "Pump", "Rail", "StagingA",
)
INPUT_FIELDS = ("ActionCode", "RequestSeq", "Start", "Reset")
OUTPUT_DEFAULTS: dict[str, Any] = {
    "State": 0,
    "ActiveCode": 0,
    "AcceptedSeq": 0,
    "CompletedSeq": 0,
    "Step": 0,
    "ErrorCode": 0,
    "SafeState": 0,
    "Retryable": False,
}
TERMINAL_STATES = {20, 30, 40, 50}
MODELED_ACTIONS: dict[str, frozenset[int]] = {
    "Sampling": frozenset({10, 20, 31, 32, 33, 40, 50, 55, 60, 61, 62}),
    "Collect": frozenset({10, 21, 22, 23, 24, 30, 41, 42, 43}),
    "Develop": frozenset({10, 20, 21, 22, 26, 31, 32, 50, 51}),
    "PhotoScrape": frozenset({10, 31, 32, 33, 34, 35, 36, 40, 41, 42, 43, 44, 51, 52}),
    "FeedLift": frozenset({10, 11, 12, 13, 21, 22, 91}),
    "Pump": frozenset({10, 20}),
    "Rail": frozenset({10}),
    "StagingA": frozenset({24, 25}),
}
INSTANT_ACTIONS = frozenset({
    ("Sampling", 32),
    ("Sampling", 33),
    ("Collect", 24),
    ("Develop", 51),
    ("PhotoScrape", 32),
    ("PhotoScrape", 36),
    ("PhotoScrape", 41),
    ("PhotoScrape", 52),
    ("Pump", 10),
    ("Pump", 20),
    ("StagingA", 24),
    ("StagingA", 25),
})


class VariableAdapter(Protocol):
    """定义状态机所需的最小变量读写端口。"""

    def read(self, name: str) -> Any:
        """读取变量；参数为 BrowseName，返回节点值。"""

        ...

    def write(self, name: str, value: Any) -> None:
        """写入变量；参数为 BrowseName 和值，返回无。"""

        ...


class OpcUaVariableAdapter:
    """按 PTLC GVL BrowseName 路径定位变量并保持远端 VariantType 写入。"""

    def __init__(
        self,
        url: str,
        browse_path: tuple[str, ...],
        username: str = "",
        password: str = "",
    ) -> None:
        """创建适配器；参数为端点、GVL 路径和可选凭据，暂不建立连接。"""

        self.url = url
        self.browse_path = browse_path
        self.username = username
        self.password = password
        self._client = self._new_client()
        self._nodes: dict[str, Any] = {}
        self._gvl: Any = None

    def _new_client(self) -> Any:
        """按当前连接参数创建一个尚未连接的 OPC UA 客户端。"""

        from opcua import Client

        client = Client(self.url, timeout=10)
        if self.username:
            client.set_user(self.username)
            client.set_password(self.password)
        return client

    def connect(self) -> None:
        """连接远端 OPC UA 服务；无参数和返回值。"""

        self._client.connect()

    def disconnect(self) -> None:
        """尽力断开远端连接；重复调用安全，无返回值。"""

        try:
            self._client.disconnect()
        except Exception:  # noqa: BLE001 - 三方客户端断开异常不应阻止进程退出。
            return

    def _reconnect(self) -> None:
        """重建连接并清除节点缓存；无参数和返回值。"""

        self.disconnect()
        self._client = self._new_client()
        self._client.connect()
        self._nodes.clear()
        self._gvl = None

    @staticmethod
    def _child(parent: Any, browse_name: str) -> Any:
        """按 BrowseName 查询直接子节点；找不到时抛出 ``KeyError``。"""

        for child in parent.get_children():
            if child.get_browse_name().Name == browse_name:
                return child
        raise KeyError(f"BrowseName 子节点不存在: {browse_name}")

    def _gvl_node(self) -> Any:
        """解析并缓存配置的 GVL 根节点。"""

        if self._gvl is None:
            node = self._client.get_objects_node()
            for part in self.browse_path:
                node = self._child(node, part)
            self._gvl = node
        return self._gvl

    def _node(self, name: str) -> Any:
        """按变量名解析并缓存节点；参数为 BrowseName，返回 OPC UA 节点。"""

        if name not in self._nodes:
            self._nodes[name] = self._child(self._gvl_node(), name)
        return self._nodes[name]

    def _io(self, operation: Any) -> Any:
        """执行可重连 I/O；参数为零参数调用，返回其结果。"""

        for attempt in range(3):
            try:
                return operation()
            except (TimeoutError, ConnectionError, OSError):
                if attempt == 2:
                    raise
                time.sleep(0.5)
                self._reconnect()
        raise AssertionError("unreachable")

    def read(self, name: str) -> Any:
        """读取变量；参数为 BrowseName，返回保留原类型的节点值。"""

        return self._io(lambda: self._node(name).get_value())

    def write(self, name: str, value: Any) -> None:
        """写入变量；参数为 BrowseName 和新值，返回无。"""

        self._io(lambda: self._write_once(name, value))

    def _write_once(self, name: str, value: Any) -> None:
        """使用远端节点的 VariantType 执行一次写入。"""

        from opcua import ua

        node = self._node(name)
        variant_type = node.get_data_type_as_variant_type()
        node.set_value(ua.Variant(value, variant_type))


@dataclass(frozen=True)
class HandshakeEvent:
    """记录一个可对外观察的 L2 握手阶段事件。"""

    station: str
    phase: str
    action_code: int
    request_seq: int


@dataclass(frozen=True)
class MotionSegment:
    """记录一段轴运动；``starts_after`` 是相对受理时刻的偏移秒数。"""

    actual_name: str
    start: float
    target: float
    starts_after: float
    duration: float


@dataclass
class ActionCycle:
    """保存一个已受理原子动作的可恢复运行状态。"""

    action_code: int
    request_seq: int
    started_at: float
    due_at: float
    outcome: str
    error_code: int = 0
    safe_state: int = 10
    retryable: bool = False
    steps: tuple[int, ...] = ()
    last_step_index: int = -1
    motion: tuple[MotionSegment, ...] = ()
    plant_action: Any = None


@dataclass
class DeployCycle:
    """保存 PLC 下载安全态准备周期的请求身份和起始时刻。"""

    request_seq: int
    preparing_since: float


class RuntimeFaults:
    """维护可在运行期替换的确定性故障表。"""

    VALID_OUTCOMES = frozenset({"done", "reject", "error", "hang", "interrupt"})

    def __init__(self) -> None:
        """创建空故障表；无参数和返回值。"""

        self._items: dict[tuple[str, int], str] = {}

    def set(self, station: str, action_code: int, outcome: str) -> None:
        """设置工位动作故障；参数为工位、动作码和结果，非法结果抛错。"""

        normalized = str(outcome).strip().lower()
        if normalized not in self.VALID_OUTCOMES:
            raise ValueError(f"未知故障结果: {outcome}")
        self._items[(station, int(action_code))] = normalized

    def clear(self, station: str | None = None, action_code: int | None = None) -> None:
        """按可选工位和动作码清除故障；两者均空时清空全部。"""

        if station is None and action_code is None:
            self._items.clear()
            return
        for key in list(self._items):
            if (station is None or key[0] == station) and (
                action_code is None or key[1] == int(action_code)
            ):
                self._items.pop(key, None)

    def outcome(self, station: str, action_code: int) -> str | None:
        """查询工位动作故障结果；未配置时返回空。"""

        return self._items.get((station, int(action_code)))

    def load_payload(self, payload: Mapping[str, Any]) -> None:
        """用 JSON 同构映射替换故障表；非法工位被忽略。"""

        self._items.clear()
        for station, by_code in payload.items():
            if station not in STATIONS or not isinstance(by_code, Mapping):
                continue
            for code, outcome in by_code.items():
                self.set(station, int(code), str(outcome))

    def snapshot(self) -> dict[str, dict[str, str]]:
        """返回按工位分组、可 JSON 序列化的故障快照。"""

        result: dict[str, dict[str, str]] = {}
        for (station, code), outcome in sorted(self._items.items()):
            result.setdefault(station, {})[str(code)] = outcome
        return result
