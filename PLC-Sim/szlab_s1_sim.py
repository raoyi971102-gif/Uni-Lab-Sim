"""SZLab S1 连续流工作站的隔离 HTTP stand-in Adapter。"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable, Mapping
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

EventSink = Callable[[str, str, Mapping[str, Any]], None]


class S1World:
    """保存 S1 HTTP 行为所需的最小确定性业务状态。"""

    def __init__(self) -> None:
        self.materials: list[dict[str, Any]] = []
        self.assigned_materials: list[Any] = []
        self.orders: list[dict[str, Any]] = []
        self.logs: list[dict[str, Any]] = []
        self.channels: dict[int, str] = {}
        self.wash_status = "idle"
        self.fill_status = "idle"
        self._next_material_id = 1
        self._next_order_id = 1

    @staticmethod
    def response(data: Any = None, description: str = "Succeed!") -> dict[str, Any]:
        return {"code": "0", "desc": description, "data": data}

    def dispatch(
        self,
        method: str,
        path: str,
        query: Mapping[str, list[str]],
        body: Any,
    ) -> tuple[str, dict[str, Any]]:
        """执行一个 S1 API 请求并返回 Action 名和兼容响应。"""

        route = path.removeprefix("/api/v1") or "/"
        if route == "/auth/login" and method == "POST":
            return "login", self.response({"token": "szlab-sim-token"})
        if route == "/material/search" and method == "GET":
            keyword = str((query.get("nameKey") or [""])[0]).lower()
            items = [item for item in self.materials if keyword in str(item.get("name", "")).lower()]
            return "sync_materials", self.response(
                {"records": items, "total": len(items)}
            )
        if route == "/material/add" and method == "POST":
            item = dict(body or {})
            item.setdefault("id", self._next_material_id)
            self._next_material_id = max(self._next_material_id, int(item["id"]) + 1)
            self.materials.append(item)
            return "create_material", self.response(item)
        if route == "/preparation/setInfo" and method == "POST":
            self.assigned_materials = list(body or [])
            return "set_materials", self.response(self.assigned_materials)
        if route == "/preparation/getCurrentInfo" and method == "GET":
            return "query_current_info", self.response(
                {"materials": list(self.assigned_materials)}
            )
        if route == "/experiment/add" and method == "POST":
            order = dict(body or {})
            order.setdefault("id", self._next_order_id)
            order.setdefault("status", "ready")
            self._next_order_id = max(self._next_order_id, int(order["id"]) + 1)
            self.orders.append(order)
            return "create_order", self.response(order)
        if route == "/experiment/start" and method == "POST":
            identifiers = {int(value) for value in (body or [])}
            for order in self.orders:
                if not identifiers or int(order["id"]) in identifiers:
                    order["status"] = "running"
                    self.channels[int(order.get("channel", 1))] = "running"
            return "scheduler_start", self.response(sorted(identifiers))
        if route == "/manualControl/stop" and method == "GET":
            channel = int((query.get("channel") or [1])[0])
            self.channels[channel] = "stopped"
            return "scheduler_stop", self.response({"channel": channel, "status": "stopped"})
        if route == "/experiment/getDEPhase" and method == "GET":
            identifier = int((query.get("id") or [0])[0])
            order = next((item for item in self.orders if int(item["id"]) == identifier), None)
            return "query_experiment_status", self.response(
                {"id": identifier, "phase": (order or {}).get("status", "unknown")}
            )
        if route in {"/experimentInformation/Allchannel", "/experimentInformation/channel"} and method == "GET":
            channel = int((query.get("channel") or [0])[0])
            data = (
                {str(key): value for key, value in sorted(self.channels.items())}
                if channel == 0
                else {"channel": channel, "status": self.channels.get(channel, "idle")}
            )
            return "query_realtime_status", self.response(data)
        if route in {"/experiment/listReady", "/experiment/listQueue", "/experiment/listDone"} and method == "GET":
            status = {
                "/experiment/listReady": "ready",
                "/experiment/listQueue": "running",
                "/experiment/listDone": "done",
            }[route]
            items = [order for order in self.orders if order.get("status") == status]
            return "list_orders", self.response({"records": items, "total": len(items)})
        if route == "/logHistory/find" and method == "POST":
            return "query_logs", self.response(list(self.logs))
        if route == "/wash/washStatus" and method == "GET":
            return "query_wash_status", self.response({"status": self.wash_status})
        if route == "/fill/status" and method == "GET":
            return "query_fill_status", self.response({"status": self.fill_status})
        if route == "/wash/oneClickWash" and method == "POST":
            self.wash_status = "completed"
            return "start_wash", self.response({"status": self.wash_status})
        if route == "/fill/start" and method == "POST":
            self.fill_status = "completed"
            return "start_fill", self.response({"status": self.fill_status})
        return "unknown", {
            "code": "404",
            "desc": f"S1 仿真端点不存在: {method} {route}",
            "data": None,
        }

    def snapshot(self) -> dict[str, Any]:
        """返回可持久化的 S1 状态快照。"""

        return {
            "materials": list(self.materials),
            "assigned_materials": list(self.assigned_materials),
            "orders": list(self.orders),
            "channels": dict(self.channels),
            "wash_status": self.wash_status,
            "fill_status": self.fill_status,
        }


class S1SimulationServer:
    """在后台线程串行提供 S1 HTTP API，避免动作状态互相覆盖。"""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 8055,
        *,
        event_sink: EventSink | None = None,
    ) -> None:
        self.host = str(host)
        self.port = int(port)
        self.world = S1World()
        self._event_sink = event_sink
        self._server: HTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._state_lock = threading.RLock()

    @property
    def endpoint(self) -> str:
        """返回驱动应使用的 S1 API 根地址。"""

        return f"http://{self.host}:{self.port}/api/v1"

    def start(self) -> None:
        """绑定端口并启动后台 HTTP 循环。"""

        if self._server is not None:
            return
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                self._handle("GET")

            def do_POST(self) -> None:
                self._handle("POST")

            def _handle(self, method: str) -> None:
                parsed = urlparse(self.path)
                length = int(self.headers.get("content-length", "0") or 0)
                raw_body = self.rfile.read(length) if length else b""
                try:
                    body = json.loads(raw_body.decode("utf-8")) if raw_body else None
                except json.JSONDecodeError:
                    self._send(400, {"code": "400", "desc": "请求 JSON 无效", "data": None})
                    return
                with owner._state_lock:
                    action, payload = owner.world.dispatch(
                        method, parsed.path, parse_qs(parsed.query), body
                    )
                    detail = {"method": method, "path": parsed.path}
                    if owner._event_sink is not None and action != "unknown":
                        owner._event_sink(action, "accepted", detail)
                        owner._event_sink(action, "completed", detail)
                        owner._event_sink(action, "reset", detail)
                    owner.world.logs.append(
                        {
                            "timestamp": time.time(),
                            "method": method,
                            "path": parsed.path,
                            "action": action,
                        }
                    )
                self._send(200 if payload.get("code") == "0" else 404, payload)

            def _send(self, status: int, payload: Mapping[str, Any]) -> None:
                encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("content-type", "application/json; charset=utf-8")
                self.send_header("content-length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

            def log_message(self, _format: str, *_args: Any) -> None:
                return

        self._server = HTTPServer((self.host, self.port), Handler)
        self.port = int(self._server.server_address[1])
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="szlab-s1-simulator",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        """停止 HTTP 循环并释放端口；重复调用安全。"""

        server = self._server
        if server is None:
            return
        server.shutdown()
        server.server_close()
        self._server = None
        thread = self._thread
        self._thread = None
        if thread is not None:
            thread.join(timeout=2.0)

    def snapshot(self) -> dict[str, Any]:
        """返回端点和 S1 业务状态。"""

        with self._state_lock:
            return {
                "endpoint": self.endpoint,
                "running": self._server is not None,
                **self.world.snapshot(),
            }
