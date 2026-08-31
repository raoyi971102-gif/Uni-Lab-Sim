"""CSV import/export for the Modbus device and register address model."""

from __future__ import annotations

import csv
import io
from collections.abc import Iterable
from dataclasses import replace

from .config import AREA_NAMES, AppConfig, ConfigError, PointSpec, parse_config

CSV_COLUMNS = (
    "unit_id",
    "device_name",
    "area",
    "area_size",
    "address",
    "alias",
    "value",
    "format",
    "description",
)
_BIT_AREAS = frozenset({"coils", "discrete_inputs"})


def decode_registers_csv(data: bytes) -> str:
    """Decode CSV produced by UTF-8 tools or common Chinese Excel installs."""
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ConfigError("CSV 文件必须使用 UTF-8 或 GB18030 编码")


def dump_registers_csv(config: AppConfig) -> str:
    """Export explicit points plus one size-preserving row for each empty area."""
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=CSV_COLUMNS, lineterminator="\n")
    writer.writeheader()
    for device in config.devices:
        for area_name in AREA_NAMES:
            area = device.area(area_name)
            points: Iterable[PointSpec | None] = area.points or (None,)
            for point in points:
                writer.writerow(
                    {
                        "unit_id": device.unit_id,
                        "device_name": device.name,
                        "area": area_name,
                        "area_size": area.size,
                        "address": "" if point is None else point.address,
                        "alias": "" if point is None else point.alias,
                        "value": "" if point is None else _csv_value(point.value),
                        "format": "" if point is None else point.display_format,
                        "description": "" if point is None else point.description,
                    }
                )
    return "\ufeff" + output.getvalue()


def replace_registers_from_csv(config: AppConfig, text: str) -> AppConfig:
    """Replace devices and register definitions while preserving transports."""
    rows = _read_rows(text)
    devices: dict[int, dict[str, object]] = {}
    seen_addresses: set[tuple[int, str, int]] = set()

    for line_number, row in rows:
        path = f"CSV 第 {line_number} 行"
        unit_id = _csv_integer(
            row["unit_id"], f"{path} unit_id", minimum=1, maximum=247
        )
        device_name = row["device_name"].strip() or f"Unit {unit_id}"
        area_name = row["area"].strip()
        if area_name not in AREA_NAMES:
            raise ConfigError(f"{path} area 必须是以下值之一: {', '.join(AREA_NAMES)}")
        area_size = _csv_integer(
            row["area_size"], f"{path} area_size", minimum=1, maximum=65536
        )

        device = devices.setdefault(
            unit_id, {"unit_id": unit_id, "name": device_name, "areas": {}}
        )
        if device["name"] != device_name:
            raise ConfigError(f"{path} Unit {unit_id} 的 device_name 与前面行不一致")
        areas = device["areas"]
        assert isinstance(areas, dict)
        area = areas.setdefault(area_name, {"size": area_size, "points": {}})
        if area["size"] != area_size:
            raise ConfigError(
                f"{path} Unit {unit_id} 的 {area_name} area_size 与前面行不一致"
            )

        address_text = row["address"].strip()
        point_fields = ("alias", "value", "format", "description")
        if not address_text:
            if any(row[field].strip() for field in point_fields):
                raise ConfigError(f"{path} address 留空时点位字段也必须留空")
            continue

        address = _csv_integer(
            address_text, f"{path} address", minimum=0, maximum=65535
        )
        if address >= area_size:
            raise ConfigError(f"{path} address {address} 超出数据区大小 {area_size}")
        address_key = (unit_id, area_name, address)
        if address_key in seen_addresses:
            raise ConfigError(
                f"{path} 地址重复: Unit {unit_id} / {area_name} / {address}"
            )
        seen_addresses.add(address_key)

        is_bit = area_name in _BIT_AREAS
        default_format = "bool" if is_bit else "uint16"
        raw_value = row["value"].strip()
        if is_bit:
            value: bool | int = _csv_boolean(raw_value or "false", f"{path} value")
        else:
            value = (
                _csv_integer(
                    raw_value or "0", f"{path} value", minimum=-32768, maximum=65535
                )
                & 0xFFFF
            )
        points = area["points"]
        assert isinstance(points, dict)
        points[address] = {
            "alias": row["alias"].strip(),
            "value": value,
            "format": row["format"].strip() or default_format,
            "description": row["description"].strip(),
        }

    if not devices:
        raise ConfigError("CSV 至少需要一个设备")
    for unit_id, device in devices.items():
        areas = device["areas"]
        assert isinstance(areas, dict)
        missing = [area_name for area_name in AREA_NAMES if area_name not in areas]
        if missing:
            raise ConfigError(f"Unit {unit_id} 缺少数据区行: {', '.join(missing)}")

    raw_devices = [devices[unit_id] for unit_id in sorted(devices)]
    parsed_devices = parse_config(
        {
            "version": 1,
            "active_transport": config.active_transport.value,
            "transports": _transport_payload(config),
            "devices": raw_devices,
        }
    ).devices
    return replace(config, devices=parsed_devices)


def _read_rows(text: str) -> list[tuple[int, dict[str, str]]]:
    normalized = text.removeprefix("\ufeff")
    try:
        reader = csv.DictReader(io.StringIO(normalized, newline=""))
        headers = tuple((name or "").strip() for name in (reader.fieldnames or ()))
        missing = [name for name in CSV_COLUMNS if name not in headers]
        unexpected = [name for name in headers if name and name not in CSV_COLUMNS]
        if missing:
            raise ConfigError(f"CSV 缺少列: {', '.join(missing)}")
        if unexpected:
            raise ConfigError(f"CSV 包含未知列: {', '.join(unexpected)}")
        rows: list[tuple[int, dict[str, str]]] = []
        for line_number, raw in enumerate(reader, start=2):
            if None in raw:
                raise ConfigError(f"CSV 第 {line_number} 行列数超过表头")
            row = {str(key).strip(): str(value or "") for key, value in raw.items()}
            if not any(value.strip() for value in row.values()):
                continue
            rows.append((line_number, row))
        return rows
    except csv.Error as exc:
        raise ConfigError(f"CSV 语法错误: {exc}") from exc


def _transport_payload(config: AppConfig) -> dict[str, object]:
    # Import here to keep CSV parsing independent from YAML serialization details.
    from .config import config_to_dict

    return config_to_dict(config)["transports"]


def _csv_integer(value: str, path: str, *, minimum: int, maximum: int) -> int:
    try:
        result = int(value, 0)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{path} 必须是整数") from exc
    if not minimum <= result <= maximum:
        raise ConfigError(f"{path} 必须在 {minimum}..{maximum} 范围内")
    return result


def _csv_boolean(value: str, path: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"true", "1", "on"}:
        return True
    if normalized in {"false", "0", "off"}:
        return False
    raise ConfigError(f"{path} 必须是 true/false、on/off 或 1/0")


def _csv_value(value: bool | int) -> str | int:
    if isinstance(value, bool):
        return "true" if value else "false"
    return value
