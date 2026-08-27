from dataclasses import replace

import pytest

from modbus_sim.config import (
    ConfigError,
    TransportMode,
    add_device,
    config_to_dict,
    dump_config,
    load_config,
    load_config_text,
    parse_config,
    remove_device,
    update_device,
)
from modbus_sim.cli import _load_with_overrides, build_parser


def test_default_config_covers_every_transport_and_shared_devices():
    config = load_config()

    assert config.active_transport is TransportMode.TCP
    assert set(config.transports) == set(TransportMode)
    assert [device.unit_id for device in config.devices] == [1, 2]
    assert config.device(1).area("holding_registers").values("holding_registers")[:4] == [1200, 850, 12, 0]


def test_yaml_round_trip_preserves_typed_configuration():
    config = load_config()

    assert load_config_text(dump_config(config)) == config
    assert parse_config(config_to_dict(config)) == config


def test_transport_mode_parser_accepts_enum_and_string():
    assert TransportMode.parse(TransportMode.ASCII) is TransportMode.ASCII
    assert TransportMode.parse("rtu-rs485") is TransportMode.RTU_RS485


def test_devices_can_be_added_and_removed_but_never_all_removed():
    config = load_config()
    changed = add_device(config, 10, "Drive")

    assert changed.device(10).name == "Drive"
    assert all(area.size == 16 for area in changed.device(10).areas.values())
    assert remove_device(changed, 10) == config

    one_device = replace(config, devices=(config.devices[0],))
    with pytest.raises(ConfigError, match="至少保留一个"):
        remove_device(one_device, 1)


def test_device_identity_and_area_sizes_can_be_edited_without_losing_points():
    config = update_device(
        load_config(),
        1,
        11,
        "Renamed PLC",
        {"coils": 24, "discrete_inputs": 20, "holding_registers": 64, "input_registers": 48},
    )

    assert config.device(11).name == "Renamed PLC"
    assert config.device(11).area("holding_registers").size == 64
    assert config.device(11).area("holding_registers").point_map()[3].alias == "Command_Word"
    with pytest.raises(ConfigError, match="仍有地址 3"):
        update_device(config, 11, 11, "Too small", {"holding_registers": 3})


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda raw: raw.update(active_transport="invalid"), "active_transport"),
        (lambda raw: raw["devices"].append(raw["devices"][0]), "unit_id 不能重复"),
        (lambda raw: raw["devices"][0].update(unit_id=248), "1..247"),
        (lambda raw: raw["transports"]["tcp"].update(port=0), "1..65535"),
        (lambda raw: raw["transports"].pop("ascii"), "缺少必需的传输配置"),
    ],
)
def test_invalid_config_is_rejected(mutation, message):
    raw = config_to_dict(load_config())
    mutation(raw)

    with pytest.raises(ConfigError, match=message):
        parse_config(raw)


def test_cli_overrides_are_revalidated():
    args = build_parser().parse_args(["serve", "--tcp-port", "0"])

    with pytest.raises(ConfigError, match="1..65535"):
        _load_with_overrides(args)
