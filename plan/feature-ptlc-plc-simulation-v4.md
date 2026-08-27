---
title: PTLC PLC 仿真能力补齐 v4
status: Completed
date: 2026-08-20
owner: PLC-SIM maintainers
---

# Introduction

本计划把 PLC-SIM 的 PTLC 分支建设为 Uni-Lab OS 的 PLC 仿真后端。最终产品由 Uni-Lab OS FE、Uni-Lab OS Backend 与 PLC-SIM 共同替代临时使用的 PTLC_UI 仿真链路；PLC-SIM 只负责 PLC 节点、L2 握手、PLC 控制的执行机构、传感器与过程状态，不实现工作流调度、机器人、相机、视觉或其他直连设备仿真。

实施结果：八工位 55/55 个 dispatcher 合法动作已进入 `PtlcPlant`，握手代理不再保留合法动作的 `unmodeled` 永久运行路径；`PtlcSensorEngine` 已区分输出命令与延迟到位反馈，统一合成 IX8..IX12，并提供 `standalone` 自动补位和 `federated` 幂等外部物料事件。CLI、GUI API 与仿真服务页均可选择模式和提交事件，外部设备模拟器无需进入 PLC-SIM 即可更新 PLC 可见事实。

# 1. Requirements & Constraints

- 以 `codex/ptlc-unilab-domain-v4` 的八工位 PLC 行为规格为当前协议事实快照。
- 八工位 dispatcher 接受的 55 个动作码必须全部进入确定性终态；合法动作不得再以 `unmodeled` 无限运行。
- 保留 `RequestSeq / AcceptedSeq / CompletedSeq`、启动沿、Reset、部署安全态及故障注入语义。
- PLC-SIM 负责轴、气缸、泵、阀、液位、上料/废料升降与 PLC 输入字节等 PLC 侧物理量；外部设备仿真只能通过明确的世界状态输入影响这些量。
- 不复制 PTLC_UI 的工作流 mini-VM、ResourceGate、机器人运动、工具、相机、视觉或 host action 运行时。
- 不虚构未在 PTLC 节点清单中声明的 OPC UA BrowseName；新增诊断信息通过代理快照输出。
- 保持 Python 3.11.x、离线可测试、时间倍率、运行期故障文件和 Windows/macOS/Linux 打包兼容。
- 行为规格随 PLC-SIM 发布，运行时不依赖 pTLC_platformUI 或 Uni-Lab OS 源码仓库。

# 2. Implementation Steps

1. 声明 `PTLC-PLC-BOUNDARY-V4`，固定 OS 编排层、PLC 仿真层与外部设备仿真层的职责和数据流。
2. 建立深模块 `PtlcPlant`：对握手层只暴露动作开始、推进、结束、清理、节点契约和快照接口；其内部封装动作时序、轴段、过程状态、输入合成与动作校验。
3. 从八份 `ptlc.plc_choreography/v1` 快照构造 55 个动作的执行计划，覆盖公开 Step、延时、门禁、错误码、可重试性及完成副作用。
4. 补齐 FeedLift 确定性标定与料架/废料堆模型、Sampling 与 PhotoScrape 轴序、Collect 泵/气缸过程、Develop 缸液位/排液过程、Pump、Rail 与 StagingA。
5. 将 `PtlcHandshakeSimulator` 收窄为 L2 信封状态机，委托 `PtlcPlant` 执行 PLC 物理行为，同时保留故障注入覆盖正常执行的能力。
6. 扩展 CLI/GUI 状态快照，报告 55/55 覆盖率、PLC 世界状态和活动动作；保留现有 OPC UA 启动方式。
7. 添加契约、动作矩阵、时序、门禁、状态副作用、重启/复位及真实 OPC UA 集成测试。
8. 更新 README，说明正式架构、支持范围、外部设备集成边界与迁移路径。
9. 新增传感器仿真层：区分执行器命令与延迟到位反馈，统一合成 IX8..IX12，并记录在途转换。
10. 提供 `standalone` / `federated` 两种物料感知模式；前者服务 PLC 独立调试，后者通过幂等事件与机器人等外部模拟器协作。
11. 补齐收瓶门禁、上料/废料计数、展缸废液传感器以及 GUI 世界事件接口的回归测试。

# 3. Alternatives

- 继续让 PTLC_UI 承担完整仿真：拒绝。它把 UI、工作流执行、机器人和 PLC 仿真绑定在一起，不符合 Uni-Lab OS 的长期架构。
- 把 PTLC_UI 的 mini-VM 整体搬进 PLC-SIM：拒绝。工作流编排属于 Uni-Lab OS Backend，会造成双调度器和状态真源冲突。
- 仅把所有合法动作改成固定延时完成：拒绝。虽然能解除挂起，但无法验证 PLC 输入门禁、Step、轴运动和过程副作用。
- 为每个工作流单独启动握手脚本：拒绝。动作能力应属于设备包级 PLC 仿真，一次启动覆盖全部工作流。

# 4. Dependencies

- `python-opcua`：OPC UA 客户端与节点类型保持。
- `PyYAML`：加载自包含的 PLC 行为规格。
- 现有 `PackageSimulationRuntime`：复用通用时钟、故障和状态快照能力时使用，不承载 PTLC 领域规则。
- `pTLC_platformUI@codex/ptlc-unilab-domain-v4`：仅作为构建期对照真源，不作为运行时依赖。

# 5. Files

- `PLC-Sim/ptlc_plant.py`：新增 PLC 设备仿真深模块。
- `PLC-Sim/ptlc_sensors.py`：PLC 输入、气缸反馈与外部物料事件仿真模块。
- `PLC-Sim/ptlc_runtime.py`：统一 55 个已建模动作及活动执行数据。
- `PLC-Sim/ptlc_handshake_agent.py`：将动作执行委托给 PLC 设备仿真。
- `PLC-Sim/ptlc_agent_cli.py`：覆盖率和设备状态输出。
- `PLC-Sim/config/ptlc_handshake.yaml`：PLC 世界初值、动作时序与 FeedLift 标定。
- `PLC-Sim/config/ptlc_behavior/*`：继续保留八工位行为规格快照。
- `PLC-Sim/tests/test_ptlc_plant.py`：新增设备仿真单元测试。
- `PLC-Sim/tests/test_ptlc_sensors.py`：新增传感器时序、模式与事件测试。
- `PLC-Sim/tests/test_ptlc_handshake_agent.py`：更新全动作与边界测试。
- `README.md`：更新 PTLC 正式架构和使用说明。

# 6. Testing

- 规格覆盖：dispatcher 接受集合必须与 `MODELED_ACTIONS` 完全相等，总数为 55。
- 参数化执行：每个合法动作在无故障、满足门禁的基准世界中完成或按规格返回确定性错误，不得永久等待。
- 动态行为：断言 Step、轴插值、FeedLift 计数/位置、真空、气缸、缸状态与排液状态。
- 协议行为：断言启动沿、序号、终态保持、Start 下降重装、Reset、中断与故障注入。
- 集成行为：在内置 OPC UA 服务上运行握手代理并验证节点读写。
- 回归门：运行 PTLC 聚焦测试后运行仓库全量测试。

最终验证（2026-08-20）：216 项测试收集，214 项通过、2 项按环境跳过；本次触及的 Python 文件通过 Ruff，浏览器脚本通过 `node --check`，Git diff 通过空白检查。

# 7. Risks & Assumptions

- `PTLC-RISK-HARDWARE-PARITY`：行为快照描述的是当前软件契约，不代表真实 PLC/硬件联调已经完成；文档和交付结论必须明确区分。
- `PTLC-RISK-EXTERNAL-SENSORS`：机器人搬运造成的物料传感器变化由外部设备仿真提供；默认世界仅提供可重复的基准状态，不推断机器人动作。
- `PTLC-RISK-BLACKBOX`：CNC 与泵控制器内部协议按 PLC 可观察时序近似，不模拟供应商固件内部算法。
- `PTLC-ASSUMPTION-OS-OWNER`：Uni-Lab OS Backend 是工作流、资源锁与跨设备协调的唯一状态真源。
- `PTLC-ASSUMPTION-PLC-OWNER`：同一 PLC 输出量只由 PLC-SIM 的 `PtlcPlant` 写入，外部模拟器通过输入状态接口协作。

# 8. Related Specifications / Further Reading

- `PLC-Sim/config/ptlc_behavior/*.yaml`：PTLC PLC choreography v1 快照。
- `PLC-Sim/config/ptlc_nodes.yaml`：PLC OPC UA 节点契约。
- `pTLC_platformUI/eit_ptlc/runtime/sim_stack.py`：临时全栈仿真的对照实现，仅用于差距审查。
- `plan/architecture-device-package-simulation-runtime-v1.md`：设备包级仿真运行时的既有架构计划。
