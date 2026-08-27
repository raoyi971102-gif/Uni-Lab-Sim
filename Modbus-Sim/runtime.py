"""Long-lived simulator state shared by the GUI API and protocol server."""

from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

from pymodbus.constants import ExcCodes

from .config import (
    AREA_FUNCTION_CODES,
    AREA_LABELS,
    AREA_NAMES,
    WRITABLE_AREAS,
    AppConfig,
    ConfigError,
    TransportMode,
    add_device,
    config_to_dict,
    dump_config,
    parse_config,
    remove_device,
    select_transport,
    update_device,
)
from .server import ServerPlan, build_server_plan, create_server


FUNCTION_LABELS = {
    1: "读线圈",
    2: "读离散输入",
    3: "读保持寄存器",
    4: "读输入寄存器",
    5: "写单个线圈",
    6: "写单个寄存器",
    15: "写多个线圈",
    16: "写多个寄存器",
    22: "掩码写寄存器",
    23: "读写多个寄存器",
    43: "读设备标识",
}


@dataclass(frozen=True)
class TrafficEntry:
    sequence: int
    timestamp: str
    direction: str
    transport: str
    unit_id: int | None
    function_code: int | None
    function_name: str
    address: int | None
    count: int | None
    data_hex: str
    error: bool


class SimulatorRuntime:
    """Own the selected configuration and at most one active protocol server."""

    def __init__(self, config: AppConfig) -> None:
        self._config = config
        self._server: Any | None = None
        self._plan: ServerPlan | None = None
        self._lock = asyncio.Lock()
        self._started_at: float | None = None
        self._last_error = ""
        self._connections = 0
        self._tx = 0
        self._rx = 0
        self._errors = 0
        self._sequence = 0
        self._traffic: deque[TrafficEntry] = deque(maxlen=500)

    @property
    def config(self) -> AppConfig:
        return self._config

    @property
    def running(self) -> bool:
        return self._server is not None

    def config_payload(self) -> dict[str, Any]:
        return config_to_dict(self._config)

    def config_yaml(self) -> str:
        return dump_config(self._config)

    async def replace_config(self, payload: object) -> None:
        parsed = parse_config(payload)
        async with self._lock:
            self._require_stopped("修改配置")
            self._config = parsed
            self._last_error = ""

    async def select_transport(self, mode: TransportMode | str) -> None:
        async with self._lock:
            self._require_stopped("切换传输方式")
            self._config = select_transport(self._config, mode)

    async def add_device(self, unit_id: int, name: str, sizes: dict[str, int] | None = None) -> None:
        async with self._lock:
            self._require_stopped("添加从站")
            self._config = add_device(self._config, unit_id, name, sizes)

    async def update_device(
        self,
        current_unit_id: int,
        unit_id: int,
        name: str,
        sizes: dict[str, int],
    ) -> None:
        async with self._lock:
            self._require_stopped("修改从站")
            self._config = update_device(self._config, current_unit_id, unit_id, name, sizes)

    async def remove_device(self, unit_id: int) -> None:
        async with self._lock:
            self._require_stopped("删除从站")
            self._config = remove_device(self._config, unit_id)

    async def update_point_definition(
        self,
        unit_id: int,
        area_name: str,
        address: int,
        payload: dict[str, Any],
    ) -> None:
        async with self._lock:
            self._require_stopped("修改寄存器定义")
            raw = config_to_dict(self._config)
            device = next((item for item in raw["devices"] if item["unit_id"] == unit_id), None)
            if device is None:
                raise ConfigError(f"配置中没有从站地址 {unit_id}")
            if area_name not in AREA_NAMES:
                raise ConfigError(f"未知数据区: {area_name}")
            area = device["areas"][area_name]
            if not 0 <= address < int(area["size"]):
                raise ConfigError(f"地址 {address} 超出 {AREA_LABELS[area_name]} 大小 {area['size']}")
            point = dict(area["points"].get(address, area["points"].get(str(address), {})))
            point.update(payload)
            area["points"][address] = point
            self._config = parse_config(raw)

    async def start(self, mode: TransportMode | str | None = None) -> dict[str, Any]:
        async with self._lock:
            if self._server is not None:
                raise ConfigError("仿真服务已经在运行")
            if mode is not None:
                self._config = select_transport(self._config, mode)
            plan = build_server_plan(self._config)
            self._last_error = ""
            self._connections = 0
            self._tx = self._rx = self._errors = 0
            self._sequence = 0
            self._traffic.clear()
            server = create_server(
                self._config,
                trace_packet=self._trace_packet,
                trace_connect=self._trace_connect,
            )
            try:
                await server.serve_forever(background=True)
            except Exception as exc:
                self._last_error = str(exc)
                try:
                    await server.shutdown()
                except Exception:
                    pass
                raise ConfigError(f"启动 {plan.mode.value} 服务失败: {exc}") from exc
            self._server = server
            self._plan = plan
            self._started_at = time.monotonic()
            return self.state()

    async def stop(self) -> dict[str, Any]:
        async with self._lock:
            if self._server is None:
                return self.state()
            server = self._server
            self._server = None
            self._plan = None
            self._started_at = None
            self._connections = 0
            try:
                await server.shutdown()
            except Exception as exc:
                self._last_error = str(exc)
                raise ConfigError(f"停止仿真服务失败: {exc}") from exc
            return self.state()

    def state(self) -> dict[str, Any]:
        plan = self._plan or build_server_plan(self._config)
        uptime = 0.0 if self._started_at is None else max(0.0, time.monotonic() - self._started_at)
        return {
            "running": self.running,
            "transport": self._config.active_transport.value,
            "endpoint": plan.endpoint,
            "uptime_seconds": round(uptime, 1),
            "connections": self._connections,
            "tx": self._tx,
            "rx": self._rx,
            "errors": self._errors,
            "last_error": self._last_error,
            "traffic_sequence": self._sequence,
        }

    async def register_rows(self, unit_id: int, area_name: str) -> dict[str, Any]:
        if area_name not in AREA_NAMES:
            raise ConfigError(f"未知数据区: {area_name}")
        device = self._config.device(unit_id)
        area = device.area(area_name)
        initial_values = area.values(area_name)
        live_values: list[bool] | list[int] = list(initial_values)
        if self._server is not None:
            values = await self._server.context.async_getValues(
                unit_id,
                AREA_FUNCTION_CODES[area_name],
                0,
                area.size,
            )
            if not isinstance(values, list):
                raise ConfigError(f"读取 {AREA_LABELS[area_name]} 失败: {values}")
            live_values = values
        points = area.point_map()
        rows = []
        for address in range(area.size):
            point = points.get(address)
            rows.append({
                "address": address,
                "plc_address": _plc_address(area_name, address),
                "alias": point.alias if point else "",
                "description": point.description if point else "",
                "format": point.display_format if point else ("bool" if area_name in {"coils", "discrete_inputs"} else "uint16"),
                "initial_value": initial_values[address],
                "live_value": live_values[address],
                "writable": area_name in WRITABLE_AREAS,
            })
        return {
            "unit_id": unit_id,
            "device_name": device.name,
            "area": area_name,
            "area_label": AREA_LABELS[area_name],
            "running": self.running,
            "rows": rows,
        }

    async def write_live_value(self, unit_id: int, area_name: str, address: int, value: object) -> None:
        async with self._lock:
            if self._server is None:
                raise ConfigError("服务未运行；请修改初值或先启动服务")
            if area_name not in WRITABLE_AREAS:
                raise ConfigError(f"{AREA_LABELS.get(area_name, area_name)} 对 Modbus 客户端只读")
            area = self._config.device(unit_id).area(area_name)
            if not 0 <= address < area.size:
                raise ConfigError(f"地址 {address} 超出数据区大小 {area.size}")
            if area_name == "coils":
                normalized = _coerce_bool(value)
                function_code = 5
            else:
                normalized = _coerce_register(value)
                function_code = 6
            result = await self._server.context.async_setValues(
                unit_id,
                function_code,
                address,
                [normalized],
            )
            if isinstance(result, ExcCodes):
                raise ConfigError(f"写入地址 {address} 失败: {result.name}")

    def traffic(self, after: int = 0) -> dict[str, Any]:
        entries = [asdict(item) for item in self._traffic if item.sequence > after]
        return {"sequence": self._sequence, "entries": entries}

    def clear_traffic(self) -> None:
        self._traffic.clear()

    def _require_stopped(self, action: str) -> None:
        if self._server is not None:
            raise ConfigError(f"服务运行期间不能{action}；请先停止服务")

    def _trace_connect(self, connected: bool) -> None:
        self._connections = max(0, self._connections + (1 if connected else -1))

    def _trace_packet(self, sending: bool, data: bytes) -> bytes:
        self._sequence += 1
        if sending:
            self._tx += 1
        else:
            self._rx += 1
        metadata = _packet_metadata(self._config.active_transport, data, sending=sending)
        error = bool(metadata["function_code"] is not None and metadata["function_code"] & 0x80)
        if error:
            self._errors += 1
        function_code = metadata["function_code"]
        base_code = None if function_code is None else function_code & 0x7F
        self._traffic.append(TrafficEntry(
            sequence=self._sequence,
            timestamp=datetime.now().astimezone().isoformat(timespec="milliseconds"),
            direction="Tx" if sending else "Rx",
            transport=self._config.active_transport.value,
            unit_id=metadata["unit_id"],
            function_code=function_code,
            function_name=FUNCTION_LABELS.get(base_code, "未知功能") if base_code is not None else "未解析",
            address=metadata["address"],
            count=metadata["count"],
            data_hex=data.hex(" ").upper(),
            error=error,
        ))
        return data


def _packet_metadata(mode: TransportMode, data: bytes, *, sending: bool) -> dict[str, int | None]:
    payload = data
    if mode is TransportMode.ASCII:
        try:
            text = data.strip().decode("ascii")
            payload = bytes.fromhex(text.removeprefix(":")[:-2])
        except (UnicodeDecodeError, ValueError):
            payload = b""
    elif mode is TransportMode.TCP:
        payload = data[6:] if len(data) >= 8 else b""
    if len(payload) < 2:
        return {"unit_id": None, "function_code": None, "address": None, "count": None}
    function_code = payload[1]
    base_code = function_code & 0x7F
    address = None
    count = None
    if not sending and base_code in {1, 2, 3, 4, 15, 16, 23} and len(payload) >= 6:
        address = int.from_bytes(payload[2:4], "big")
        count = int.from_bytes(payload[4:6], "big")
    elif base_code in {5, 6, 22} and len(payload) >= 4:
        address = int.from_bytes(payload[2:4], "big")
        count = 1
    elif sending and base_code in {15, 16} and len(payload) >= 6:
        address = int.from_bytes(payload[2:4], "big")
        count = int.from_bytes(payload[4:6], "big")
    elif sending and base_code in {1, 2, 3, 4, 23} and len(payload) >= 3:
        byte_count = payload[2]
        count = None if base_code in {1, 2} else byte_count // 2
    return {"unit_id": payload[0], "function_code": function_code, "address": address, "count": count}


def _plc_address(area_name: str, address: int) -> int:
    bases = {"coils": 1, "discrete_inputs": 10001, "input_registers": 30001, "holding_registers": 40001}
    return bases[area_name] + address


def _coerce_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, str)) and str(value).strip().lower() in {"0", "false", "off"}:
        return False
    if isinstance(value, (int, str)) and str(value).strip().lower() in {"1", "true", "on"}:
        return True
    raise ConfigError("线圈值必须是 true/false、on/off 或 1/0")


def _coerce_register(value: object) -> int:
    try:
        result = int(value, 0) if isinstance(value, str) else int(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError("寄存器值必须是整数") from exc
    if not -32768 <= result <= 65535:
        raise ConfigError("寄存器值必须在 -32768..65535 范围内")
    return result & 0xFFFF
