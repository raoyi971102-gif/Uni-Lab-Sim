#!/usr/bin/env python3
"""SZLab OPC UA 工作流握手代理。

用途：

1. ``list``：列出仓库中全部工作流的 PLC/配置先决条件，不连接服务器。
2. ``check``：只读检查远端 OPC UA 中可自动判定的先决条件。
3. ``serve``：写入测试先决条件，并监听 PC→PLC 信号，模拟 PLC 握手。

协议目录与 Uni-Lab-SZLab 当前工作流源码对齐，覆盖 ``workflows`` 目录中全部
18 个 Python 工作流、37 个唯一动作调用。状态机只依赖 :class:`VariableAdapter`
这一处 interface；OPC UA、内存测试替身等实现都作为 adapter 接入。
握手场景名称使用工作流源码中的真实函数名；旧版 S07/S09 场景名仍作为兼容别名：

- ``s07_material_dosing`` → ``s07_粉桶与烧杯搬运后固体称量``
- ``szlab_s09_pipetting_workflow`` → ``s09_移液调试``

主要动作包括：

- ``szlab_mixer_robot.submit_place_to_s04``（机器人任务号 7）
- ``szlab_mixer_stirrer.run_stirring``
- ``szlab_mixer_robot.submit_pick_from_s04``（机器人任务号 8）
- ``szlab_mixer_photoshotting.take_photo``（当前为只读完成信号）
- ``szlab_mixer_pump.run_solvent_addition``
- ``szlab_mixer_robot.submit_place_to_s06``（机器人任务号 11）
- ``szlab_mixer_robot.submit_pick_from_s06``（机器人任务号 12）
- ``szlab_mixer_robot.submit_place_to_s071``（机器人任务号 13）
- ``szlab_mixer_robot.submit_place_to_s072``（机器人任务号 15）
- ``szlab_mixer_robot.submit_pick_from_s072``（机器人任务号 16）
- ``szlab_s07_solid_addition.scan_powder_cartridges``（S07 工艺 1）
- ``szlab_s07_solid_addition.rotate_powder_cartridge_to_feed``（S07 工艺 2）
- ``szlab_s07_solid_addition.dose_powder``（S07 工艺 3）
- ``szlab_s08_cap_station.process_cap_with_sample_parts``（S08 工艺 1-6）
- ``szlab_mixer_pipetting_station.prepare_liquid_station``
- ``szlab_mixer_pipetting_station.bind_sample_to_station``
- ``szlab_mixer_pipetting_station.add_liquid``（内部工艺 5→7→8→6；完成只认 ``S09工艺完成``）
- ``szlab_mixer_pipetting_station.release_station``
- S09 工艺 9 测密度：按 ``S09测密度次数`` 写入抽/放液天平数组前 N 项；不再使用 ``S09天平读数稳定``
- ``szlab_poly_plc.get_stack_status``（只读，无动态握手）
- ``szlab_mixer_robot.pick_beaker_from_s03``（机器人任务号 6）
- ``szlab_mixer_robot.place_beaker_to_s06``（机器人任务号 11）
- ``szlab_mixer_pump.add_solvent_to_beaker``
- ``szlab_mixer_robot.pick_beaker_from_s06``（机器人任务号 12）
- ``szlab_mixer_robot.pick``（标准 Site 动作，S071/S03）
- ``szlab_s07_solid_addition.prepare_powder_cartridge_site``（S07 工艺 2）
- ``szlab_mixer_robot.place``（标准 Site 动作，S072）
- ``host_node.transfer_resource``（物理动作成功后的物料系统记账）
- ``szlab_s07_solid_addition.dose_powder_with_materials``（S07 工艺 3）

建议用 ``--workflow WORKFLOW_ID`` 定向运行单个工作流；选择
``s06_robot_workflow`` 时会让 S06
烧杯传感器从 False 开始，并由任务 11/12 的握手周期切换；选择
``s09_移液调试``（或兼容别名 ``szlab_s09_pipetting_workflow``）时会初始化
S09 工位和液体余量，并响应全部内部工艺。原有
``--s06-robot-workflow``、``--s09-pipetting-workflow`` 参数仍作为兼容别名保留。

本文件不依赖 Uni-Lab-OS 进程，也不创建 OPC UA 节点；它只连接由 CSV
创建好的节点。请使用包含 ``python-opcua`` 的 unilab Python 环境运行。
"""

from __future__ import annotations

import argparse
import json
import signal
import sys
import threading
import time
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol

try:
    from .common import load_yaml
    from .package_simulation import write_snapshot_atomic
    from .szlab_package_runtime import SzlabPackageRuntime, default_package_config_path
    from .szlab_s1_sim import S1SimulationServer
except ImportError:  # Direct ``python szlab_handshake_agent.py`` compatibility.
    from common import load_yaml
    from package_simulation import write_snapshot_atomic
    from szlab_package_runtime import SzlabPackageRuntime, default_package_config_path
    from szlab_s1_sim import S1SimulationServer

DEFAULT_URL = "opc.tcp://opcua.ideawit.com:4855/xuse_sim"
DEFAULT_NODE_PREFIX = "ns=4;s=上位机通讯|"

ROBOT_HOME = "Robot_Home"
ROBOT_WRITE_ALLOWED = "Robot_任务允许写入"
ROBOT_WRITE_DONE = "Robot_任务写入完成"
ROBOT_TASK_NUMBER = "任务号"
ROBOT_TASK_COMPLETE = "Robot_任务完成"
ROBOT_TOOL_PAYLOAD_SENSOR = "传感器状态_上位机[3].NO[6]"
S04_ROBOT_POSITION = "S04取放料编号"
S03_BEAKER_SENSOR = "传感器状态_上位机[0].NO[6]"
S03_SAMPLE_VIAL_SENSOR = "传感器状态_上位机[1].NO[8]"

S05_DONE = "S05加工完成"
S05_RESULT = "S05拍照结果"
S05_MATERIAL_SENSOR = "传感器状态_上位机[3].NO[0]"

S06_READY = "S06准备信号"
S06_ALLOW = "S06允许加工"
S06_PROCESS = "S06工艺选择"
S06_PARAMS_WRITTEN = "S06参数写入完成"
S06_DONE = "S06加工完成"
S06_BEAKER_SENSOR = "传感器状态_上位机[3].NO[1]"
S06_STORAGE_BOTTLE_SENSOR = {
    1: "传感器状态_上位机[4].NO[12]",
    2: "传感器状态_上位机[5].NO[1]",
}

S071_ROBOT_POSITION = "S071取放料编号"
S072_ROBOT_PRODUCT = "S072取放料产品"
S071_SENSOR_BY_SLOT = {
    1: "传感器状态_上位机[3].NO[8]",
    2: "传感器状态_上位机[3].NO[9]",
    3: "传感器状态_上位机[3].NO[10]",
    4: "传感器状态_上位机[3].NO[11]",
    5: "传感器状态_上位机[3].NO[12]",
    6: "传感器状态_上位机[3].NO[13]",
}
S072_SENSOR_BY_POSITION = {
    1: "传感器状态_上位机[3].NO[14]",
    2: "传感器状态_上位机[3].NO[15]",
}

S07_HOME = "S07原点信号"
S07_ALLOW = "S07允许加工"
S07_PROCESS = "S07工艺选择"
S07_PARAMS_WRITTEN = "S07参数写入完成"
S07_DONE = "S07工艺完成"
S07_BALANCE_READING = "S07天平读数"
S07_PROCESS_LABELS = {
    1: "粉罐扫码盘点",
    2: "替换粉罐旋转到进料位",
    3: "注粉",
}

S08_HOME = "S08原点信号"
S08_ALLOW = "S08允许加工"
S08_PROCESS = "S08工艺选择"
S08_PARAMS_WRITTEN = "S08参数写入完成"
S08_DONE = "S08工艺完成"
S08_CAP_STORAGE_SLOT = "S082瓶盖暂存位"
S08_STATION_STATUS = "工站状态[7]"
S08_ROBOT_PRODUCT = "S08取放料产品"
S08_ROBOT_POSITION = "S08取放料编号"
S08_POUR_PRODUCT = "S08倒料产品选择"
S08_CAP_STATION_SENSOR = {
    1: "传感器状态_上位机[3].NO[14]",
    2: "传感器状态_上位机[3].NO[15]",
}
S08_CAP_STORAGE_SENSOR = {
    slot: f"传感器状态_上位机[4].NO[{slot - 1}]" for slot in range(1, 6)
}
S08_PROCESS_LABELS = {
    1: "500 mL 样品瓶开盖",
    2: "500 mL 样品瓶关盖",
    3: "250 mL 样品瓶开盖",
    4: "250 mL 样品瓶关盖",
    5: "100 mL 液体瓶开盖",
    6: "100 mL 液体瓶关盖",
}

S09_PROCESS = "S09工艺选择"
S09_PARAMS_WRITTEN = "S09参数写入完成"
S09_DONE = "S09工艺完成"

# S09 Edge 驱动会把启动等待时已经存在的完成码视为上一轮残留：它先等
# 完成码回到 0，再等待本轮完成码。PLC-Sim 的动作很快，完成码可能早于
# Edge 进入等待而产生竞态。保持请求未清零时周期性重发 0 -> 完成码，既
# 保留完成码锁存语义，也让迟到的 Edge 等待者能观察到一条新的完成边沿。
S09_COMPLETION_HOLD_SECONDS = 0.5
S09_COMPLETION_REARM_SECONDS = 0.1
S09_ALLOW = "S09允许加工"
S09_STATION_STATUS = "工站状态[8]"
S09_TIP_BOX = "S09TIP盒工位编号"
S09_LIQUID_BOTTLE = "S09液体瓶编号"
S09_TRANSFER_PRODUCT = "S09取放料产品"
S09_TRANSFER_POSITION = "S09取放料编号"
# 旧 PLC 表可能仍有该节点名，但当前 0810/真机交互不再使用稳定位。
# 握手代理不得把它作为先决条件，也不得读写它。
S09_BALANCE_STABLE = "S09天平读数稳定"
S09_BALANCE_READING = "S09天平读数"
S09_DENSITY_COUNT = "S09测密度次数"
S09_ASPIRATE_BALANCE_READINGS = "S09抽液天平读数"
S09_DISPENSE_BALANCE_READINGS = "S09放液天平读数"
S09_TIP_BOX_SENSOR = {
    1: "传感器状态_上位机[4].NO[5]",
    2: "传感器状态_上位机[4].NO[6]",
}
S09_STATION_SENSOR = {
    position: f"传感器状态_上位机[4].NO[{position + 6}]" for position in range(1, 6)
}

S02_ROBOT_POSITION = "S02取放料编号"
S03_ROBOT_PRODUCT = "S03取放料产品"
S03_ROBOT_POSITION = "S03取放料编号"
S10_ROBOT_POSITION = "S10取放料编号"
S11_ROBOT_PRODUCT = "S11取放料产品"
S11_ROBOT_POSITION = "S11取放料编号"
S09_PROCESS_LABELS = {
    1: "去安全位1",
    2: "去安全位2",
    3: "去安全位3",
    4: "去安全位4",
    5: "取 TIP",
    6: "放 TIP",
    7: "液体瓶取液",
    8: "烧杯放液",
    9: "测密度抽排液",
}


def s09_remaining_volume(bottle: int) -> str:
    return f"S09液体瓶{int(bottle)}剩余液量"


def s09_density_balance_vars(base_name: str) -> list[str]:
    return [f"{base_name}[{index}]" for index in range(10)]


def clamp_s09_density_count(count: Any) -> int:
    try:
        value = int(count or 1)
    except (TypeError, ValueError):
        value = 1
    if value < 1:
        return 1
    if value > 10:
        return 10
    return value


def s08_cap_cache(slot: int, index: int) -> str:
    return f"S082_{int(slot)}数据缓存[{int(index)}]"


SUPPORTED_ACTIONS = (
    "szlab_mixer_robot.submit_place_to_s04",
    "szlab_mixer_stirrer.run_stirring",
    "szlab_mixer_robot.submit_pick_from_s04",
    "szlab_mixer_photoshotting.take_photo",
    "szlab_mixer_pump.run_solvent_addition",
    "szlab_mixer_robot.submit_place_to_s06",
    "szlab_mixer_robot.submit_pick_from_s06",
    "szlab_mixer_pipetting_station.prepare_liquid_station",
    "szlab_mixer_pipetting_station.bind_sample_to_station",
    "szlab_mixer_pipetting_station.add_liquid",
    "szlab_mixer_pipetting_station.release_station",
    "szlab_mixer_robot.submit_place_to_s071",
    "szlab_mixer_robot.submit_place_to_s072",
    "szlab_mixer_robot.submit_pick_from_s072",
    "szlab_s07_solid_addition.scan_powder_cartridges",
    "szlab_s07_solid_addition.rotate_powder_cartridge_to_feed",
    "szlab_s07_solid_addition.dose_powder",
    "szlab_s08_cap_station.process_cap_with_sample_parts",
    "szlab_poly_plc.get_stack_status",
    "szlab_mixer_robot.pick_beaker_from_s03",
    "szlab_mixer_robot.place_beaker_to_s06",
    "szlab_mixer_pump.add_solvent_to_beaker",
    "szlab_mixer_robot.pick_beaker_from_s06",
    "szlab_mixer_robot.pick",
    "szlab_s07_solid_addition.prepare_powder_cartridge_site",
    "szlab_mixer_robot.place",
    "host_node.transfer_resource",
    "szlab_s07_solid_addition.dose_powder_with_materials",
    "szlab_mixer_photoshotting.inspect_beaker",
    "szlab_mixer_pipetting_station.add_liquid_with_materials",
    "szlab_mixer_pump.add_solvent_with_materials",
    "szlab_mixer_robot.pick_beaker",
    "szlab_mixer_robot.pour_beaker_into_vial",
    "szlab_mixer_stirrer.stir_beaker",
    "szlab_s07_solid_addition.dose_powder_with_two_materials",
    "szlab_s08_cap_station.process_liquid_reagent_100ml_cap_with_material",
    "szlab_s08_cap_station.process_sample_vial_250ml_cap_with_material",
)

# 保留首版握手器导出的动作别名，避免既有测试脚本和外部调用方因扩展
# SUPPORTED_ACTIONS 而被迫按元组下标取值。
S04_PLACE_ACTION = SUPPORTED_ACTIONS[0]
S04_STIR_ACTION = SUPPORTED_ACTIONS[1]
S04_PICK_ACTION = SUPPORTED_ACTIONS[2]
S06_PUMP_ACTION = SUPPORTED_ACTIONS[4]
S06_PLACE_ACTION = SUPPORTED_ACTIONS[5]
S06_PICK_ACTION = SUPPORTED_ACTIONS[6]
S09_ADD_LIQUID_ACTION = SUPPORTED_ACTIONS[9]
S07_SOLID_ACTION_BY_PROCESS = {
    1: SUPPORTED_ACTIONS[14],
    2: SUPPORTED_ACTIONS[15],
    3: SUPPORTED_ACTIONS[16],
}
S08_CAP_ACTION = SUPPORTED_ACTIONS[17]
MATERIAL_S03_PICK_ACTION = SUPPORTED_ACTIONS[19]
MATERIAL_S06_PLACE_ACTION = SUPPORTED_ACTIONS[20]
MATERIAL_S06_ADD_ACTION = SUPPORTED_ACTIONS[21]
MATERIAL_S06_PICK_ACTION = SUPPORTED_ACTIONS[22]
S07_MATERIAL_ROBOT_PICK_ACTION = SUPPORTED_ACTIONS[23]
S07_MATERIAL_PREPARE_ACTION = SUPPORTED_ACTIONS[24]
S07_MATERIAL_ROBOT_PLACE_ACTION = SUPPORTED_ACTIONS[25]
S07_MATERIAL_COMMIT_ACTION = SUPPORTED_ACTIONS[26]
S07_MATERIAL_DOSE_ACTION = SUPPORTED_ACTIONS[27]
SINGLE_SAMPLE_WORKFLOW = "s_z_lab_单样品全流程_物料感知"
ATTACHMENT_SINGLE_SAMPLE_WORKFLOW = "s_z_lab_单样品原子流程_无_s07_扫码"
DUAL_TASK_ATTACHMENT_WORKFLOW = "s_z_lab_双任务单样品原子流程_无_s07_扫码"
SINGLE_SAMPLE_WORKFLOWS = frozenset(
    {
        SINGLE_SAMPLE_WORKFLOW,
        ATTACHMENT_SINGLE_SAMPLE_WORKFLOW,
        DUAL_TASK_ATTACHMENT_WORKFLOW,
    }
)
STANDARD_TRANSFER_WORKFLOW = "s_z_lab_标准物料转运"
BEAKER_TRANSFER_CHAIN_WORKFLOW = "s_z_lab_烧杯五工位搬运"
BEAKER_TRANSFER_UNWITNESSED_SITE_SENSORS = frozenset(
    {
        S072_SENSOR_BY_POSITION[2],
        S06_BEAKER_SENSOR,
        S05_MATERIAL_SENSOR,
    }
)
S07_MATERIAL_WORKFLOW = "s07_粉桶与烧杯搬运后固体称量"
S09_WORKFLOW = "s09_移液调试"
SINGLE_SAMPLE_S07_DOSE_ACTION = (
    "szlab_s07_solid_addition.dose_powder_with_two_materials"
)
SINGLE_SAMPLE_S08_LIQUID_CAP_ACTION = (
    "szlab_s08_cap_station.process_liquid_reagent_100ml_cap_with_material"
)
SINGLE_SAMPLE_S08_SAMPLE_CAP_ACTION = (
    "szlab_s08_cap_station.process_sample_vial_250ml_cap_with_material"
)
SINGLE_SAMPLE_S09_ACTION = "szlab_mixer_pipetting_station.add_liquid_with_materials"
SINGLE_SAMPLE_PUMP_ACTION = "szlab_mixer_pump.add_solvent_with_materials"
SINGLE_SAMPLE_STIR_ACTION = "szlab_mixer_stirrer.stir_beaker"
SINGLE_SAMPLE_ROBOT_PICK_ACTION = "szlab_mixer_robot.pick_beaker"
SINGLE_SAMPLE_ROBOT_POUR_ACTION = "szlab_mixer_robot.pour_beaker_into_vial"

WORKFLOW_IDS = (
    "szlab_magnetic_stirring_workflow",
    "szlab_photoshotting_workflow",
    "szlab_robot_action_workflow",
    "s04_robot_stirring_workflow",
    "s06_robot_workflow",
    "s07_robot_workflow",
    "szlab_s07_solid_addition_workflow",
    "s08_cap_workflow",
    S09_WORKFLOW,
    "szlab_stack_s05_s06_workflow",
    "szlab_mixer_workflow",
    "szlab_mixer_pump_production",
    "szlab_material_s06_workflow",
    S07_MATERIAL_WORKFLOW,
    STANDARD_TRANSFER_WORKFLOW,
    SINGLE_SAMPLE_WORKFLOW,
    ATTACHMENT_SINGLE_SAMPLE_WORKFLOW,
    DUAL_TASK_ATTACHMENT_WORKFLOW,
    BEAKER_TRANSFER_CHAIN_WORKFLOW,
)

WORKFLOW_ALIASES = {
    "s07_material_dosing": S07_MATERIAL_WORKFLOW,
    "szlab_s09_pipetting_workflow": S09_WORKFLOW,
}

WORKFLOW_COMPONENTS = {
    "szlab_magnetic_stirring_workflow": frozenset({"stirrer"}),
    "szlab_photoshotting_workflow": frozenset({"photo"}),
    "szlab_robot_action_workflow": frozenset({"robot_s04"}),
    "s04_robot_stirring_workflow": frozenset({"robot_s04", "stirrer"}),
    "s06_robot_workflow": frozenset({"robot_s06", "pump"}),
    "s07_robot_workflow": frozenset({"robot_s07"}),
    "szlab_s07_solid_addition_workflow": frozenset({"s07"}),
    "s08_cap_workflow": frozenset({"s08"}),
    S09_WORKFLOW: frozenset({"s09"}),
    "szlab_stack_s05_s06_workflow": frozenset({"photo", "pump"}),
    "szlab_mixer_workflow": frozenset({"pump"}),
    "szlab_mixer_pump_production": frozenset({"pump"}),
    "szlab_material_s06_workflow": frozenset({"robot_s03", "robot_s06", "pump"}),
    S07_MATERIAL_WORKFLOW: frozenset({"robot_s03", "robot_s07", "s07"}),
    STANDARD_TRANSFER_WORKFLOW: frozenset({"robot_standard"}),
    BEAKER_TRANSFER_CHAIN_WORKFLOW: frozenset({"robot_standard"}),
    SINGLE_SAMPLE_WORKFLOW: frozenset(
        {"robot_standard", "pump", "stirrer", "photo", "s07", "s08", "s09"}
    ),
    ATTACHMENT_SINGLE_SAMPLE_WORKFLOW: frozenset(
        {"robot_standard", "pump", "stirrer", "photo", "s07", "s08", "s09"}
    ),
    DUAL_TASK_ATTACHMENT_WORKFLOW: frozenset(
        {"robot_standard", "pump", "stirrer", "photo", "s07", "s08", "s09"}
    ),
}
ALL_COMPONENTS = frozenset().union(*WORKFLOW_COMPONENTS.values())
ROBOT_COMPONENTS = frozenset(
    {"robot_s03", "robot_s04", "robot_s06", "robot_s07", "robot_standard"}
)

STANDARD_ROBOT_TASK_KIND: dict[int, Literal["pick", "place", "pour"]] = {
    1: "pick",
    **{task: "place" if task % 2 else "pick" for task in range(3, 25)},
    25: "pour",
}

ROBOT_ACTION_BY_TASK = {
    6: MATERIAL_S03_PICK_ACTION,
    7: SUPPORTED_ACTIONS[0],
    8: SUPPORTED_ACTIONS[2],
    11: SUPPORTED_ACTIONS[5],
    12: SUPPORTED_ACTIONS[6],
    13: SUPPORTED_ACTIONS[11],
    14: S07_MATERIAL_ROBOT_PICK_ACTION,
    15: SUPPORTED_ACTIONS[12],
    16: SUPPORTED_ACTIONS[13],
}

MATERIAL_S06_ACTION_BY_TASK = {
    6: MATERIAL_S03_PICK_ACTION,
    11: MATERIAL_S06_PLACE_ACTION,
    12: MATERIAL_S06_PICK_ACTION,
}

MATERIAL_S07_ACTION_BY_TASK = {
    6: S07_MATERIAL_ROBOT_PICK_ACTION,
    14: S07_MATERIAL_ROBOT_PICK_ACTION,
    15: S07_MATERIAL_ROBOT_PLACE_ACTION,
}


def s04_station(position: int) -> str:
    return f"S04{int(position)}"


def s04_sensor(position: int) -> str:
    position = int(position)
    if position not in range(1, 7):
        raise ValueError("S04 position 必须在 1-6 范围内")
    return f"传感器状态_上位机[2].NO[{position + 9}]"


def s04_allow(position: int) -> str:
    return f"{s04_station(position)}允许加工"


def s04_status(position: int) -> str:
    return f"{s04_station(position)}磁搅状态"


def s04_process(position: int) -> str:
    return f"{s04_station(position)}磁搅工艺选择"


def s04_params_written(position: int) -> str:
    return f"{s04_station(position)}参数写入完成"


def s04_done(position: int) -> str:
    return f"{s04_station(position)}加工完成"


def s04_duration(position: int) -> str:
    """S04 磁搅时长节点；驱动以毫秒写入。"""

    return f"磁搅时间设置_上位机[{int(position) - 1}]"


def s071_sensor(slot: int) -> str:
    try:
        return S071_SENSOR_BY_SLOT[int(slot)]
    except KeyError as exc:
        raise ValueError("S071 位置必须在 1-6 范围内") from exc


def s072_sensor(position: int) -> str:
    try:
        return S072_SENSOR_BY_POSITION[int(position)]
    except KeyError as exc:
        raise ValueError("S072 位置必须在 1-2 范围内") from exc


def _linear_bit_sensor(
    start_array: int, start_bit: int, position: int, count: int, label: str
) -> str:
    position = int(position)
    if position not in range(1, count + 1):
        raise ValueError(f"{label}必须在 1-{count} 范围内")
    offset = start_bit + position - 1
    array_index, bit_index = divmod(start_array * 16 + offset, 16)
    return f"传感器状态_上位机[{array_index}].NO[{bit_index}]"


def s02_sensor(position: int) -> str:
    return _linear_bit_sensor(0, 0, position, 6, "S02 TIP 位置")


def s03_sensor(product_type: int, position: int) -> str:
    start_array, start_bit = (0, 6) if int(product_type) == 1 else (1, 8)
    return _linear_bit_sensor(start_array, start_bit, position, 18, "S03 容器位置")


def s10_sensor(position: int) -> str:
    return _linear_bit_sensor(4, 12, position, 20, "S10 试剂瓶位置")


def s11_sensor(product_type: int, position: int) -> str:
    start_array, start_bit = (6, 0) if int(product_type) == 1 else (7, 2)
    return _linear_bit_sensor(start_array, start_bit, position, 18, "S11 容器位置")


def s09_transfer_sensor(product_type: int, position: int) -> str:
    product_type = int(product_type)
    position = int(position)
    if product_type == 1:
        try:
            return S09_TIP_BOX_SENSOR[position]
        except KeyError as exc:
            raise ValueError("S09 TIP 盒位置必须在 1-2 范围内") from exc
    if product_type == 2:
        try:
            return S09_STATION_SENSOR[position]
        except KeyError as exc:
            raise ValueError("S09 液体试剂瓶位置必须在 1-5 范围内") from exc
    if product_type == 3 and position == 1:
        # S09 的烧杯位没有独立在位传感器。NO[7] 属于 1 号试剂瓶位；
        # 若烧杯取放也改写它，随后向 REAGENT1 放瓶会被误判为库位已占用。
        return ""
    raise ValueError("S09 取放料产品/位置不合法")


class VariableAdapter(Protocol):
    """状态机所需的最小变量读写 interface。"""

    def read(self, name: str) -> Any: ...

    def write(self, name: str, value: Any) -> None: ...


@dataclass(frozen=True)
class Requirement:
    kind: Literal["opcua", "config", "file", "parameter"]
    subject: str
    expectation: str
    expected: Any = None
    operator: Literal["eq", "in", "gt", "readable", "manual"] = "manual"
    phase: str = "启动前"
    note: str = ""

    def evaluate(self, adapter: VariableAdapter) -> tuple[bool | None, Any]:
        if self.kind != "opcua" or self.operator == "manual":
            return None, None
        try:
            actual = adapter.read(self.subject)
        except Exception as exc:  # noqa: BLE001 - 报告远端单节点错误
            return False, f"{type(exc).__name__}: {exc}"
        if self.operator == "readable":
            return True, actual
        if self.operator == "eq":
            return actual == self.expected, actual
        if self.operator == "in":
            return actual in self.expected, actual
        if self.operator == "gt":
            try:
                return actual > self.expected, actual
            except TypeError:
                return False, actual
        raise ValueError(f"未知检查操作: {self.operator}")


@dataclass(frozen=True)
class WorkflowSpec:
    workflow_id: str
    actions: tuple[str, ...]
    requirements: tuple[Requirement, ...]


def _opc_eq(
    subject: str, expected: Any, *, phase: str = "启动前", note: str = ""
) -> Requirement:
    return Requirement(
        kind="opcua",
        subject=subject,
        expectation=f"== {expected!r}",
        expected=expected,
        operator="eq",
        phase=phase,
        note=note,
    )


def _opc_in(subject: str, expected: Iterable[Any], *, note: str = "") -> Requirement:
    values = tuple(expected)
    return Requirement(
        kind="opcua",
        subject=subject,
        expectation=f"in {values!r}",
        expected=values,
        operator="in",
        note=note,
    )


def _opc_gt(subject: str, expected: Any, *, note: str = "") -> Requirement:
    return Requirement(
        kind="opcua",
        subject=subject,
        expectation=f"> {expected!r}",
        expected=expected,
        operator="gt",
        note=note,
    )


def _opc_readable(subject: str, *, note: str = "") -> Requirement:
    return Requirement(
        kind="opcua",
        subject=subject,
        expectation="节点存在且可读",
        operator="readable",
        note=note,
    )


def _manual(
    kind: Literal["config", "file", "parameter"],
    subject: str,
    expectation: str,
    *,
    note: str = "",
) -> Requirement:
    return Requirement(
        kind=kind,
        subject=subject,
        expectation=expectation,
        operator="manual",
        note=note,
    )


def _robot_common() -> tuple[Requirement, ...]:
    return (
        _opc_eq(ROBOT_HOME, True),
        _opc_eq(ROBOT_WRITE_ALLOWED, True),
        _opc_eq(ROBOT_TASK_COMPLETE, 0, note="开始新任务前完成码应清零"),
    )


def build_workflow_specs(position: int = 1, pump: int = 1) -> tuple[WorkflowSpec, ...]:
    """返回仓库当前 18 个 Python 工作流的先决条件目录。

    参数：``position`` 是 S04 调试库位编号；``pump`` 是 S06 储液泵选择。
    返回：工作流（Workflow）标识、动作及 PLC 先决条件的不可变目录。
    异常：库位编号或储液泵选择超出支持范围时抛出 ``ValueError``。
    """

    position = int(position)
    pump = int(pump)
    if position not in range(1, 7):
        raise ValueError("position 必须在 1-6 范围内")
    if pump not in (1, 2, 3):
        raise ValueError("pump 必须是 1、2 或 3")

    storage_requirements = tuple(
        _opc_eq(S06_STORAGE_BOTTLE_SENSOR[index], True)
        for index in ((1, 2) if pump == 3 else (pump,))
    )
    s06_common = (
        _opc_eq(S06_READY, True),
        _opc_eq(S06_ALLOW, True),
        _opc_eq(S06_DONE, False, note="wait_new_cycle_done 要求从 False 开始新周期"),
        *storage_requirements,
    )
    s04_common = (
        _opc_eq(s04_allow(position), True),
        _opc_eq(s04_status(position), 1, note="1=空闲；握手器执行期间切为 2=Busy"),
        _opc_eq(s04_done(position), False, note="新一轮磁搅开始前完成信号应清零"),
    )
    standard_transfer_requirements = (
        *_robot_common(),
        _manual("config", "standard_actions_enabled", "必须启用标准 robot.pick/place"),
        _manual(
            "runtime",
            "workflow_execution_identity",
            "OS 必须为每个 robot.pick/place Action 注入有效 WorkflowNodeJob UUID",
        ),
    )
    single_sample_requirements = (
        *standard_transfer_requirements,
        _opc_eq(S06_READY, True),
        _opc_eq(S06_ALLOW, True),
        _opc_eq(S07_HOME, True),
        _opc_eq(S07_ALLOW, True),
        _opc_eq(S08_HOME, True),
        _opc_eq(S08_ALLOW, True),
        _opc_eq(S09_ALLOW, True),
        _opc_eq(S03_BEAKER_SENSOR, True, note="固定示例烧杯源位 L1B1"),
        _opc_eq(S03_SAMPLE_VIAL_SENSOR, True, note="固定示例 250 mL 样品瓶源位 L1A1"),
        _opc_eq(s071_sensor(1), True, note="固定示例粗粉桶源位 L1C1"),
        _opc_eq(s071_sensor(2), True, note="固定示例精粉桶源位 L1C2"),
        _opc_eq(s10_sensor(1), True, note="固定示例试剂瓶源位 R1C1"),
        _opc_eq(S09_TIP_BOX_SENSOR[1], True, note="移液前 TIP 盒 1 必须在位"),
        _opc_readable(S09_BALANCE_READING),
        _opc_readable(S07_BALANCE_READING),
        _manual(
            "config",
            "s08_s09_site_witnesses",
            "S08 双向瓶位、S09 试剂瓶位与 250 mL 负载必须完成现场验收",
        ),
    )
    attachment_single_sample_requirements = (
        *standard_transfer_requirements,
        _opc_eq(S06_READY, True),
        _opc_eq(S06_ALLOW, True),
        _opc_eq(S07_HOME, True),
        _opc_eq(S07_ALLOW, True),
        _opc_eq(S08_HOME, True),
        _opc_eq(S08_ALLOW, True),
        _opc_eq(S09_ALLOW, True),
        _opc_eq(S03_BEAKER_SENSOR, True, note="固定示例烧杯源位 L1B1"),
        _opc_eq(S03_SAMPLE_VIAL_SENSOR, True, note="固定示例 250 mL 样品瓶源位 L1A1"),
        _opc_eq(s10_sensor(1), True, note="固定示例试剂瓶源位 R1C1"),
        _opc_eq(S09_TIP_BOX_SENSOR[1], True, note="移液前 TIP 盒 1 必须在位"),
        _opc_readable(S09_BALANCE_READING),
        _opc_readable(S07_BALANCE_READING),
        _manual(
            "config",
            "s07_dosing_powder_preloaded",
            "加样粉桶堆栈起始即已装入粗/精粉桶（P01/P02），本流程不再从上料仓转运",
        ),
        _manual(
            "config",
            "s08_s09_site_witnesses",
            "S08 双向瓶位、S09 试剂瓶位与 250 mL 负载必须完成现场验收",
        ),
    )
    attachment_single_sample_actions = (
        "szlab_mixer_robot.pick",
        "szlab_mixer_robot.place",
        "host_node.transfer_resource",
        "szlab_s08_cap_station.process_liquid_reagent_100ml_cap_with_material",
        "szlab_s07_solid_addition.dose_powder_with_two_materials",
        "szlab_mixer_pump.add_solvent_with_materials",
        "szlab_mixer_pipetting_station.add_liquid_with_materials",
        "szlab_mixer_stirrer.stir_beaker",
        "szlab_s08_cap_station.process_sample_vial_250ml_cap_with_material",
        "szlab_mixer_photoshotting.inspect_beaker",
        "szlab_mixer_robot.pick_beaker",
        "szlab_mixer_robot.pour_beaker_into_vial",
    )
    return (
        WorkflowSpec(
            "szlab_magnetic_stirring_workflow",
            ("szlab_mixer_stirrer.run_stirring",),
            (
                _opc_eq(
                    s04_sensor(position), True, note="实机驱动在加工前后均校验烧杯在位"
                ),
                *s04_common,
            ),
        ),
        WorkflowSpec(
            "szlab_photoshotting_workflow",
            ("szlab_mixer_photoshotting.take_photo",),
            (
                _opc_eq(
                    S05_MATERIAL_SENSOR, True, note="实机驱动在拍照前后均校验物料在位"
                ),
                _opc_eq(S05_DONE, True, note="当前驱动没有拍照启动写入，只等待该信号"),
                _opc_eq(S05_RESULT, 1, note="1=OK，2=NG"),
            ),
        ),
        WorkflowSpec(
            "szlab_robot_action_workflow",
            (
                "szlab_mixer_robot.submit_place_to_s04",
                "szlab_mixer_robot.submit_pick_from_s04",
            ),
            (
                *_robot_common(),
                _opc_eq(
                    s04_sensor(position),
                    False,
                    note="放料前目标位必须为空；握手器放料后会置 True",
                ),
            ),
        ),
        WorkflowSpec(
            "s04_robot_stirring_workflow",
            (
                "szlab_mixer_robot.submit_place_to_s04",
                "szlab_mixer_stirrer.run_stirring",
                "szlab_mixer_robot.submit_pick_from_s04",
            ),
            (
                *_robot_common(),
                _opc_eq(
                    s04_sensor(position),
                    False,
                    note="放料前为空，放料后 True，取料后恢复 False",
                ),
                *s04_common,
            ),
        ),
        WorkflowSpec(
            "s06_robot_workflow",
            (
                "szlab_mixer_robot.submit_place_to_s06",
                "szlab_mixer_pump.run_solvent_addition",
                "szlab_mixer_robot.submit_pick_from_s06",
            ),
            (
                *_robot_common(),
                _opc_eq(
                    S06_BEAKER_SENSOR, False, note="机器人放料前 S06 加液位必须为空"
                ),
                *s06_common,
                _manual(
                    "parameter", "skip_level_check", "False 时储液瓶传感器必须在位"
                ),
            ),
        ),
        WorkflowSpec(
            "s07_robot_workflow",
            (
                "szlab_mixer_robot.submit_place_to_s071",
                "szlab_mixer_robot.submit_place_to_s072",
                "szlab_mixer_robot.submit_pick_from_s072",
            ),
            (
                *_robot_common(),
                _opc_eq(
                    "传感器状态_上位机[3].NO[8]",
                    False,
                    note="S071 放粉罐目标位初始为空",
                ),
                _opc_eq(
                    "传感器状态_上位机[3].NO[14]", False, note="S072 放料目标位初始为空"
                ),
            ),
        ),
        WorkflowSpec(
            "szlab_s07_solid_addition_workflow",
            (
                "szlab_s07_solid_addition.scan_powder_cartridges",
                "szlab_s07_solid_addition.rotate_powder_cartridge_to_feed",
                "szlab_s07_solid_addition.dose_powder",
            ),
            (
                _opc_eq("S07原点信号", True),
                _opc_eq("S07允许加工", True),
                _opc_eq("S07工艺完成", 0, note="每轮开始前完成工艺号应清零"),
                _opc_readable(
                    "S07位置1二维码[99]", note="扫码动作需要全部 10×100 个二维码节点"
                ),
                _opc_readable(
                    S07_BALANCE_READING, note="注粉动作完成后会读取并记录最终天平值"
                ),
                _manual(
                    "file",
                    "s07_powder_params.json",
                    "default recipe 存在且参数长度正确",
                ),
            ),
        ),
        WorkflowSpec(
            "s08_cap_workflow",
            (
                "szlab_s08_cap_station.process_cap_with_sample_parts(open)",
                "szlab_s08_cap_station.process_cap_with_sample_parts(close)",
            ),
            (
                _opc_eq("S08原点信号", True),
                _opc_eq("S08允许加工", True),
                _opc_eq(
                    "S08工艺完成", 0, note="每轮完成后还必须响应 PC 复位并再次清零"
                ),
                _opc_eq("S082_1数据缓存[0]", 0, note="开盖前至少一个瓶盖暂存缓存为空"),
                _opc_eq(
                    S08_CAP_STATION_SENSOR[2],
                    True,
                    note="100 mL 液体瓶开关盖工位必须有瓶",
                ),
                _opc_eq(
                    S08_CAP_STORAGE_SENSOR[1],
                    False,
                    note="首次开盖前暂存位为空；开盖后会置 True",
                ),
                _manual("parameter", "sample_id", "非零，open/close 使用完全相同的 ID"),
                _opc_in(
                    "工站状态[7]", (2, 3, 4, 5, 6), note="仅开启工站状态校验时要求"
                ),
            ),
        ),
        WorkflowSpec(
            S09_WORKFLOW,
            (
                "szlab_mixer_pipetting_station.prepare_liquid_station",
                "szlab_mixer_pipetting_station.bind_sample_to_station",
                "szlab_mixer_pipetting_station.add_liquid",
                "szlab_mixer_pipetting_station.release_station",
            ),
            (
                _opc_eq("工站状态[8]", 2, note="prepare_liquid_station 只接受状态 2"),
                _opc_eq("S09工艺完成", 0, note="每个 5/7/8/6 子工艺开始前完成号应清零"),
                _opc_eq(
                    S09_TIP_BOX_SENSOR[1], True, note="取放 TIP 工艺要求 TIP 盒在位"
                ),
                _opc_eq(S09_STATION_SENSOR[1], True, note="1 号试剂瓶和烧杯工位在位"),
                _opc_readable(
                    S09_BALANCE_READING,
                    note="工艺 8 可选遥测；完成判定只看 S09工艺完成，不等待稳定位",
                ),
                _opc_readable(
                    f"{S09_ASPIRATE_BALANCE_READINGS}[0]",
                    note="工艺 9 完成后按测密度次数读取抽/放液天平数组",
                ),
                _opc_gt(f"S09液体瓶{pump if pump in (1, 2) else 1}剩余液量", 0.0),
                _manual(
                    "parameter", "tip_box_index/tip_index", "分别在 1-2、1-96 范围内"
                ),
            ),
        ),
        WorkflowSpec(
            "szlab_stack_s05_s06_workflow",
            (
                "szlab_poly_plc.get_stack_status",
                "szlab_mixer_photoshotting.take_photo",
                "szlab_mixer_pump.run_solvent_addition",
            ),
            (
                _opc_readable(
                    "传感器状态_上位机[0].NO[0]", note="堆栈传感器组节点必须可读"
                ),
                _opc_eq(S05_MATERIAL_SENSOR, True),
                _opc_eq(S05_DONE, True),
                _opc_eq(S05_RESULT, 1),
                _opc_eq(S06_BEAKER_SENSOR, True),
                *s06_common,
            ),
        ),
        WorkflowSpec(
            "szlab_mixer_workflow",
            ("szlab_mixer_pump.run_solvent_addition",),
            (
                _opc_eq(S06_BEAKER_SENSOR, True),
                *s06_common,
            ),
        ),
        WorkflowSpec(
            "szlab_mixer_pump_production",
            ("szlab_mixer_pump.run_solvent_addition",),
            (
                _opc_eq(S06_BEAKER_SENSOR, True),
                *s06_common,
            ),
        ),
        WorkflowSpec(
            "szlab_material_s06_workflow",
            (
                MATERIAL_S03_PICK_ACTION,
                MATERIAL_S06_PLACE_ACTION,
                MATERIAL_S06_ADD_ACTION,
                MATERIAL_S06_PICK_ACTION,
            ),
            (
                *_robot_common(),
                _opc_eq(S03_BEAKER_SENSOR, True, note="S03 1-1 取料源位必须有烧杯"),
                _opc_eq(
                    S06_BEAKER_SENSOR, False, note="机器人放料前 S06 加液位必须为空"
                ),
                *s06_common,
                _manual(
                    "parameter", "skip_level_check", "False 时储液瓶传感器必须在位"
                ),
            ),
        ),
        WorkflowSpec(
            S07_MATERIAL_WORKFLOW,
            (
                S07_MATERIAL_ROBOT_PICK_ACTION,
                S07_MATERIAL_PREPARE_ACTION,
                S07_MATERIAL_ROBOT_PLACE_ACTION,
                S07_MATERIAL_COMMIT_ACTION,
                S07_MATERIAL_ROBOT_PICK_ACTION,
                S07_MATERIAL_ROBOT_PLACE_ACTION,
                S07_MATERIAL_COMMIT_ACTION,
                S07_MATERIAL_DOSE_ACTION,
            ),
            (
                *_robot_common(),
                _opc_eq(S03_BEAKER_SENSOR, True, note="烧杯源 Site 必须有 500 mL 烧杯"),
                _opc_eq(s071_sensor(1), True, note="示例粉桶源 Site 必须有粉桶"),
                _opc_eq(s072_sensor(1), False, note="S07/S072 交接位必须为空"),
                _opc_eq(S07_HOME, True),
                _opc_eq(S07_ALLOW, True),
                _opc_eq(S07_DONE, 0, note="每轮开始前完成工艺号应清零"),
                _opc_readable(S07_BALANCE_READING),
                _manual(
                    "config",
                    "standard_actions_enabled",
                    "必须启用标准 robot.pick/place",
                ),
                _manual(
                    "runtime",
                    "workflow_execution_identity",
                    "OS 必须为每个 robot.pick/place Action 注入有效 WorkflowNodeJob UUID",
                ),
            ),
        ),
        *(
            WorkflowSpec(
                workflow_id,
                (
                    "szlab_mixer_robot.pick",
                    "szlab_mixer_robot.place",
                    "host_node.transfer_resource",
                ),
                standard_transfer_requirements,
            )
            for workflow_id in (STANDARD_TRANSFER_WORKFLOW,)
        ),
        WorkflowSpec(
            BEAKER_TRANSFER_CHAIN_WORKFLOW,
            (
                "szlab_mixer_robot.pick",
                "szlab_mixer_robot.place",
                "host_node.transfer_resource",
            ),
            (
                *standard_transfer_requirements,
                _opc_eq(S03_BEAKER_SENSOR, True, note="S3-L1B1 取料源位必须有烧杯"),
                _opc_eq(S09_STATION_SENSOR[1], False, note="S09 BEAKER1 必须为空"),
                _opc_eq(s04_sensor(1), False, note="S041 必须为空"),
            ),
        ),
        WorkflowSpec(
            SINGLE_SAMPLE_WORKFLOW,
            (
                "szlab_mixer_robot.pick",
                "szlab_mixer_robot.place",
                "host_node.transfer_resource",
                "szlab_s07_solid_addition.scan_powder_cartridges",
                "szlab_s07_solid_addition.prepare_powder_cartridge_site",
                "szlab_s07_solid_addition.dose_powder_with_two_materials",
                "szlab_mixer_pump.add_solvent_with_materials",
                "szlab_s08_cap_station.process_liquid_reagent_100ml_cap_with_material",
                "szlab_mixer_pipetting_station.add_liquid_with_materials",
                "szlab_mixer_stirrer.stir_beaker",
                "szlab_mixer_photoshotting.inspect_beaker",
                "szlab_s08_cap_station.process_sample_vial_250ml_cap_with_material",
                "szlab_mixer_robot.pick_beaker",
                "szlab_mixer_robot.pour_beaker_into_vial",
            ),
            single_sample_requirements,
        ),
        WorkflowSpec(
            ATTACHMENT_SINGLE_SAMPLE_WORKFLOW,
            attachment_single_sample_actions,
            attachment_single_sample_requirements,
        ),
        WorkflowSpec(
            DUAL_TASK_ATTACHMENT_WORKFLOW,
            attachment_single_sample_actions,
            (
                *attachment_single_sample_requirements,
                _opc_eq(s03_sensor(1, 2), True, note="Task B 烧杯源位 L1B2"),
                _opc_eq(s03_sensor(3, 2), True, note="Task B 样品瓶源位 L1A2"),
                _opc_eq(s10_sensor(2), True, note="Task B 试剂瓶源位 R1C2"),
                _opc_eq(s11_sensor(1, 2), False, note="Task B 烧杯成品位 L1B2"),
                _opc_eq(s11_sensor(3, 2), False, note="Task B 样品瓶成品位 L1A2"),
            ),
        ),
    )


class OpcUaVariableAdapter:
    """使用直接 NodeId 访问 CSV 已创建变量的生产 adapter。"""

    def __init__(
        self, url: str, node_prefix: str, username: str = "", password: str = ""
    ) -> None:
        self.url = url
        self.node_prefix = node_prefix
        self.username = username
        self.password = password
        self._client = self._new_client()
        self._nodes: dict[str, Any] = {}
        self._browse_index: dict[str, Any] | None = None

    def _new_client(self) -> Any:
        from opcua import Client

        client = Client(self.url, timeout=10)
        if self.username:
            client.set_user(self.username)
            client.set_password(self.password)
        return client

    def connect(self) -> None:
        self._client.connect()

    def disconnect(self) -> None:
        try:
            self._client.disconnect()
        except Exception as exc:
            print(
                f"OPC UA 断开连接时忽略临时错误: {type(exc).__name__}: {exc}",
                file=sys.stderr,
                flush=True,
            )

    def _reconnect(self) -> None:
        try:
            self._client.disconnect()
        except Exception:
            pass
        self._client = self._new_client()
        self._client.connect()
        self._nodes.clear()
        self._browse_index = None

    def _run_io(self, name: str, operation: Any) -> Any:
        attempts = 3
        for attempt in range(1, attempts + 1):
            try:
                return operation()
            except (TimeoutError, ConnectionError, OSError) as exc:
                if attempt >= attempts:
                    raise RuntimeError(
                        f"{name}: OPC UA 通信失败（已重试 {attempts} 次）"
                    ) from exc
                print(
                    f"{name}: OPC UA {type(exc).__name__}，"
                    f"正在重连并重试 ({attempt}/{attempts})",
                    file=sys.stderr,
                    flush=True,
                )
                time.sleep(1.0)
                self._reconnect()
        raise AssertionError("unreachable")

    def _node(self, name: str) -> Any:
        node = self._nodes.get(name)
        if node is None:
            node = self._client.get_node(f"{self.node_prefix}{name}")
            try:
                node.get_data_type_as_variant_type()
            except Exception as direct_error:  # noqa: BLE001 - 兼容非标准 NodeId 树
                node = self._browse_name_index().get(name)
                if node is None:
                    raise KeyError(
                        f"OPC UA 节点不存在: {name} ({self.node_prefix}{name})"
                    ) from direct_error
            self._nodes[name] = node
        return node

    def _browse_name_index(
        self,
        *,
        max_depth: int = 12,
        max_nodes: int = 20_000,
    ) -> dict[str, Any]:
        """扫描一次 BrowseName，兼容 Uni-Lab 测试服务器创建的嵌套节点。"""

        cached = getattr(self, "_browse_index", None)
        if cached is not None:
            return cached
        index: dict[str, Any] = {}
        stack: list[tuple[Any, int]] = [(self._client.get_objects_node(), 0)]
        visited = 0
        while stack and visited < max_nodes:
            node, depth = stack.pop()
            visited += 1
            try:
                index.setdefault(node.get_browse_name().Name, node)
                if depth < max_depth:
                    stack.extend((child, depth + 1) for child in node.get_children())
            except Exception:
                continue
        self._browse_index = index
        return index

    def read(self, name: str) -> Any:
        return self._run_io(name, lambda: self._node(name).get_value())

    def write(self, name: str, value: Any) -> None:
        """按远端变量真实 VariantType 写 Value，不改时间戳或状态码。"""

        self._run_io(name, lambda: self._write_once(name, value))

    def _write_once(self, name: str, value: Any) -> None:
        from opcua import ua

        node = self._node(name)
        variant_type = node.get_data_type_as_variant_type()
        data_value = ua.DataValue()
        data_value.Value = ua.Variant(value, variant_type)
        data_value.StatusCode = None
        data_value.SourceTimestamp = None
        data_value.ServerTimestamp = None
        data_value.SourcePicoseconds = None
        data_value.ServerPicoseconds = None

        write_value = ua.WriteValue()
        write_value.NodeId = node.nodeid
        write_value.AttributeId = ua.AttributeIds.Value
        write_value.Value = data_value

        params = ua.WriteParameters()
        params.NodesToWrite = [write_value]
        results = self._client.uaclient.write(params)
        if results and not results[0].is_good():
            raise RuntimeError(f"{name}: {results[0]}")


@dataclass(frozen=True)
class HandshakeEvent:
    action: str
    phase: Literal["accepted", "completed", "reset"]
    detail: dict[str, Any]


@dataclass
class _Cycle:
    phase: Literal["idle", "executing", "await_reset"] = "idle"
    due_at: float = 0.0
    process: int = 0
    position: int = 0
    sensor: str = ""
    duration_seconds: float = 0.0


class WorkflowHandshakeSimulator:
    """覆盖 SZLab 全部 PLC 协议族的握手状态机。

    ``package_mode=True`` 时工作流仅作为初始场景标签，Robot 与 S04-S09
    始终常驻；默认 False 保持现有单元测试和第三方源码调用的兼容语义。
    """

    def __init__(
        self,
        adapter: VariableAdapter,
        *,
        position: int = 1,
        pump: int = 1,
        process_delay: float = 0.5,
        delays: dict[str, float] | None = None,
        initial_values: dict[str, Any] | None = None,
        s06_robot_workflow: bool = False,
        s09_pipetting_workflow: bool = False,
        s09_remaining_volume_ml: float = 100.0,
        s07_balance_reading: float = 1.0,
        s09_balance_reading: float = 1.0,
        workflow: str | None = None,
        package_mode: bool = False,
        time_scale: float = 1.0,
    ) -> None:
        if int(position) not in range(1, 7):
            raise ValueError("position 必须在 1-6 范围内")
        if int(pump) not in (1, 2, 3):
            raise ValueError("pump 必须是 1、2 或 3")
        requested_workflow = workflow or "all"
        selected_workflow = WORKFLOW_ALIASES.get(requested_workflow, requested_workflow)
        if selected_workflow not in ("all", *WORKFLOW_IDS):
            raise ValueError(f"不支持的握手工作流: {requested_workflow}")
        if float(time_scale) <= 0:
            raise ValueError("仿真时间倍率必须大于 0")
        self.adapter = adapter
        self.position = int(position)
        self.pump = int(pump)
        self.process_delay = max(float(process_delay), 0.0)
        self.delays = {
            str(key): max(float(value), 0.0) for key, value in (delays or {}).items()
        }
        self.initial_value_overrides = dict(initial_values or {})
        self.workflow = selected_workflow
        self.package_mode = bool(package_mode)
        self.time_scale = float(time_scale)
        self.s06_robot_workflow = bool(
            s06_robot_workflow
            or selected_workflow
            in {
                "s06_robot_workflow",
                "szlab_material_s06_workflow",
            }
            or selected_workflow in SINGLE_SAMPLE_WORKFLOWS
        )
        self.s09_pipetting_workflow = bool(
            s09_pipetting_workflow
            or selected_workflow == S09_WORKFLOW
            or selected_workflow in SINGLE_SAMPLE_WORKFLOWS
        )
        self.s09_remaining_volume_ml = float(s09_remaining_volume_ml)
        self.s07_balance_reading = float(s07_balance_reading)
        self.s09_balance_reading = float(s09_balance_reading)
        if self.s09_pipetting_workflow and self.s09_remaining_volume_ml <= 0:
            raise ValueError("S09 初始液体余量必须大于 0 mL")
        self.robot = _Cycle()
        self.stirrers = {
            position_id: _Cycle(position=position_id) for position_id in range(1, 7)
        }
        # 源码级兼容：旧调用方仍可读取所选调试位的单周期属性。
        self.stirrer = self.stirrers[self.position]
        self.pump_cycle = _Cycle(process=self.pump)
        self.s07_cycle = _Cycle()
        self.s08_cycle = _Cycle()
        self.s09_cycle = _Cycle()
        self._s071_loaded_sensor = ""
        self.completed_actions = 0

    @property
    def enabled_components(self) -> frozenset[str]:
        if self.package_mode or self.workflow == "all":
            return ALL_COMPONENTS
        return WORKFLOW_COMPONENTS[self.workflow]

    @property
    def scenario_components(self) -> frozenset[str]:
        """返回初始场景实际需要的组件，不影响设备包处理器常驻。"""

        if self.workflow == "all":
            return ALL_COMPONENTS
        return WORKFLOW_COMPONENTS[self.workflow]

    def initialization_values(self) -> dict[str, Any]:
        """返回当前工作流场景启动前应写入 PLC 的可验证初始值。

        参数：无；使用当前代理的工作流选择和调试参数。
        返回：PLC 节点名到仿真初值的映射；在位值仅是传感器观测，
        不改写物料或库位（Site）的权威事实。
        """

        components = self.enabled_components
        scenario_components = self.scenario_components
        values: dict[str, Any] = {}
        if components & ROBOT_COMPONENTS:
            values.update(
                {
                    ROBOT_HOME: True,
                    ROBOT_WRITE_ALLOWED: True,
                    ROBOT_WRITE_DONE: False,
                    ROBOT_TASK_COMPLETE: 0,
                }
            )
            if self.workflow != BEAKER_TRANSFER_CHAIN_WORKFLOW:
                values[ROBOT_TOOL_PAYLOAD_SENSOR] = False
        if "robot_s03" in components:
            values[S03_BEAKER_SENSOR] = True
        if self.workflow in SINGLE_SAMPLE_WORKFLOWS:
            values.update(
                {
                    S03_BEAKER_SENSOR: True,
                    S03_SAMPLE_VIAL_SENSOR: True,
                    s071_sensor(1): True,
                    s071_sensor(2): True,
                    s10_sensor(1): True,
                    s11_sensor(1, 1): False,
                    s11_sensor(2, 1): False,
                    s072_sensor(2): False,
                }
            )
        if self.workflow == DUAL_TASK_ATTACHMENT_WORKFLOW:
            values.update(
                {
                    s03_sensor(1, 2): True,
                    s03_sensor(3, 2): True,
                    s10_sensor(2): True,
                    s11_sensor(1, 2): False,
                    s11_sensor(3, 2): False,
                }
            )
        if self.workflow == BEAKER_TRANSFER_CHAIN_WORKFLOW:
            values.update(
                {
                    S03_BEAKER_SENSOR: True,
                    S09_STATION_SENSOR[1]: False,
                    s04_sensor(1): False,
                }
            )
        if "robot_s04" in components:
            for position_id in (
                range(1, 7) if self.package_mode else (self.position,)
            ):
                values[s04_sensor(position_id)] = False
        if "stirrer" in components:
            for position_id in (
                range(1, 7) if self.package_mode else (self.position,)
            ):
                values.update(
                    {
                        s04_sensor(position_id): not (
                            "robot_s04" in scenario_components
                            or self.workflow in SINGLE_SAMPLE_WORKFLOWS
                        )
                        and "stirrer" in scenario_components
                        and position_id == self.position,
                        s04_allow(position_id): True,
                        s04_status(position_id): 1,
                        s04_done(position_id): False,
                    }
                )
        if "photo" in components:
            values.update(
                {
                    S05_MATERIAL_SENSOR: self.workflow not in SINGLE_SAMPLE_WORKFLOWS,
                    S05_DONE: True,
                    S05_RESULT: 1,
                }
            )
        if "pump" in components:
            values.update(
                {
                    S06_READY: True,
                    S06_ALLOW: True,
                    S06_DONE: False,
                    S06_BEAKER_SENSOR: not self.s06_robot_workflow,
                }
            )
            for index in (1, 2) if self.pump == 3 else (self.pump,):
                values[S06_STORAGE_BOTTLE_SENSOR[index]] = True
        if "robot_s07" in components:
            values.update(
                {
                    s071_sensor(1): self.workflow == S07_MATERIAL_WORKFLOW,
                    s072_sensor(1): False,
                    s072_sensor(2): False,
                    S072_ROBOT_PRODUCT: 0,
                }
            )
        if "s07" in components:
            values.update(
                {
                    S07_HOME: True,
                    S07_ALLOW: True,
                    S07_DONE: 0,
                    S07_BALANCE_READING: 0.0,
                }
            )
        if "s08" in components:
            station_values = {
                sensor: self.workflow not in SINGLE_SAMPLE_WORKFLOWS and position == 2
                for position, sensor in S08_CAP_STATION_SENSOR.items()
            }
            values.update(
                {
                    S08_HOME: True,
                    S08_ALLOW: True,
                    S08_DONE: 0,
                    S08_STATION_STATUS: 2,
                    **station_values,
                    **{sensor: False for sensor in S08_CAP_STORAGE_SENSOR.values()},
                    **{s08_cap_cache(1, index): 0 for index in range(30)},
                }
            )
        if "s09" in components:
            station_present = self.workflow not in SINGLE_SAMPLE_WORKFLOWS
            values.update(
                {
                    S09_STATION_STATUS: 2,
                    S09_ALLOW: True,
                    S09_DONE: 0,
                    S09_TIP_BOX_SENSOR[1]: True,
                    S09_TIP_BOX_SENSOR[2]: True,
                    **{
                        sensor: station_present
                        for sensor in S09_STATION_SENSOR.values()
                    },
                    S09_BALANCE_READING: self.s09_balance_reading,
                    S09_DENSITY_COUNT: 0,
                    **{
                        name: 0.0
                        for name in (
                            *s09_density_balance_vars(S09_ASPIRATE_BALANCE_READINGS),
                            *s09_density_balance_vars(S09_DISPENSE_BALANCE_READINGS),
                        )
                    },
                    **{
                        s09_remaining_volume(index): self.s09_remaining_volume_ml
                        for index in range(1, 6)
                    },
                }
            )
        return {**values, **self.initial_value_overrides}

    def cleanup_values(self) -> dict[str, Any]:
        """返回停止握手场景时用于撤销模拟物理状态的安全复位值。

        参数：无；使用当前代理启用的协议组件。
        返回：仅包含代理所有 PLC 输出的复位映射，不覆盖 Edge 输入。
        """

        components = self.enabled_components
        values: dict[str, Any] = {}
        if components & ROBOT_COMPONENTS:
            values.update(
                {
                    ROBOT_HOME: False,
                    ROBOT_WRITE_ALLOWED: False,
                    ROBOT_TASK_COMPLETE: 0,
                }
            )
            if self.workflow != BEAKER_TRANSFER_CHAIN_WORKFLOW:
                values[ROBOT_TOOL_PAYLOAD_SENSOR] = False
        if "robot_s03" in components:
            values[S03_BEAKER_SENSOR] = False
        if self.workflow in SINGLE_SAMPLE_WORKFLOWS:
            values.update(
                {
                    S03_BEAKER_SENSOR: False,
                    S03_SAMPLE_VIAL_SENSOR: False,
                    s071_sensor(1): False,
                    s071_sensor(2): False,
                    s10_sensor(1): False,
                    s11_sensor(1, 1): False,
                    s11_sensor(2, 1): False,
                    s072_sensor(2): False,
                }
            )
        if self.workflow == DUAL_TASK_ATTACHMENT_WORKFLOW:
            values.update(
                {
                    s03_sensor(1, 2): False,
                    s03_sensor(3, 2): False,
                    s10_sensor(2): False,
                    s11_sensor(1, 2): False,
                    s11_sensor(3, 2): False,
                }
            )
        if "robot_s04" in components:
            for position_id in (
                range(1, 7) if self.package_mode else (self.position,)
            ):
                values[s04_sensor(position_id)] = False
        if "stirrer" in components:
            for position_id in (
                range(1, 7) if self.package_mode else (self.position,)
            ):
                values.update(
                    {
                        s04_sensor(position_id): False,
                        s04_allow(position_id): False,
                        s04_status(position_id): 0,
                        s04_done(position_id): False,
                    }
                )
        if "photo" in components:
            values.update(
                {
                    S05_MATERIAL_SENSOR: False,
                    S05_DONE: False,
                    S05_RESULT: 0,
                }
            )
        if "pump" in components:
            values.update(
                {
                    S06_READY: False,
                    S06_ALLOW: False,
                    S06_DONE: False,
                    S06_BEAKER_SENSOR: False,
                }
            )
            for index in (1, 2) if self.pump == 3 else (self.pump,):
                values[S06_STORAGE_BOTTLE_SENSOR[index]] = False
        if "robot_s07" in components:
            values.update(
                {
                    s071_sensor(1): False,
                    s072_sensor(1): False,
                    s072_sensor(2): False,
                    S072_ROBOT_PRODUCT: 0,
                }
            )
        if "s07" in components:
            values.update(
                {
                    S07_HOME: False,
                    S07_ALLOW: False,
                    S07_DONE: 0,
                    S07_BALANCE_READING: 0.0,
                }
            )
        if "s08" in components:
            values.update(
                {
                    S08_HOME: False,
                    S08_ALLOW: False,
                    S08_DONE: 0,
                    S08_STATION_STATUS: 0,
                    **{sensor: False for sensor in S08_CAP_STATION_SENSOR.values()},
                    **{sensor: False for sensor in S08_CAP_STORAGE_SENSOR.values()},
                    **{s08_cap_cache(1, index): 0 for index in range(30)},
                }
            )
        if "s09" in components:
            values.update(
                {
                    S09_STATION_STATUS: 0,
                    S09_ALLOW: False,
                    S09_DONE: 0,
                    **{sensor: False for sensor in S09_TIP_BOX_SENSOR.values()},
                    **{sensor: False for sensor in S09_STATION_SENSOR.values()},
                    S09_BALANCE_READING: 0.0,
                    S09_DENSITY_COUNT: 0,
                    **{
                        name: 0.0
                        for name in (
                            *s09_density_balance_vars(S09_ASPIRATE_BALANCE_READINGS),
                            *s09_density_balance_vars(S09_DISPENSE_BALANCE_READINGS),
                        )
                    },
                    **{s09_remaining_volume(index): 0.0 for index in range(1, 6)},
                }
            )
        if self.workflow == BEAKER_TRANSFER_CHAIN_WORKFLOW:
            values.update(
                {
                    S03_BEAKER_SENSOR: False,
                    S09_STATION_SENSOR[1]: False,
                    s04_sensor(1): False,
                }
            )
        for name, initial_value in self.initial_value_overrides.items():
            values.setdefault(name, False if isinstance(initial_value, bool) else 0)
        return values

    def initialize(self) -> None:
        for name, value in self.initialization_values().items():
            self.adapter.write(name, value)

    def cleanup(self) -> None:
        for name, value in self.cleanup_values().items():
            self.adapter.write(name, value)

    def check_supported_prerequisites(self) -> list[tuple[str, bool, Any, Any]]:
        result: list[tuple[str, bool, Any, Any]] = []
        for name, expected in self.initialization_values().items():
            try:
                actual = self.adapter.read(name)
                result.append((name, actual == expected, expected, actual))
            except Exception as exc:  # noqa: BLE001
                result.append((name, False, expected, f"{type(exc).__name__}: {exc}"))
        return result

    def step(self, now: float | None = None) -> list[HandshakeEvent]:
        now = time.monotonic() if now is None else float(now)
        components = self.enabled_components
        events: list[HandshakeEvent] = []
        if components & ROBOT_COMPONENTS:
            events.extend(self._step_robot(now))
        if "stirrer" in components:
            for position_id in (
                range(1, 7) if self.package_mode else (self.position,)
            ):
                events.extend(self._step_stirrer(now, position_id))
        if "pump" in components:
            events.extend(self._step_pump(now))
        if "s07" in components:
            events.extend(self._step_s07(now))
        if "s08" in components:
            events.extend(self._step_s08(now))
        if "s09" in components:
            events.extend(self._step_s09(now))
        self.completed_actions += sum(event.phase == "completed" for event in events)
        return events

    def all_cycles_idle(self) -> bool:
        """所有已启用握手均已被 Edge 消费并完成复位。"""

        components = self.enabled_components
        cycles = []
        if components & ROBOT_COMPONENTS:
            cycles.append(self.robot)
        if "stirrer" in components:
            cycles.extend(
                self.stirrers[position_id]
                for position_id in (
                    range(1, 7) if self.package_mode else (self.position,)
                )
            )
        if "pump" in components:
            cycles.append(self.pump_cycle)
        if "s07" in components:
            cycles.append(self.s07_cycle)
        if "s08" in components:
            cycles.append(self.s08_cycle)
        if "s09" in components:
            cycles.append(self.s09_cycle)
        return all(cycle.phase == "idle" for cycle in cycles)

    def _robot_task_supported(self, task: int) -> bool:
        return task in ROBOT_ACTION_BY_TASK or (
            "robot_standard" in self.enabled_components
            and task in STANDARD_ROBOT_TASK_KIND
        )

    def _robot_task_position_and_sensor(self, task: int) -> tuple[int, str]:
        if task == 1:
            return 0, ""
        if task in (3, 4):
            position = int(self.adapter.read(S02_ROBOT_POSITION) or 0)
            return position, s02_sensor(position)
        if task in (5, 6):
            if task == 6 and self.workflow not in (
                {STANDARD_TRANSFER_WORKFLOW} | SINGLE_SAMPLE_WORKFLOWS
            ):
                return 1, S03_BEAKER_SENSOR
            product_type = int(self.adapter.read(S03_ROBOT_PRODUCT) or 0)
            position = int(self.adapter.read(S03_ROBOT_POSITION) or 0)
            return position, s03_sensor(product_type, position)
        if task in (7, 8):
            position = int(self.adapter.read(S04_ROBOT_POSITION) or 0)
            if not self.package_mode and position != self.position:
                raise RuntimeError(
                    f"机器人 S04 位置不匹配：脚本监听 {self.position}，收到 {position}"
                )
            return position, s04_sensor(position)
        if task in (9, 10):
            return 1, S05_MATERIAL_SENSOR
        if task in (11, 12):
            return 1, S06_BEAKER_SENSOR
        if task in (13, 14):
            position = int(self.adapter.read(S071_ROBOT_POSITION) or 0)
            return position, s071_sensor(position)
        if task in (15, 16):
            position = int(self.adapter.read(S072_ROBOT_PRODUCT) or 0)
            return position, s072_sensor(position)
        if task in (17, 18):
            position = int(self.adapter.read(S08_ROBOT_POSITION) or 0)
            try:
                return position, S08_CAP_STATION_SENSOR[position]
            except KeyError as exc:
                raise ValueError("S08 取放料位置必须在 1-2 范围内") from exc
        if task in (19, 20):
            product_type = int(self.adapter.read(S09_TRANSFER_PRODUCT) or 0)
            position = int(self.adapter.read(S09_TRANSFER_POSITION) or 0)
            if (
                self.workflow == BEAKER_TRANSFER_CHAIN_WORKFLOW
                and product_type == 3
                and position == 1
            ):
                # 五工位烧杯搬运没有后续 REAGENT1 放瓶动作，可把 NO[7] 当作该
                # 专用场景的站内见证；其它工作流仍走 s09_transfer_sensor 的空见证，
                # 避免烧杯状态污染试剂瓶库位。
                return position, S09_STATION_SENSOR[1]
            return position, s09_transfer_sensor(product_type, position)
        if task in (21, 22):
            position = int(self.adapter.read(S10_ROBOT_POSITION) or 0)
            return position, s10_sensor(position)
        if task in (23, 24):
            product_type = int(self.adapter.read(S11_ROBOT_PRODUCT) or 0)
            position = int(self.adapter.read(S11_ROBOT_POSITION) or 0)
            return position, s11_sensor(product_type, position)
        if task == 25:
            return int(self.adapter.read(S08_POUR_PRODUCT) or 0), ""
        raise ValueError(f"不支持的机器人任务号: {task}")

    def _step_robot(self, now: float) -> list[HandshakeEvent]:
        """推进机器人握手状态机并返回本轮产生的物理执行事件。

        参数：``now`` 是调用方提供的单调时钟秒值，用于判定模拟动作是否到期。
        返回：本轮接受、完成或复位的握手事件列表；没有状态变化时返回空列表。
        安全约束：普通取放料先同步启用的物理见证，再发布 Robot_Home 和完成码；
        五工位搬运不改写 S0722/S05/S06 在位观测及夹爪负载，匹配动作级旁路。
        """

        cycle = self.robot
        events: list[HandshakeEvent] = []
        if cycle.phase == "idle":
            write_done = bool(self.adapter.read(ROBOT_WRITE_DONE))
            task = int(self.adapter.read(ROBOT_TASK_NUMBER) or 0)
            if write_done and self._robot_task_supported(task):
                position, sensor = self._robot_task_position_and_sensor(task)
                self.adapter.write(ROBOT_WRITE_ALLOWED, False)
                self.adapter.write(ROBOT_HOME, False)
                self.adapter.write(ROBOT_TASK_COMPLETE, 0)
                cycle.phase = "executing"
                cycle.process = task
                cycle.position = position
                cycle.sensor = sensor
                cycle.duration_seconds = self._delay_seconds("robot")
                cycle.due_at = now + cycle.duration_seconds
                events.append(
                    HandshakeEvent(
                        self._robot_action(task),
                        "accepted",
                        {
                            "task_number": task,
                            **({"position": position} if position else {}),
                            **({"sensor": sensor} if sensor else {}),
                        },
                    )
                )
        elif cycle.phase == "executing" and now >= cycle.due_at:
            # 任务类型决定动作完成后的库位占用和夹爪持料物理证据。
            task_kind = STANDARD_ROBOT_TASK_KIND.get(cycle.process)
            occupied = task_kind == "place"
            site_witness_enabled = not (
                self.workflow == BEAKER_TRANSFER_CHAIN_WORKFLOW
                and cycle.sensor in BEAKER_TRANSFER_UNWITNESSED_SITE_SENSORS
            )
            if cycle.sensor and site_witness_enabled:
                self.adapter.write(cycle.sensor, occupied)
            tool_holding: bool | None = None
            tool_witness_enabled = self.workflow != BEAKER_TRANSFER_CHAIN_WORKFLOW
            if task_kind in {"pick", "place"}:
                tool_holding = task_kind == "pick"
                # 夹爪传感器仅提供物理执行见证，不替代库存系统的物料结算。
                if tool_witness_enabled:
                    self.adapter.write(ROBOT_TOOL_PAYLOAD_SENSOR, tool_holding)
            rearmed_sensor = ""
            if cycle.process == 13:
                self._s071_loaded_sensor = cycle.sensor
            elif cycle.process == 16 and self._s071_loaded_sensor:
                # s07_robot_workflow 的最后一步取走 S072 产品后，模拟 S071
                # 粉罐已被工站消费/移走，从而无需重启即可开始下一轮放粉罐。
                rearmed_sensor = self._s071_loaded_sensor
                self.adapter.write(rearmed_sensor, False)
                self._s071_loaded_sensor = ""
            self.adapter.write(ROBOT_HOME, True)
            self.adapter.write(ROBOT_TASK_COMPLETE, cycle.process)
            cycle.phase = "await_reset"
            events.append(
                HandshakeEvent(
                    self._robot_action(cycle.process),
                    "completed",
                    {
                        "task_number": cycle.process,
                        "occupied": occupied,
                        "site_witness_enabled": site_witness_enabled,
                        "tool_witness_enabled": tool_witness_enabled,
                        **(
                            {"tool_holding": tool_holding}
                            if tool_holding is not None
                            else {}
                        ),
                        **({"position": cycle.position} if cycle.position else {}),
                        **({"sensor": cycle.sensor} if cycle.sensor else {}),
                        **(
                            {"rearmed_sensor": rearmed_sensor} if rearmed_sensor else {}
                        ),
                    },
                )
            )
        elif cycle.phase == "await_reset":
            write_done = bool(self.adapter.read(ROBOT_WRITE_DONE))
            task = int(self.adapter.read(ROBOT_TASK_NUMBER) or 0)
            # Robot_任务写入完成=False 表示 Edge 已经消费完成码。任务号在
            # SKIP_RESET_AFTER_RUN 等配置下可能保留为上一任务，不能用它
            # 阻塞状态机重装填；下一轮仍以 write_done 的新上升沿触发。
            # 连续任务可能在两次轮询之间完成 False -> True，此时
            # 任务号的变化是唯一可见的新周期证据，必须先结束旧周期。
            next_task = (
                write_done
                and task != cycle.process
                and self._robot_task_supported(task)
            )
            if not write_done or next_task:
                previous_task = cycle.process
                self.adapter.write(ROBOT_TASK_COMPLETE, 0)
                events.append(
                    HandshakeEvent(
                        self._robot_action(previous_task),
                        "reset",
                        {
                            "task_number": previous_task,
                            "observed_task_number": task,
                        },
                    )
                )
                if next_task:
                    position, sensor = self._robot_task_position_and_sensor(task)
                    self.adapter.write(ROBOT_WRITE_ALLOWED, False)
                    self.adapter.write(ROBOT_HOME, False)
                    cycle.phase = "executing"
                    cycle.process = task
                    cycle.position = position
                    cycle.sensor = sensor
                    cycle.duration_seconds = self._delay_seconds("robot")
                    cycle.due_at = now + cycle.duration_seconds
                    events.append(
                        HandshakeEvent(
                            self._robot_action(task),
                            "accepted",
                            {
                                "task_number": task,
                                **({"position": position} if position else {}),
                                **({"sensor": sensor} if sensor else {}),
                            },
                        )
                    )
                else:
                    self.adapter.write(ROBOT_WRITE_ALLOWED, True)
                    self.adapter.write(ROBOT_HOME, True)
                    cycle.phase = "idle"
                    cycle.process = 0
                    cycle.position = 0
                    cycle.sensor = ""
        return events

    def _robot_action(self, task: int) -> str:
        if self.workflow == "szlab_material_s06_workflow":
            return MATERIAL_S06_ACTION_BY_TASK[task]
        if self.workflow == S07_MATERIAL_WORKFLOW:
            return MATERIAL_S07_ACTION_BY_TASK[task]
        if self.workflow == "all" and task in ROBOT_ACTION_BY_TASK:
            return ROBOT_ACTION_BY_TASK[task]
        if "robot_standard" in self.enabled_components:
            if self.workflow in SINGLE_SAMPLE_WORKFLOWS and task == 10:
                return SINGLE_SAMPLE_ROBOT_PICK_ACTION
            if self.workflow in SINGLE_SAMPLE_WORKFLOWS and task == 25:
                return SINGLE_SAMPLE_ROBOT_POUR_ACTION
            kind = STANDARD_ROBOT_TASK_KIND[task]
            if kind in {"pick", "place"}:
                return f"szlab_mixer_robot.{kind}"
            return "szlab_mixer_robot.submit_pour_from_s08"
        return ROBOT_ACTION_BY_TASK[task]

    def _step_stirrer(
        self, now: float, position: int | None = None
    ) -> list[HandshakeEvent]:
        """推进指定 S04 位置；设备包模式会在每轮扫描全部六个位置。"""

        position_id = self.position if position is None else int(position)
        cycle = self.stirrers[position_id]
        events: list[HandshakeEvent] = []
        params_name = s04_params_written(position_id)
        process_name = s04_process(position_id)
        if cycle.phase == "idle":
            params_written = bool(self.adapter.read(params_name))
            process = int(self.adapter.read(process_name) or 0)
            if params_written and process in (1, 2, 3):
                self.adapter.write(s04_allow(position_id), False)
                self.adapter.write(s04_status(position_id), 2)
                self.adapter.write(s04_done(position_id), False)
                cycle.phase = "executing"
                cycle.process = process
                cycle.duration_seconds = self._stirrer_duration_seconds(position_id)
                cycle.due_at = now + cycle.duration_seconds
                events.append(
                    HandshakeEvent(
                        self._stirrer_action(),
                        "accepted",
                        {
                            "process": process,
                            "position": position_id,
                            "duration_seconds": cycle.duration_seconds,
                        },
                    )
                )
        elif cycle.phase == "executing" and now >= cycle.due_at:
            self.adapter.write(s04_done(position_id), True)
            self.adapter.write(s04_status(position_id), 1)
            cycle.phase = "await_reset"
            events.append(
                HandshakeEvent(
                    self._stirrer_action(),
                    "completed",
                    {
                        "process": cycle.process,
                        "position": position_id,
                        "duration_seconds": cycle.duration_seconds,
                    },
                )
            )
        elif cycle.phase == "await_reset":
            params_written = bool(self.adapter.read(params_name))
            process = int(self.adapter.read(process_name) or 0)
            if not params_written and process == 0:
                self.adapter.write(s04_done(position_id), False)
                self.adapter.write(s04_allow(position_id), True)
                self.adapter.write(s04_status(position_id), 1)
                events.append(
                    HandshakeEvent(
                        self._stirrer_action(),
                        "reset",
                        {"process": cycle.process, "position": position_id},
                    )
                )
                cycle.phase = "idle"
                cycle.process = 0
        return events

    def _stirrer_action(self) -> str:
        if self.workflow in SINGLE_SAMPLE_WORKFLOWS:
            return SINGLE_SAMPLE_STIR_ACTION
        return S04_STIR_ACTION

    def _step_pump(self, now: float) -> list[HandshakeEvent]:
        cycle = self.pump_cycle
        events: list[HandshakeEvent] = []
        if cycle.phase == "idle":
            params_written = bool(self.adapter.read(S06_PARAMS_WRITTEN))
            process = int(self.adapter.read(S06_PROCESS) or 0)
            if params_written and process in (1, 2, 3):
                self.adapter.write(S06_ALLOW, False)
                self.adapter.write(S06_DONE, False)
                cycle.phase = "executing"
                cycle.process = process
                cycle.duration_seconds = self._delay_seconds("pump")
                cycle.due_at = now + cycle.duration_seconds
                events.append(
                    HandshakeEvent(
                        self._pump_action(),
                        "accepted",
                        {"process": process},
                    )
                )
        elif cycle.phase == "executing" and now >= cycle.due_at:
            self.adapter.write(S06_DONE, True)
            cycle.phase = "await_reset"
            events.append(
                HandshakeEvent(
                    self._pump_action(),
                    "completed",
                    {"process": cycle.process},
                )
            )
        elif cycle.phase == "await_reset":
            params_written = bool(self.adapter.read(S06_PARAMS_WRITTEN))
            process = int(self.adapter.read(S06_PROCESS) or 0)
            # 参数写入标志的下降沿是 PC 已消费完成信号的权威确认。工艺号
            # 允许保留旧值；下一轮仍需 params_written 再次变为 True。
            if not params_written:
                self.adapter.write(S06_DONE, False)
                self.adapter.write(S06_ALLOW, True)
                events.append(
                    HandshakeEvent(
                        self._pump_action(),
                        "reset",
                        {
                            "process": cycle.process,
                            "observed_process": process,
                        },
                    )
                )
                cycle.phase = "idle"
                cycle.process = 0
        return events

    def _pump_action(self) -> str:
        if self.workflow == "szlab_material_s06_workflow":
            return MATERIAL_S06_ADD_ACTION
        if self.workflow in SINGLE_SAMPLE_WORKFLOWS:
            return SINGLE_SAMPLE_PUMP_ACTION
        return S06_PUMP_ACTION

    def _step_s07(self, now: float) -> list[HandshakeEvent]:
        """模拟 S07 扫码、转位和注粉三个工艺的可重复握手。"""

        cycle = self.s07_cycle
        events: list[HandshakeEvent] = []
        process = int(self.adapter.read(S07_PROCESS) or 0)
        params_written = bool(self.adapter.read(S07_PARAMS_WRITTEN))

        if cycle.phase == "idle":
            if params_written and process in S07_PROCESS_LABELS:
                self.adapter.write(S07_ALLOW, False)
                self.adapter.write(S07_DONE, 0)
                cycle.phase = "executing"
                cycle.process = process
                cycle.duration_seconds = self._delay_seconds("s07")
                cycle.due_at = now + cycle.duration_seconds
                events.append(
                    HandshakeEvent(
                        self._s07_action(process),
                        "accepted",
                        {
                            "process": process,
                            "process_label": S07_PROCESS_LABELS[process],
                        },
                    )
                )
        elif cycle.phase == "executing" and now >= cycle.due_at:
            if cycle.process == 3:
                self.adapter.write(S07_BALANCE_READING, self.s07_balance_reading)
            self.adapter.write(S07_DONE, cycle.process)
            cycle.phase = "await_reset"
            events.append(
                HandshakeEvent(
                    self._s07_action(cycle.process),
                    "completed",
                    {
                        "process": cycle.process,
                        "process_label": S07_PROCESS_LABELS[cycle.process],
                    },
                )
            )
        elif cycle.phase == "await_reset":
            if not params_written and process == 0:
                self.adapter.write(S07_DONE, 0)
                self.adapter.write(S07_ALLOW, True)
                events.append(
                    HandshakeEvent(
                        self._s07_action(cycle.process),
                        "reset",
                        {
                            "process": cycle.process,
                            "process_label": S07_PROCESS_LABELS[cycle.process],
                        },
                    )
                )
                cycle.phase = "idle"
                cycle.process = 0
        return events

    def _s07_action(self, process: int) -> str:
        if self.workflow == S07_MATERIAL_WORKFLOW:
            if process == 2:
                return S07_MATERIAL_PREPARE_ACTION
            if process == 3:
                return S07_MATERIAL_DOSE_ACTION
        if self.workflow in SINGLE_SAMPLE_WORKFLOWS and process == 3:
            return SINGLE_SAMPLE_S07_DOSE_ACTION
        return S07_SOLID_ACTION_BY_PROCESS[process]

    def _step_s08(self, now: float) -> list[HandshakeEvent]:
        """模拟 S08 开/关盖工艺，并在 Edge 复位参数后清零完成码。"""

        cycle = self.s08_cycle
        events: list[HandshakeEvent] = []
        process = int(self.adapter.read(S08_PROCESS) or 0)
        params_written = bool(self.adapter.read(S08_PARAMS_WRITTEN))
        cap_storage_slot = int(self.adapter.read(S08_CAP_STORAGE_SLOT) or 0)

        if cycle.phase == "idle":
            if params_written and process in S08_PROCESS_LABELS:
                self.adapter.write(S08_ALLOW, False)
                self.adapter.write(S08_DONE, 0)
                cycle.phase = "executing"
                cycle.process = process
                cycle.position = cap_storage_slot
                cycle.duration_seconds = self._delay_seconds("s08")
                cycle.due_at = now + cycle.duration_seconds
                events.append(
                    HandshakeEvent(
                        self._s08_action(process),
                        "accepted",
                        {
                            "process": process,
                            "process_label": S08_PROCESS_LABELS[process],
                            "cap_storage_slot": cap_storage_slot,
                        },
                    )
                )
        elif cycle.phase == "executing" and now >= cycle.due_at:
            if cycle.position in S08_CAP_STORAGE_SENSOR:
                self.adapter.write(
                    S08_CAP_STORAGE_SENSOR[cycle.position],
                    cycle.process in {1, 3, 5},
                )
            self.adapter.write(S08_DONE, cycle.process)
            cycle.phase = "await_reset"
            events.append(
                HandshakeEvent(
                    self._s08_action(cycle.process),
                    "completed",
                    {
                        "process": cycle.process,
                        "process_label": S08_PROCESS_LABELS[cycle.process],
                        "cap_storage_slot": cycle.position,
                    },
                )
            )
        elif cycle.phase == "await_reset":
            if not params_written and process == 0 and cap_storage_slot == 0:
                self.adapter.write(S08_DONE, 0)
                self.adapter.write(S08_ALLOW, True)
                events.append(
                    HandshakeEvent(
                        self._s08_action(cycle.process),
                        "reset",
                        {
                            "process": cycle.process,
                            "process_label": S08_PROCESS_LABELS[cycle.process],
                            "cap_storage_slot": cycle.position,
                        },
                    )
                )
                cycle.phase = "idle"
                cycle.process = 0
                cycle.position = 0
        return events

    def _s08_action(self, process: int) -> str:
        if self.workflow in SINGLE_SAMPLE_WORKFLOWS:
            if process in {5, 6}:
                return SINGLE_SAMPLE_S08_LIQUID_CAP_ACTION
            if process in {3, 4}:
                return SINGLE_SAMPLE_S08_SAMPLE_CAP_ACTION
        return S08_CAP_ACTION

    def _step_s09(self, now: float) -> list[HandshakeEvent]:
        """模拟 S09 单工艺握手；支持 add_liquid 的 5→7→8→6 连续序列。"""

        cycle = self.s09_cycle
        events: list[HandshakeEvent] = []
        process = int(self.adapter.read(S09_PROCESS) or 0)
        params_written = bool(self.adapter.read(S09_PARAMS_WRITTEN))

        if cycle.phase == "idle":
            # 新驱动会保持参数完成信号，直到工艺完成后统一清零。必须同时
            # 观察到有效工艺号和参数完成，避免把残留工艺号当作新请求。
            if params_written and process in S09_PROCESS_LABELS:
                self.adapter.write(S09_ALLOW, False)
                self.adapter.write(S09_DONE, 0)
                cycle.phase = "executing"
                cycle.process = process
                cycle.duration_seconds = self._delay_seconds("s09")
                cycle.due_at = now + cycle.duration_seconds
                events.append(
                    HandshakeEvent(
                        self._s09_action(),
                        "accepted",
                        {
                            "process": process,
                            "process_label": S09_PROCESS_LABELS[process],
                            "params_written": params_written,
                        },
                    )
                )
        elif cycle.phase == "executing" and now >= cycle.due_at:
            if cycle.process == 8:
                # 可选遥测：驱动完成不等待该点，但仿真仍写入便于观察。
                self.adapter.write(S09_BALANCE_READING, self.s09_balance_reading)
            if cycle.process == 9:
                count = clamp_s09_density_count(self.adapter.read(S09_DENSITY_COUNT))
                for name in s09_density_balance_vars(S09_ASPIRATE_BALANCE_READINGS)[:count]:
                    self.adapter.write(name, self.s09_balance_reading)
                for name in s09_density_balance_vars(S09_DISPENSE_BALANCE_READINGS)[:count]:
                    self.adapter.write(name, self.s09_balance_reading)
            self.adapter.write(S09_DONE, cycle.process)
            cycle.phase = "await_reset"
            cycle.due_at = now + S09_COMPLETION_HOLD_SECONDS
            events.append(
                HandshakeEvent(
                    self._s09_action(),
                    "completed",
                    {
                        "process": cycle.process,
                        "process_label": S09_PROCESS_LABELS[cycle.process],
                    },
                )
            )
        elif cycle.phase == "await_reset":
            # Edge 收到完成码后会把工艺号和参数完成信号一起清零。二者都
            # 复位才允许新一轮。请求仍保持时周期性重发完成边沿，兼容 Edge
            # 对启动等待前已存在完成码的防重逻辑；这不会重复执行物理动作，
            # 也不会重复计入 completed_actions。
            if not params_written and process == 0:
                self.adapter.write(S09_DONE, 0)
                self.adapter.write(S09_ALLOW, True)
                events.append(
                    HandshakeEvent(
                        self._s09_action(),
                        "reset",
                        {
                            "process": cycle.process,
                            "observed_process": process,
                        },
                    )
                )
                cycle.phase = "idle"
                cycle.process = 0
            elif now >= cycle.due_at:
                done = int(self.adapter.read(S09_DONE) or 0)
                if done == cycle.process:
                    self.adapter.write(S09_DONE, 0)
                    cycle.due_at = now + S09_COMPLETION_REARM_SECONDS
                else:
                    self.adapter.write(S09_DONE, cycle.process)
                    cycle.due_at = now + S09_COMPLETION_HOLD_SECONDS
        return events

    def _s09_action(self) -> str:
        if self.workflow in SINGLE_SAMPLE_WORKFLOWS:
            return SINGLE_SAMPLE_S09_ACTION
        return S09_ADD_LIQUID_ACTION

    def _delay_seconds(self, group: str) -> float:
        simulated_seconds = self.delays.get(group, self.process_delay)
        return simulated_seconds / self.time_scale

    def _stirrer_duration_seconds(self, position: int | None = None) -> float:
        """优先采用驱动写入的 S04 毫秒时长，节点缺失时回退配置延时。"""

        position_id = self.position if position is None else int(position)
        try:
            duration_ms = float(self.adapter.read(s04_duration(position_id)))
        except (KeyError, TypeError, ValueError, RuntimeError):
            return self._delay_seconds("stirrer")
        return max(duration_ms, 0.0) / 1000.0 / self.time_scale

    def protocol_snapshot(self) -> dict[str, Any]:
        """返回无需读 OPC UA 的协议周期状态，供 GUI 和诊断持久化。"""

        def cycle_state(cycle: _Cycle) -> dict[str, Any]:
            return {
                "phase": cycle.phase,
                "process": cycle.process,
                "position": cycle.position,
                "sensor": cycle.sensor,
                "duration_seconds": cycle.duration_seconds,
            }

        return {
            "mode": "package" if self.package_mode else "legacy-workflow",
            "scenario": self.workflow,
            "enabled_components": sorted(self.enabled_components),
            "completed_actions": self.completed_actions,
            "cycles": {
                "robot": cycle_state(self.robot),
                "stirrer": {
                    str(position): cycle_state(cycle)
                    for position, cycle in self.stirrers.items()
                },
                "pump": cycle_state(self.pump_cycle),
                "s07": cycle_state(self.s07_cycle),
                "s08": cycle_state(self.s08_cycle),
                "s09": cycle_state(self.s09_cycle),
            },
        }


def _print_catalog(specs: tuple[WorkflowSpec, ...]) -> None:
    print(f"当前工作流数量: {len(specs)}")
    print(f"已支持动作数量: {len(SUPPORTED_ACTIONS)}")
    print()
    for spec in specs:
        print(f"[{spec.workflow_id}]")
        print("  动作:")
        for action in spec.actions:
            supported = (
                " [已支持握手]" if action.split("(")[0] in SUPPORTED_ACTIONS else ""
            )
            print(f"    - {action}{supported}")
        print("  先决条件:")
        for requirement in spec.requirements:
            note = f"；{requirement.note}" if requirement.note else ""
            print(
                f"    - ({requirement.kind}/{requirement.phase}) "
                f"{requirement.subject} {requirement.expectation}{note}"
            )
        print()


def _print_check(specs: tuple[WorkflowSpec, ...], adapter: VariableAdapter) -> bool:
    all_passed = True
    for spec in specs:
        print(f"[{spec.workflow_id}]")
        for requirement in spec.requirements:
            passed, actual = requirement.evaluate(adapter)
            if passed is None:
                marker = "MANUAL"
            elif passed:
                marker = "PASS"
            else:
                marker = "FAIL"
                all_passed = False
            actual_text = "" if passed is None else f"，实际={actual!r}"
            print(
                f"  {marker:6} {requirement.subject} "
                f"{requirement.expectation}{actual_text}"
            )
    return all_passed


def _event_line(event: HandshakeEvent) -> str:
    return json.dumps(
        {
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "action": event.action,
            "phase": event.phase,
            "detail": event.detail,
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def _config_path() -> str:
    return str(Path(__file__).with_name("config") / "szlab_handshake.yaml")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        nargs="?",
        choices=("list", "check", "serve"),
        default="serve",
    )
    parser.add_argument("--config", default=_config_path(), help="YAML 配置文件")
    parser.add_argument(
        "--package-config",
        default=None,
        help="设备包级世界状态与覆盖配置；默认使用内置 config/szlab_package.yaml",
    )
    parser.add_argument(
        "--state-file",
        default=None,
        help="原子写入设备包运行状态 JSON，供 GUI/诊断读取",
    )
    parser.add_argument("--url", default="opc.tcp://127.0.0.1:4855/xuse_sim/")
    parser.add_argument("--node-prefix", default=None)
    parser.add_argument("--username", default=None)
    parser.add_argument("--password", default=None)
    parser.add_argument("--s1-host", default=None, help="S1 HTTP stand-in 监听地址")
    parser.add_argument("--s1-port", type=int, default=None, help="S1 HTTP stand-in 监听端口")
    parser.add_argument(
        "--no-s1-http",
        action="store_true",
        help="不启动设备包自带的 S1 HTTP stand-in",
    )
    parser.add_argument("--position", type=int, default=None, help="S04 位置，1-6")
    parser.add_argument("--pump", type=int, default=None, choices=(1, 2, 3))
    parser.add_argument("--poll-interval", type=float, default=None)
    parser.add_argument("--poll-ms", type=int, default=None, help="轮询间隔（毫秒）")
    parser.add_argument(
        "--process-delay",
        type=float,
        default=None,
        help="无设备时长参数动作的统一延时（秒）",
    )
    parser.add_argument(
        "--delay-ms", type=int, default=None, help="统一动作延时（毫秒）"
    )
    parser.add_argument(
        "--time-scale",
        type=float,
        default=None,
        help="仿真时间倍率，必须大于 0；动作参数时长和配置延时统一生效",
    )
    parser.add_argument(
        "--workflow",
        choices=("all", *WORKFLOW_IDS, *WORKFLOW_ALIASES),
        default=None,
        help="list/check 的工作流过滤器；serve 设备包模式中仅作为兼容初始场景",
    )
    parser.add_argument(
        "--legacy-workflow-mode",
        action="store_true",
        help="恢复旧版只启用所选工作流协议的行为；默认一次启动全部设备协议",
    )
    parser.add_argument(
        "--s06-robot-workflow",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="完整模拟 S06 机器人工作流：初始烧杯传感器为 False，并响应机器人任务号 11/12",
    )
    parser.add_argument(
        "--s09-pipetting-workflow",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="完整模拟 S09 移液工作流：初始化工位/液量，并响应工艺 5、7、8、6",
    )
    parser.add_argument(
        "--s09-remaining-volume-ml",
        type=float,
        default=None,
        help="S09 1-5 号液体瓶的初始余量（mL，默认 100）",
    )
    parser.add_argument(
        "--s07-balance-reading",
        type=float,
        default=None,
        help="S07 注粉完成时写入的模拟天平读数（默认 1.0）",
    )
    parser.add_argument(
        "--s09-balance-reading",
        type=float,
        default=None,
        help="S09 工艺 8 遥测/工艺 9 密度数组写入的模拟天平值（默认 1.0）",
    )
    parser.add_argument(
        "--max-actions",
        type=int,
        default=0,
        help="完成指定数量的交互动作后退出；0 表示持续运行",
    )
    parser.add_argument(
        "--no-initialize",
        action="store_true",
        help="不写入 PLC→PC 仿真初值",
    )
    parser.add_argument(
        "--keep-state-on-exit",
        action="store_true",
        help="退出时不把仿真器负责的 PLC→PC 信号恢复为安全初始值",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = load_yaml(args.config)
    position = (
        args.position if args.position is not None else int(config.get("position", 1))
    )
    pump = args.pump if args.pump is not None else int(config.get("pump", 1))
    requested_workflow = args.workflow or str(config.get("workflow", "all"))
    selected_workflow = WORKFLOW_ALIASES.get(requested_workflow, requested_workflow)
    if selected_workflow not in ("all", *WORKFLOW_IDS):
        print(f"不支持的握手工作流: {requested_workflow}", file=sys.stderr)
        return 2
    package_mode = (
        not args.legacy_workflow_mode
        and str(config.get("mode", "package")).strip().lower() == "package"
    )
    time_scale = (
        float(args.time_scale)
        if args.time_scale is not None
        else float(config.get("time_scale", 1.0))
    )
    if time_scale <= 0:
        print("仿真时间倍率必须大于 0", file=sys.stderr)
        return 2

    if args.delay_ms is not None:
        process_delay = max(args.delay_ms, 0) / 1000.0
        delays: dict[str, float] = {}
    elif args.process_delay is not None:
        process_delay = max(args.process_delay, 0.0)
        delays = {}
    else:
        process_delay = max(float(config.get("delay_ms", 500)), 0.0) / 1000.0
        delay_aliases = {"s04": "stirrer", "s06": "pump"}
        delays = {
            delay_aliases.get(str(key), str(key)): max(float(value), 0.0) / 1000.0
            for key, value in dict(config.get("delays", {})).items()
        }

    if args.poll_ms is not None:
        poll_interval = max(args.poll_ms, 5) / 1000.0
    elif args.poll_interval is not None:
        poll_interval = max(args.poll_interval, 0.005)
    else:
        poll_interval = max(float(config.get("poll_ms", 20)), 5.0) / 1000.0

    def config_bool(cli_value: bool | None, key: str, default: bool) -> bool:
        return bool(config.get(key, default)) if cli_value is None else bool(cli_value)

    s06_robot_workflow = config_bool(
        args.s06_robot_workflow,
        "s06_robot_workflow",
        False,
    )
    s09_pipetting_workflow = config_bool(
        args.s09_pipetting_workflow,
        "s09_pipetting_workflow",
        False,
    )
    s09_remaining_volume_ml = (
        args.s09_remaining_volume_ml
        if args.s09_remaining_volume_ml is not None
        else float(config.get("s09_remaining_volume_ml", 100.0))
    )
    s07_balance_reading = (
        args.s07_balance_reading
        if args.s07_balance_reading is not None
        else float(config.get("s07_balance_reading", 1.0))
    )
    s09_balance_reading = (
        args.s09_balance_reading
        if args.s09_balance_reading is not None
        else float(config.get("s09_balance_reading", 1.0))
    )

    specs = build_workflow_specs(position=position, pump=pump)
    if selected_workflow != "all" and args.command in {"list", "check"}:
        specs = tuple(spec for spec in specs if spec.workflow_id == selected_workflow)
    if args.command == "list":
        _print_catalog(specs)
        return 0

    adapter = OpcUaVariableAdapter(
        args.url,
        args.node_prefix or str(config.get("node_prefix", DEFAULT_NODE_PREFIX)),
        username=args.username or str(config.get("username", "")),
        password=args.password or str(config.get("password", "")),
    )
    print(f"连接 OPC UA: {args.url}")
    adapter.connect()
    print("OPC UA 已连接")
    try:
        if args.command == "check":
            return 0 if _print_check(specs, adapter) else 2

        simulator = WorkflowHandshakeSimulator(
            adapter,
            position=position,
            pump=pump,
            process_delay=process_delay,
            delays=delays,
            initial_values=dict(config.get("initial_values", {})),
            s06_robot_workflow=s06_robot_workflow,
            s09_pipetting_workflow=s09_pipetting_workflow,
            s09_remaining_volume_ml=s09_remaining_volume_ml,
            s07_balance_reading=s07_balance_reading,
            s09_balance_reading=s09_balance_reading,
            workflow=requested_workflow,
            package_mode=package_mode,
            time_scale=time_scale,
        )
        package_runtime = SzlabPackageRuntime(
            config_path=args.package_config or default_package_config_path(),
            scenario=selected_workflow,
            time_scale=time_scale,
        )
        s1_config = dict(config.get("s1_http", {}))
        s1_enabled = (
            package_mode
            and not args.no_s1_http
            and bool(s1_config.get("enabled", True))
        )
        s1_server: S1SimulationServer | None = None
        snapshot_lock = threading.RLock()

        def publish_state() -> None:
            if args.state_file:
                with snapshot_lock:
                    protocol_snapshot = simulator.protocol_snapshot()
                    if s1_server is not None:
                        protocol_snapshot["s1_http"] = s1_server.snapshot()
                    write_snapshot_atomic(
                        args.state_file,
                        package_runtime.snapshot(protocol_snapshot),
                    )

        def observe_s1(
            action: str, phase: str, detail: dict[str, Any]
        ) -> None:
            package_runtime.observe_external(action, phase, detail)
            publish_state()

        if s1_enabled:
            s1_server = S1SimulationServer(
                host=args.s1_host or str(s1_config.get("host", "127.0.0.1")),
                port=(
                    int(args.s1_port)
                    if args.s1_port is not None
                    else int(s1_config.get("port", 8055))
                ),
                event_sink=observe_s1,
            )
            try:
                s1_server.start()
            except OSError as exc:
                print(f"S1 HTTP stand-in 启动失败: {exc}", file=sys.stderr)
                package_runtime.runtime.world.update_device(
                    "s1_workstation",
                    state="failed",
                    error=str(exc),
                )
                package_runtime.stop()
                publish_state()
                return 4
            package_runtime.runtime.world.update_device(
                "s1_workstation",
                state="ready",
                endpoint=s1_server.endpoint,
            )
            print(f"S1 HTTP stand-in 已启动: {s1_server.endpoint}")

        # 即使显式禁止 OPC UA 初始化，GUI 也应立即看到会话身份与覆盖报告。
        publish_state()

        if not args.no_initialize:
            mode_label = "设备包" if package_mode else f"握手场景 {selected_workflow!r}"
            print(f"写入 {mode_label} 的仿真先决条件...")
            simulator.initialize()
            package_runtime.initialize_protocol(simulator.initialization_values())
            publish_state()
            checks = simulator.check_supported_prerequisites()
            failed = [item for item in checks if not item[1]]
            for name, passed, expected, actual in checks:
                print(
                    f"  {'PASS' if passed else 'FAIL'} {name}: "
                    f"expected={expected!r}, actual={actual!r}"
                )
            if failed:
                print("先决条件写入后校验失败，拒绝进入握手循环", file=sys.stderr)
                if s1_server is not None:
                    s1_server.stop()
                package_runtime.stop()
                publish_state()
                return 3

        if package_mode:
            print("SZLab 设备包仿真器已启动；Robot 与 S04-S09 全部常驻，按 Ctrl+C 停止。")
        else:
            print("兼容工作流握手仿真器已启动；按 Ctrl+C 停止。")
        print("S05 为只读完成信号，已保持 S05加工完成=True、S05拍照结果=1。")
        stop_requested = False

        def _request_stop(_signum: int, _frame: Any) -> None:
            nonlocal stop_requested
            stop_requested = True

        previous_sigint = signal.signal(signal.SIGINT, _request_stop)
        previous_sigterm: Any = None
        try:
            previous_sigterm = signal.signal(signal.SIGTERM, _request_stop)
        except (AttributeError, ValueError):
            pass
        try:
            while not stop_requested:
                events = simulator.step()
                for event in events:
                    package_runtime.observe(event)
                    print(_event_line(event), flush=True)
                if events:
                    publish_state()
                if (
                    args.max_actions > 0
                    and simulator.completed_actions >= args.max_actions
                    and simulator.all_cycles_idle()
                ):
                    print(
                        f"已完成 {simulator.completed_actions} 个动作，退出握手循环。"
                    )
                    break
                time.sleep(poll_interval)
        finally:
            signal.signal(signal.SIGINT, previous_sigint)
            if previous_sigterm is not None:
                signal.signal(signal.SIGTERM, previous_sigterm)
            cleanup_on_exit = bool(config.get("cleanup_on_exit", True))
            if (
                not args.keep_state_on_exit
                and cleanup_on_exit
                and not args.no_initialize
            ):
                print("恢复仿真器负责的 PLC→PC 信号...")
                simulator.cleanup()
            if s1_server is not None:
                s1_server.stop()
            package_runtime.stop()
            publish_state()
        return 0
    finally:
        adapter.disconnect()
        print("OPC UA 已断开")


if __name__ == "__main__":
    raise SystemExit(main())
