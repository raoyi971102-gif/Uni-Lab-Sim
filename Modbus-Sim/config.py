"""Typed configuration shared by the CLI, GUI, and protocol servers."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path
from typing import Any

import yaml


class ConfigError(ValueError):
    """Configuration is missing a required value or contains an invalid value."""


class TransportMode(str, Enum):
    """Supported wire transports."""

    TCP = "tcp"
    RTU_RS485 = "rtu-rs485"
    RTU_RS232 = "rtu-rs232"
    ASCII = "ascii"

    @classmethod
    def parse(cls, value: object, path: str = "transport") -> "TransportMode":
        if isinstance(value, cls):
            return value
        try:
            return cls(str(value))
        except ValueError as exc:
            choices = ", ".join(item.value for item in cls)
            raise ConfigError(f"{path} 必须是以下值之一: {choices}") from exc


AREA_NAMES = (
    "coils",
    "discrete_inputs",
    "holding_registers",
    "input_registers",
)

AREA_LABELS = {
    "coils": "线圈",
    "discrete_inputs": "离散输入",
    "holding_registers": "保持寄存器",
    "input_registers": "输入寄存器",
}

AREA_FUNCTION_CODES = {
    "coils": 1,
    "discrete_inputs": 2,
    "holding_registers": 3,
    "input_registers": 4,
}

WRITABLE_AREAS = frozenset({"coils", "holding_registers"})


@dataclass(frozen=True)
class TcpTransportSpec:
    host: str = "0.0.0.0"
    port: int = 5020


@dataclass(frozen=True)
class SerialTransportSpec:
    device: str
    baudrate: int = 9600
    bytesize: int = 8
    parity: str = "N"
    stopbits: int = 1
    timeout: float = 1.0
    handle_local_echo: bool = False
    reconnect_delay: float = 0.5


TransportSpec = TcpTransportSpec | SerialTransportSpec


@dataclass(frozen=True)
class PointSpec:
    address: int
    value: bool | int
    alias: str = ""
    description: str = ""
    display_format: str = "bool"


@dataclass(frozen=True)
class DataAreaSpec:
    size: int
    points: tuple[PointSpec, ...] = ()

    def point_map(self) -> dict[int, PointSpec]:
        return {point.address: point for point in self.points}

    def values(self, area_name: str) -> list[bool] | list[int]:
        default: bool | int = False if area_name in {"coils", "discrete_inputs"} else 0
        values = [default for _ in range(self.size)]
        for point in self.points:
            values[point.address] = point.value
        return values


@dataclass(frozen=True)
class DeviceSpec:
    unit_id: int
    name: str
    areas: dict[str, DataAreaSpec]

    def area(self, name: str) -> DataAreaSpec:
        try:
            return self.areas[name]
        except KeyError as exc:
            raise ConfigError(f"未知数据区: {name}") from exc


@dataclass(frozen=True)
class AppConfig:
    active_transport: TransportMode
    transports: dict[TransportMode, TransportSpec]
    devices: tuple[DeviceSpec, ...]

    def transport(self, mode: TransportMode | str | None = None) -> TransportSpec:
        selected = self.active_transport if mode is None else TransportMode.parse(mode)
        try:
            return self.transports[selected]
        except KeyError as exc:
            raise ConfigError(f"配置中没有传输方式 {selected.value}") from exc

    def device(self, unit_id: int) -> DeviceSpec:
        for device in self.devices:
            if device.unit_id == unit_id:
                return device
        raise ConfigError(f"配置中没有从站地址 {unit_id}")


def default_config_path() -> Path:
    configured = os.environ.get("MODBUSSIM_CONFIG")
    if configured:
        return Path(configured).expanduser().resolve()
    return Path(__file__).resolve().parent / "config" / "demo.yaml"


def load_config(path: str | Path | None = None) -> AppConfig:
    selected = Path(path) if path is not None else default_config_path()
    try:
        payload = yaml.safe_load(selected.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigError(f"无法读取配置文件 {selected}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"YAML 语法错误 ({selected}): {exc}") from exc
    return parse_config(payload)


def load_config_text(text: str) -> AppConfig:
    try:
        return parse_config(yaml.safe_load(text))
    except yaml.YAMLError as exc:
        raise ConfigError(f"YAML 语法错误: {exc}") from exc


def parse_config(payload: object) -> AppConfig:
    root = _mapping(payload, "配置根节点")
    version = _integer(root.get("version", 1), "version", minimum=1, maximum=1)
    if version != 1:  # pragma: no cover - maximum validation already catches this.
        raise ConfigError("仅支持配置版本 1")

    active = TransportMode.parse(root.get("active_transport", "tcp"), "active_transport")
    raw_transports = _mapping(root.get("transports"), "transports")
    transports: dict[TransportMode, TransportSpec] = {}
    for raw_name, raw_spec in raw_transports.items():
        mode = TransportMode.parse(raw_name, f"transports.{raw_name}")
        spec = _mapping(raw_spec, f"transports.{mode.value}")
        transports[mode] = _parse_transport(mode, spec)
    missing_transports = set(TransportMode) - set(transports)
    if missing_transports:
        missing = ", ".join(sorted(mode.value for mode in missing_transports))
        raise ConfigError(f"transports 缺少必需的传输配置: {missing}")
    if active not in transports:
        raise ConfigError(f"active_transport={active.value} 但 transports 中没有对应配置")

    raw_devices = root.get("devices")
    if not isinstance(raw_devices, list) or not raw_devices:
        raise ConfigError("devices 必须是非空列表")
    devices = tuple(_parse_device(item, index) for index, item in enumerate(raw_devices))
    ids = [device.unit_id for device in devices]
    if len(ids) != len(set(ids)):
        raise ConfigError("devices 中的 unit_id 不能重复")
    return AppConfig(active_transport=active, transports=transports, devices=devices)


def _parse_transport(mode: TransportMode, raw: Mapping[str, Any]) -> TransportSpec:
    if mode is TransportMode.TCP:
        return TcpTransportSpec(
            host=_text(raw.get("host", "0.0.0.0"), f"transports.{mode.value}.host"),
            port=_integer(raw.get("port", 5020), f"transports.{mode.value}.port", minimum=1, maximum=65535),
        )
    defaults = {TransportMode.ASCII: (7, "E"), TransportMode.RTU_RS485: (8, "N"), TransportMode.RTU_RS232: (8, "N")}
    default_bytesize, default_parity = defaults[mode]
    parity = _text(raw.get("parity", default_parity), f"transports.{mode.value}.parity").upper()
    if parity not in {"N", "E", "O"}:
        raise ConfigError(f"transports.{mode.value}.parity 必须是 N、E 或 O")
    return SerialTransportSpec(
        device=_text(raw.get("device"), f"transports.{mode.value}.device"),
        baudrate=_integer(raw.get("baudrate", 9600), f"transports.{mode.value}.baudrate", minimum=1),
        bytesize=_integer(raw.get("bytesize", default_bytesize), f"transports.{mode.value}.bytesize", minimum=7, maximum=8),
        parity=parity,
        stopbits=_integer(raw.get("stopbits", 1), f"transports.{mode.value}.stopbits", minimum=1, maximum=2),
        timeout=_number(raw.get("timeout", 1.0), f"transports.{mode.value}.timeout", minimum=0.01),
        handle_local_echo=_boolean(raw.get("handle_local_echo", False), f"transports.{mode.value}.handle_local_echo"),
        reconnect_delay=_number(raw.get("reconnect_delay", 0.5), f"transports.{mode.value}.reconnect_delay", minimum=0.0),
    )


def _parse_device(payload: object, index: int) -> DeviceSpec:
    path = f"devices[{index}]"
    raw = _mapping(payload, path)
    unit_id = _integer(raw.get("unit_id"), f"{path}.unit_id", minimum=1, maximum=247)
    name = _text(raw.get("name", f"Unit {unit_id}"), f"{path}.name")
    raw_areas = _mapping(raw.get("areas", {}), f"{path}.areas")
    areas = {
        area_name: _parse_area(raw_areas.get(area_name, {}), f"{path}.areas.{area_name}", area_name)
        for area_name in AREA_NAMES
    }
    return DeviceSpec(unit_id=unit_id, name=name, areas=areas)


def _parse_area(payload: object, path: str, area_name: str) -> DataAreaSpec:
    raw = _mapping(payload, path)
    size = _integer(raw.get("size", 16), f"{path}.size", minimum=1, maximum=65536)
    raw_points = _mapping(raw.get("points", {}), f"{path}.points")
    points: list[PointSpec] = []
    for raw_address, raw_point in raw_points.items():
        address = _integer(raw_address, f"{path}.points address", minimum=0, maximum=65535)
        if address >= size:
            raise ConfigError(f"{path}.points.{address} 超出数据区大小 {size}")
        point_path = f"{path}.points.{address}"
        spec = raw_point if isinstance(raw_point, Mapping) else {"value": raw_point}
        value = _point_value(spec.get("value", False if area_name in {"coils", "discrete_inputs"} else 0), point_path, area_name)
        default_format = "bool" if area_name in {"coils", "discrete_inputs"} else "uint16"
        display_format = _text(spec.get("format", default_format), f"{point_path}.format")
        allowed_formats = {"bool"} if default_format == "bool" else {"uint16", "int16", "hex", "binary"}
        if display_format not in allowed_formats:
            choices = ", ".join(sorted(allowed_formats))
            raise ConfigError(f"{point_path}.format 必须是以下值之一: {choices}")
        points.append(PointSpec(
            address=address,
            value=value,
            alias=str(spec.get("alias", "")).strip(),
            description=str(spec.get("description", "")).strip(),
            display_format=display_format,
        ))
    points.sort(key=lambda point: point.address)
    return DataAreaSpec(size=size, points=tuple(points))


def config_to_dict(config: AppConfig) -> dict[str, Any]:
    transports: dict[str, Any] = {}
    for mode, spec in config.transports.items():
        if isinstance(spec, TcpTransportSpec):
            transports[mode.value] = {"host": spec.host, "port": spec.port}
        else:
            transports[mode.value] = {
                "device": spec.device,
                "baudrate": spec.baudrate,
                "bytesize": spec.bytesize,
                "parity": spec.parity,
                "stopbits": spec.stopbits,
                "timeout": spec.timeout,
                "handle_local_echo": spec.handle_local_echo,
                "reconnect_delay": spec.reconnect_delay,
            }
    devices = []
    for device in config.devices:
        areas: dict[str, Any] = {}
        for area_name, area in device.areas.items():
            points = {}
            for point in area.points:
                points[point.address] = {
                    "alias": point.alias,
                    "value": point.value,
                    "format": point.display_format,
                    "description": point.description,
                }
            areas[area_name] = {"size": area.size, "points": points}
        devices.append({"unit_id": device.unit_id, "name": device.name, "areas": areas})
    return {
        "version": 1,
        "active_transport": config.active_transport.value,
        "transports": transports,
        "devices": devices,
    }


def dump_config(config: AppConfig) -> str:
    return yaml.safe_dump(config_to_dict(config), allow_unicode=True, sort_keys=False)


def select_transport(config: AppConfig, mode: TransportMode | str) -> AppConfig:
    selected = TransportMode.parse(mode)
    config.transport(selected)
    return replace(config, active_transport=selected)


def replace_transport(config: AppConfig, mode: TransportMode, spec: TransportSpec) -> AppConfig:
    transports = dict(config.transports)
    transports[mode] = spec
    return replace(config, transports=transports)


def replace_point(config: AppConfig, unit_id: int, area_name: str, point: PointSpec) -> AppConfig:
    if area_name not in AREA_NAMES:
        raise ConfigError(f"未知数据区: {area_name}")
    devices: list[DeviceSpec] = []
    for device in config.devices:
        if device.unit_id != unit_id:
            devices.append(device)
            continue
        area = device.area(area_name)
        if not 0 <= point.address < area.size:
            raise ConfigError(f"地址 {point.address} 超出 {AREA_LABELS[area_name]} 大小 {area.size}")
        points = area.point_map()
        points[point.address] = point
        areas = dict(device.areas)
        areas[area_name] = replace(area, points=tuple(sorted(points.values(), key=lambda item: item.address)))
        devices.append(replace(device, areas=areas))
    if not any(device.unit_id == unit_id for device in devices):
        raise ConfigError(f"配置中没有从站地址 {unit_id}")
    return replace(config, devices=tuple(devices))


def add_device(
    config: AppConfig,
    unit_id: int,
    name: str,
    sizes: Mapping[str, object] | None = None,
) -> AppConfig:
    if not 1 <= unit_id <= 247:
        raise ConfigError("unit_id 必须在 1..247 范围内")
    if any(device.unit_id == unit_id for device in config.devices):
        raise ConfigError(f"从站地址 {unit_id} 已存在")
    requested_sizes = sizes or {}
    empty_areas = {
        area_name: DataAreaSpec(size=_integer(requested_sizes.get(area_name, 16), f"sizes.{area_name}", minimum=1, maximum=65536))
        for area_name in AREA_NAMES
    }
    device = DeviceSpec(unit_id=unit_id, name=name.strip() or f"Unit {unit_id}", areas=empty_areas)
    return replace(config, devices=tuple(sorted((*config.devices, device), key=lambda item: item.unit_id)))


def update_device(
    config: AppConfig,
    current_unit_id: int,
    unit_id: int,
    name: str,
    sizes: Mapping[str, object],
) -> AppConfig:
    if not 1 <= unit_id <= 247:
        raise ConfigError("unit_id 必须在 1..247 范围内")
    if unit_id != current_unit_id and any(device.unit_id == unit_id for device in config.devices):
        raise ConfigError(f"从站地址 {unit_id} 已存在")
    found = False
    devices: list[DeviceSpec] = []
    for device in config.devices:
        if device.unit_id != current_unit_id:
            devices.append(device)
            continue
        found = True
        areas: dict[str, DataAreaSpec] = {}
        for area_name in AREA_NAMES:
            area = device.area(area_name)
            size = _integer(sizes.get(area_name, area.size), f"sizes.{area_name}", minimum=1, maximum=65536)
            highest_point = max((point.address for point in area.points), default=-1)
            if size <= highest_point:
                raise ConfigError(
                    f"{AREA_LABELS[area_name]} 仍有地址 {highest_point} 的点位；大小不能缩到 {size}"
                )
            areas[area_name] = replace(area, size=size)
        devices.append(replace(device, unit_id=unit_id, name=name.strip() or f"Unit {unit_id}", areas=areas))
    if not found:
        raise ConfigError(f"配置中没有从站地址 {current_unit_id}")
    return replace(config, devices=tuple(sorted(devices, key=lambda item: item.unit_id)))


def remove_device(config: AppConfig, unit_id: int) -> AppConfig:
    devices = tuple(device for device in config.devices if device.unit_id != unit_id)
    if len(devices) == len(config.devices):
        raise ConfigError(f"配置中没有从站地址 {unit_id}")
    if not devices:
        raise ConfigError("至少保留一个从站设备")
    return replace(config, devices=devices)


def _mapping(value: object, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ConfigError(f"{path} 必须是映射对象")
    return value


def _text(value: object, path: str) -> str:
    if value is None or not str(value).strip():
        raise ConfigError(f"{path} 不能为空")
    return str(value).strip()


def _integer(value: object, path: str, *, minimum: int, maximum: int | None = None) -> int:
    if isinstance(value, bool):
        raise ConfigError(f"{path} 必须是整数")
    try:
        result = int(value, 0) if isinstance(value, str) else int(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{path} 必须是整数") from exc
    if result < minimum or (maximum is not None and result > maximum):
        bound = f"{minimum}..{maximum}" if maximum is not None else f">= {minimum}"
        raise ConfigError(f"{path} 必须在 {bound} 范围内")
    return result


def _number(value: object, path: str, *, minimum: float) -> float:
    if isinstance(value, bool):
        raise ConfigError(f"{path} 必须是数值")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{path} 必须是数值") from exc
    if result < minimum:
        raise ConfigError(f"{path} 必须 >= {minimum}")
    return result


def _boolean(value: object, path: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.lower() in {"true", "false"}:
        return value.lower() == "true"
    raise ConfigError(f"{path} 必须是 true 或 false")


def _point_value(value: object, path: str, area_name: str) -> bool | int:
    if area_name in {"coils", "discrete_inputs"}:
        return _boolean(value, f"{path}.value")
    result = _integer(value, f"{path}.value", minimum=-32768, maximum=65535)
    return result & 0xFFFF
