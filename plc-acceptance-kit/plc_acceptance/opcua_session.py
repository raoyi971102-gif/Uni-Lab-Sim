"""通过正式 OPC UA 接口执行读写、等待和证据采集。"""

from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from typing import Any

from opcua import Client, ua

from .models import CatalogNode, NodeContract, TimelineEvent

VARIANT_TYPES = {
    "BOOLEAN": ua.VariantType.Boolean,
    "INT16": ua.VariantType.Int16,
    "INT32": ua.VariantType.Int32,
    "FLOAT": ua.VariantType.Float,
    "STRING": ua.VariantType.String,
}


def utc_now() -> str:
    """返回带时区的 UTC ISO-8601 时间戳。"""

    return datetime.now(timezone.utc).isoformat()


class OpcUaSession:
    """封装验收运行所需的最小 OPC UA 接口与时间线。"""

    def __init__(
        self,
        endpoint: str,
        contracts: dict[str, NodeContract],
        catalog: dict[str, CatalogNode],
        *,
        namespace_uri: str,
        timeout_seconds: float,
        poll_interval_seconds: float,
    ) -> None:
        """创建尚未连接的验收会话。

        参数：``endpoint`` 是正式服务地址；``contracts`` 是逻辑变量契约；
        ``catalog`` 提供发现的 NodeId；``namespace_uri`` 是运行时命名空间身份；
        两个秒值控制连接和轮询时限。
        返回：无；初始化当前对象。
        """

        self.endpoint = endpoint
        self.contracts = contracts
        self.catalog = catalog
        self.namespace_uri = namespace_uri
        self.timeout_seconds = timeout_seconds
        self.poll_interval_seconds = poll_interval_seconds
        self.client = Client(endpoint, timeout=timeout_seconds)
        self.timeline: list[TimelineEvent] = []
        self._started = time.monotonic()
        self._namespace_index: int | None = None

    def _effective_node_id(self, logical_id: str) -> str:
        """用运行时发现的 Namespace Index 重写静态 NodeId。

        参数：``logical_id`` 是协议中的稳定逻辑变量。
        返回：保留标识符、替换 Namespace Index 后的运行时 NodeId。
        异常：连接前调用时抛出 ``RuntimeError``。
        """

        if self._namespace_index is None:
            raise RuntimeError("OPC UA Namespace 尚未发现")
        contract = self.contracts[logical_id]
        static_node_id = self.catalog[contract.name].node_id
        if not re.match(r"^ns=\d+;", static_node_id):
            return static_node_id
        return re.sub(
            r"^ns=\d+;",
            f"ns={self._namespace_index};",
            static_node_id,
            count=1,
        )

    def _record(
        self, operation: str, logical_id: str, value: Any, detail: str = ""
    ) -> None:
        """把一次 OPC UA 操作追加到不可变顺序时间线。

        参数：``operation`` 是操作类型，``logical_id`` 是逻辑变量，
        ``value`` 是观察值，``detail`` 是补充诊断。
        返回：无。
        """

        contract = self.contracts[logical_id]
        self.timeline.append(
            TimelineEvent(
                timestamp=utc_now(),
                elapsed_ms=(time.monotonic() - self._started) * 1000,
                operation=operation,
                logical_id=logical_id,
                node_name=contract.name,
                node_id=self._effective_node_id(logical_id),
                value=value,
                detail=detail,
            )
        )

    def connect(self) -> None:
        """连接 OPC UA Server，并把连接事实记录到时间线。

        返回：无。
        """

        self.client.connect()
        namespace_array = self.client.get_namespace_array()
        if self.namespace_uri not in namespace_array:
            self.client.disconnect()
            raise ValueError(
                f"Server 未发布 Namespace URI {self.namespace_uri!r}: {namespace_array}"
            )
        self._namespace_index = namespace_array.index(self.namespace_uri)
        self.timeline.append(
            TimelineEvent(
                timestamp=utc_now(),
                elapsed_ms=(time.monotonic() - self._started) * 1000,
                operation="connect",
                logical_id="",
                node_name="",
                node_id="",
                value={
                    "endpoint": self.endpoint,
                    "namespace_uri": self.namespace_uri,
                    "namespace_index": self._namespace_index,
                },
            )
        )

    def disconnect(self) -> None:
        """关闭 OPC UA 会话；关闭失败不掩盖既有测试结果。

        返回：无。
        """

        try:
            self.client.disconnect()
        finally:
            self.timeline.append(
                TimelineEvent(
                    timestamp=utc_now(),
                    elapsed_ms=(time.monotonic() - self._started) * 1000,
                    operation="disconnect",
                    logical_id="",
                    node_name="",
                    node_id="",
                    value=self.endpoint,
                )
            )

    def _node(self, logical_id: str) -> Any:
        """解析逻辑变量对应的 OPC UA 节点。

        参数：``logical_id`` 是协议中的稳定逻辑 ID。
        返回：python-opcua ``Node``。
        """

        contract = self.contracts[logical_id]
        return self.client.get_node(self._effective_node_id(contract.logical_id))

    def read(self, logical_id: str) -> Any:
        """读取一个逻辑变量并记录证据。

        参数：``logical_id`` 是协议逻辑 ID。
        返回：服务端当前值。
        """

        value = self._node(logical_id).get_value()
        self._record("read", logical_id, value)
        return value

    def write(self, logical_id: str, value: Any) -> None:
        """按声明类型写入主机所有的逻辑变量。

        参数：``logical_id`` 是协议逻辑 ID，``value`` 是测试刺激值。
        返回：无。
        异常：尝试写 PLC 所有变量时抛出 ``PermissionError``。
        """

        contract = self.contracts[logical_id]
        if contract.owner != "host":
            raise PermissionError(f"禁止写入 PLC 所有变量: {logical_id}")
        variant_type = VARIANT_TYPES[contract.data_type]
        self._node(logical_id).set_value(ua.Variant(value, variant_type))
        self._record("write", logical_id, value)

    def assert_equal(self, logical_id: str, expected: Any) -> None:
        """断言一个逻辑变量等于期望值。

        参数：``logical_id`` 是协议逻辑 ID，``expected`` 是期望值。
        返回：无；不相等时抛出 ``AssertionError``。
        """

        actual = self.read(logical_id)
        if actual != expected:
            raise AssertionError(f"{logical_id} 期望 {expected!r}，实际 {actual!r}")

    def assert_greater(self, logical_id: str, minimum: float) -> None:
        """断言数值型逻辑变量大于给定下界。

        参数：``logical_id`` 是协议逻辑 ID；``minimum`` 是不包含在内的下界。
        返回：无；值不可转成数值或未越过下界时抛出异常。
        """

        actual = self.read(logical_id)
        try:
            numeric_value = float(actual)
        except (TypeError, ValueError) as exc:
            raise TypeError(f"{logical_id} 不是可比较数值: {actual!r}") from exc
        if numeric_value <= float(minimum):
            raise AssertionError(f"{logical_id} 期望大于 {minimum!r}，实际 {actual!r}")

    def wait_equal(self, logical_id: str, expected: Any, timeout_ms: int) -> None:
        """轮询直到变量等于期望值或超时。

        参数：``logical_id`` 是逻辑变量，``expected`` 是目标值，
        ``timeout_ms`` 是本步骤最长等待时间。
        返回：无；超时抛出 ``TimeoutError``。
        """

        deadline = time.monotonic() + timeout_ms / 1000
        last_value: Any = None
        while time.monotonic() <= deadline:
            last_value = self.read(logical_id)
            if last_value == expected:
                return
            time.sleep(self.poll_interval_seconds)
        raise TimeoutError(
            f"等待 {logical_id} == {expected!r} 超时，最后值 {last_value!r}"
        )

    def check_access(self, logical_id: str, *, enforce_write_owner: bool) -> list[str]:
        """读取服务端类型和访问级别，返回不一致诊断。

        参数：``logical_id`` 是逻辑变量；``enforce_write_owner`` 决定是否要求
        PLC 输出节点对客户端不可写。
        返回：该节点的错误消息列表。
        """

        errors: list[str] = []
        contract = self.contracts[logical_id]
        node = self._node(logical_id)
        actual_type = node.get_data_type_as_variant_type()
        expected_type = VARIANT_TYPES[contract.data_type]
        if actual_type != expected_type:
            errors.append(
                f"{logical_id} 服务端类型 {actual_type}，期望 {expected_type}"
            )
        access = node.get_user_access_level()
        if ua.AccessLevel.CurrentRead not in access:
            errors.append(f"{logical_id} 当前身份不可读")
        writable = ua.AccessLevel.CurrentWrite in access
        if contract.owner == "host" and not writable:
            errors.append(f"{logical_id} 属于上位机但当前身份不可写")
        if enforce_write_owner and contract.owner == "plc" and writable:
            errors.append(f"{logical_id} 属于 PLC 但当前身份仍可写")
        self._record("access", logical_id, sorted(item.name for item in access))
        return errors
