"""PTLC 仿真配置中通用变量副作用的解析与执行。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

try:
    from .ptlc_runtime import VariableAdapter
except ImportError:
    from ptlc_runtime import VariableAdapter


def all_effects(config: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """收集配置中的工位级和动作级副作用；参数为配置，返回映射列表。"""

    result: list[Mapping[str, Any]] = []
    for effect in dict(config.get("station_effects", {})).values():
        if isinstance(effect, Mapping):
            result.append(effect)
    for by_action in dict(config.get("action_effects", {})).values():
        if isinstance(by_action, Mapping):
            result.extend(
                effect for effect in by_action.values() if isinstance(effect, Mapping)
            )
    return result


def effect_names(effect: Mapping[str, Any]) -> set[str]:
    """提取副作用引用的全部节点名；参数为副作用映射，返回名称集合。"""

    names = {str(name) for name in dict(effect.get("set", {}))}
    for item in effect.get("copy", ()) or ():
        names.update((str(item["from"]), str(item["to"])))
    for item in effect.get("indexed_copy", ()) or ():
        names.update((str(item["from"]), str(item["index"]), str(item["to"])))
    for item in effect.get("set_index", ()) or ():
        names.update((str(item["node"]), str(item["index"])))
    return names


def effects_for(
    config: Mapping[str, Any], station: str, code: int
) -> list[Mapping[str, Any]]:
    """返回动作完成时允许写入的显式副作用。

    Sampling 与 PhotoScrape 的轴位只由动作级运动计划更新，避免旧整站镜像让
    未参与动作的轴瞬移。参数为配置、工位和动作码，返回副作用列表。
    """

    result: list[Mapping[str, Any]] = []
    station_effect = dict(config.get("station_effects", {})).get(station)
    if station not in {"Sampling", "PhotoScrape"} and isinstance(
        station_effect, Mapping
    ):
        result.append(station_effect)
    action_effect = dict(dict(config.get("action_effects", {})).get(station, {})).get(
        str(code)
    )
    if isinstance(action_effect, Mapping):
        result.append(action_effect)
    return result


def fault_codes(config: Mapping[str, Any], station: str, key: str) -> set[int]:
    """合并全局与工位故障码；参数为配置、工位和类别，返回动作码集合。"""

    faults = dict(config.get("faults", {}))
    common = {int(value) for value in dict(faults.get("all", {})).get(key, ())}
    specific = {int(value) for value in dict(faults.get(station, {})).get(key, ())}
    return common | specific


def apply_effect(adapter: VariableAdapter, effect: Mapping[str, Any]) -> None:
    """对变量端口执行副作用；参数为适配器和映射，返回无。"""

    for name, value in dict(effect.get("set", {})).items():
        adapter.write(str(name), value)
    for item in effect.get("copy", ()) or ():
        adapter.write(str(item["to"]), adapter.read(str(item["from"])))
    for item in effect.get("indexed_copy", ()) or ():
        values = list(adapter.read(str(item["from"])))
        index = int(adapter.read(str(item["index"]))) - int(item.get("index_base", 0))
        if not 0 <= index < len(values):
            raise IndexError(f"{item['from']} 索引越界: {index}")
        adapter.write(str(item["to"]), values[index])
    for item in effect.get("set_index", ()) or ():
        name = str(item["node"])
        values = list(adapter.read(name))
        index = int(adapter.read(str(item["index"]))) - int(item.get("index_base", 0))
        if not 0 <= index < len(values):
            raise IndexError(f"{name} 索引越界: {index}")
        values[index] = item.get("value")
        adapter.write(name, values)


def apply_process_effects(
    adapter: VariableAdapter,
    process_state: dict[str, Any],
    station: str,
    code: int,
) -> None:
    """应用已建模动作的进程副作用；参数为端口、进程状态、工位和动作码。"""

    if station == "Develop" and code in {50, 51}:
        try:
            index = int(adapter.read("Expand_Target_Tank")) - 1
            states = list(adapter.read("Tank_State"))
            enables = list(adapter.read("Tank_Drain_Enable"))
            dones = list(adapter.read("Tank_Drain_Done"))
            if code == 50:
                states[index], enables[index], dones[index] = 98, False, True
            else:
                states[index], enables[index], dones[index] = 0, False, False
            adapter.write("Tank_State", states)
            adapter.write("Tank_Drain_Enable", enables)
            adapter.write("Tank_Drain_Done", dones)
        except (KeyError, IndexError, TypeError, ValueError):
            pass
    elif station == "Pump" and code in {10, 20}:
        value = code == 10
        process_state["vacuum_on"] = value
        try:
            adapter.write("Pump_Vacuum_On", value)
        except KeyError:
            pass
