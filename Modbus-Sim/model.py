"""Convert the repository configuration into PyModbus simulator devices."""

from __future__ import annotations

from pymodbus.simulator import DataType, SimData, SimDevice

from .config import AppConfig, DataAreaSpec, DeviceSpec


def build_sim_devices(config: AppConfig) -> list[SimDevice]:
    """Build one PyModbus device per configured unit id."""
    return [build_sim_device(device) for device in config.devices]


def build_sim_device(device: DeviceSpec) -> SimDevice:
    """Build four independent data blocks with standard Modbus permissions."""
    return SimDevice(
        id=device.unit_id,
        simdata=(
            [_area_data(device.areas["coils"], "coils", readonly=False)],
            [_area_data(device.areas["discrete_inputs"], "discrete_inputs", readonly=True)],
            [_area_data(device.areas["holding_registers"], "holding_registers", readonly=False)],
            [_area_data(device.areas["input_registers"], "input_registers", readonly=True)],
        ),
    )


def _area_data(area: DataAreaSpec, area_name: str, *, readonly: bool) -> SimData:
    datatype = DataType.BITS if area_name in {"coils", "discrete_inputs"} else DataType.REGISTERS
    return SimData(
        address=0,
        values=area.values(area_name),
        datatype=datatype,
        readonly=readonly,
    )
