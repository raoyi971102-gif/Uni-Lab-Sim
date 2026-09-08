"""通过正式 HTTP 接口执行外部设备验收并采集时间线证据。"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urljoin

from .models import TimelineEvent


def _utc_now() -> str:
    """返回带时区的 UTC ISO-8601 时间戳。

    参数：无。
    返回：当前 UTC 时间字符串。
    """

    return datetime.now(timezone.utc).isoformat()


def _assert_json_contains(actual: Any, expected: Any, path: str = "response") -> None:
    """递归断言 HTTP JSON 响应包含声明的稳定字段。

    参数：``actual`` 是服务端响应；``expected`` 是用例声明的最小期望；
    ``path`` 是失败诊断中的字段路径。
    返回：无；字段缺失、类型或值不符时抛出 ``AssertionError`` 或 ``TypeError``。
    """

    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            raise TypeError(f"{path} 期望 JSON 对象，实际 {actual!r}")
        for key, value in expected.items():
            if key not in actual:
                raise AssertionError(f"{path} 缺少字段 {key!r}")
            _assert_json_contains(actual[key], value, f"{path}.{key}")
        return
    if actual != expected:
        raise AssertionError(f"{path} 期望 {expected!r}，实际 {actual!r}")


class HttpSession:
    """按环境声明的服务地址执行 HTTP 请求，不持有业务状态。"""

    def __init__(
        self,
        service_endpoints: dict[str, str],
        *,
        timeout_seconds: float,
    ) -> None:
        """创建 HTTP 验收会话。

        参数：``service_endpoints`` 是服务逻辑 ID 到根地址的映射；
        ``timeout_seconds`` 是单次请求超时。
        返回：无；初始化请求时间线。
        """

        self.service_endpoints = dict(service_endpoints)
        self.timeout_seconds = float(timeout_seconds)
        self.timeline: list[TimelineEvent] = []
        self._started = time.monotonic()

    def request(
        self,
        *,
        service: str,
        method: str,
        path: str,
        body: Any = None,
        expect_status: int = 200,
        expect_json: Any = None,
    ) -> Any:
        """调用一个已声明服务并验证状态码与最小 JSON 契约。

        参数：``service`` 是环境中的服务逻辑 ID；``method`` 与 ``path``
        标识公开 HTTP 操作；``body`` 是可选 JSON 请求体；``expect_status``
        和 ``expect_json`` 是独立配置的响应断言。
        返回：解码后的 JSON 响应；连接、状态码或断言失败时抛出异常。
        """

        base_url = self.service_endpoints.get(service, "").rstrip("/") + "/"
        if base_url == "/":
            raise RuntimeError(f"环境未配置 HTTP 服务端点: {service}")
        if not path.startswith("/"):
            raise ValueError(f"HTTP path 必须以 / 开头: {path}")
        normalized_method = method.upper()
        if normalized_method not in {"GET", "POST"}:
            raise ValueError(f"不支持的 HTTP 方法: {method}")
        url = urljoin(base_url, path.removeprefix("/"))
        encoded = None if body is None else json.dumps(body).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=encoded,
            method=normalized_method,
            headers={"content-type": "application/json"},
        )
        status = 0
        payload: Any = None
        try:
            with urllib.request.urlopen(
                request,
                timeout=self.timeout_seconds,
            ) as response:
                status = int(response.status)
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            status = int(exc.code)
            raw = exc.read().decode("utf-8", errors="replace")
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                payload = raw
        finally:
            self.timeline.append(
                TimelineEvent(
                    timestamp=_utc_now(),
                    elapsed_ms=(time.monotonic() - self._started) * 1000,
                    operation="http_request",
                    logical_id=service,
                    node_name=f"{normalized_method} {path}",
                    node_id=url,
                    value={"status": status, "body": payload},
                )
            )
        if status != expect_status:
            raise AssertionError(
                f"{normalized_method} {path} 期望 HTTP {expect_status}，实际 {status}"
            )
        if expect_json is not None:
            _assert_json_contains(payload, expect_json)
        return payload
