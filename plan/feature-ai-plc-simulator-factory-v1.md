---
goal: Build an AI-assisted factory that converts a PLC project or device variable table into a reviewable PLC simulator package with device handshake profiles
version: 1.0
date_created: 2026-08-21
last_updated: 2026-08-21
owner: PLC-SIM maintainers
status: 'Planned'
tags: [feature, architecture, ai, plc, simulation, handshake, commissioning]
---

# Introduction

![Status: Planned](https://img.shields.io/badge/status-Planned-blue)

本计划把 PLC-SIM 建设为“工站调试验证底座”：使用者输入一个 InoProShop/CODESYS
`.project` 工程，或者一张设备变量表，系统自动抽取证据、识别设备与握手通道、由 AI 补全
动作语义，生成一个可审阅、可验证、可一次启动的 PLC 仿真设备包。设备包包含 OPC UA 节点、
每个设备的握手描述、动作/传感器行为、场景测试、覆盖率和未决问题；Edge 可在真实 PLC 和工站
尚未到位前连接该设备包，提前验证驱动、Action、Workflow 与异常处理。

AI 不直接生成并执行任意 Python。AI 只提交符合 JSON Schema 的 `SimulationSpecPatch`，每个
推断字段必须带证据位置、置信度和假设；确定性编译器合并补丁并生成数据化仿真包，验证器在
启动前阻止冲突、无证据的关键联锁和不完整握手进入“可联调”状态。

第一版自动化预期如下。数值是待基准集验证的产品目标，不是当前已实现能力：

| 输入 | 节点服务器 | 设备/通道识别 | 可运行握手 | 动作与物理行为 | 预期人工工作 |
| --- | --- | --- | --- | --- | --- |
| 完整 `.project`，可读取 POU/ST | 100% 确定性生成 | 目标精确率 ≥90%、召回率 ≥85% | 目标 ≥80% 动作达到可验证 L2/L3 | 显式 Step/门禁可提取；真实时长、物料与外部设备影响需校准 | 确认低置信度映射、补真实时序和外部事实 |
| 仅设备变量表 | 100% 确定性生成 | 遵循已知命名规范时目标精确率 ≥80% | 通用握手模板目标 ≥60%；非标准协议只生成草案 | 变量名无法证明的动作语义不得自动声称完成 | 提供动作清单、成功/失败条件、时序或一段现场轨迹 |

生成成熟度分为五级：`L0 Nodes`、`L1 Channels`、`L2 Handshake`、`L3 Plant`、
`L4 Calibrated`。只有 L2 及以上可供 Edge 动作联调；只有通过动作、复位、超时、故障和传感器
回归的 L3 包可标记为“工站预调试就绪”；L4 必须与真实 PLC 事件轨迹完成差分校准。

## 1. Requirements & Constraints

- **REQ-001**: 构建入口 MUST 接受两种互斥输入：单个 `.project` 工程文件，或单个设备变量表；变量表 V1 MUST 支持 PLC-SIM 现有 CSV/UTF-16 TSV 格式，以及首个工作表符合相同列语义的 `.xlsx`。
- **REQ-002**: 两种输入 MUST 先归一化为 `EvidenceBundle`，再进入推断和生成阶段；后续模块 MUST NOT 读取原始工程或电子表格。
- **REQ-003**: `.project` 输入 MUST 复用 `InoToolkit.open_project()`、`get_project_structure()`、`warm_all_code()` 和现有 GVL/DUT 提取器，收集节点、声明、POU 实现、对象路径、注释和文件哈希。
- **REQ-004**: 变量表输入 MUST 复用 `common.load_csv()` 的编码、分隔符、类型、写入所有权和握手后缀规则，并记录无法映射的列、类型和重复 NodeId。
- **REQ-005**: 归一化模型 MUST 使用 `unilab.plc_evidence/v1` 和 `unilab.plc_simulation_spec/v1` 两个版本化 Schema，并为每个设备、通道、动作、门禁、Step、效果、时长和传感器映射保存 provenance、confidence 和 review_state。
- **REQ-006**: 设备识别 MUST 先使用 NodeId 路径、GVL/DUT 结构、读写所有权和命名规则确定性分组，再允许 AI 对未归组节点提出结构化补丁。
- **REQ-007**: 握手识别 MUST 覆盖当前中文后缀 Type-A/Type-B、PTLC RequestSeq/AcceptedSeq/CompletedSeq L2，以及可配置的 request/ack/busy/done/error/reset 通用通道模板。
- **REQ-008**: AI MUST 只返回 `SimulationSpecPatch`；补丁 MUST 通过 Schema、引用完整性、节点所有权、类型、状态机闭合性和证据引用校验后才能合并。
- **REQ-009**: 生成器 MUST 为每个已识别设备生成独立握手描述，但运行时 MUST 在一个 `GeneratedPackageRuntime` 会话内加载全部设备，禁止恢复成每工作流或每设备一个进程。
- **REQ-010**: 行为 DSL MUST 表达触发沿/序号、参数快照、门禁、写节点、等待、延迟反馈、线性轴段、条件分支、错误、完成副作用、复位和取消；DSL 无法表达的行为 MUST 标记为 unsupported，不得默认成功。
- **REQ-011**: 每个生成包 MUST 包含 manifest、节点清单、设备清单、设备握手、行为、场景、证据索引、审查清单、覆盖率和生成报告，并能在没有原始工程及 AI 服务的机器上离线运行。
- **REQ-012**: 每个动作 MUST 自动生成 happy-path、guard-failure、reset/cancel、timeout/fault 四类场景；缺少必要证据时场景 MUST 失败并把动作成熟度限制在 L1。
- **REQ-013**: GUI MUST 展示提取结果、AI 建议、证据位置、置信度、冲突、未决问题和成熟度；使用者 MUST 能逐项接受、修改或拒绝建议后重新编译。
- **REQ-014**: 构建报告 MUST 分别计算 node_coverage、device_assignment_coverage、channel_coverage、action_coverage、guard_coverage、sensor_coverage、scenario_pass_rate 和 evidence_coverage，禁止只给一个综合百分比。
- **REQ-015**: 生成包 MUST 兼容现有 `PackageSimulationRuntime`、OPC UA Server、时间倍率、故障注入、世界事件和 GUI 生命周期。
- **REQ-016**: 系统 MUST 提供 `plc-sim factory inspect|infer|build|validate|serve|benchmark` 命令，并保证同一输入、同一规范补丁和同一工具版本产生内容哈希相同的包。
- **REQ-017**: 工站预调试验收 MUST 能启动生成包、连接 Edge、枚举设备 Action，并对每个 L2/L3 动作验证 accepted/running/completed 或确定性错误反馈。
- **REQ-018**: 真实 PLC 轨迹可用时，校准器 MUST 接收带时间戳的节点变化 NDJSON，生成仿真/真机差分报告，但 MUST NOT 自动放宽安全门禁。
- **REQ-019**: `SimulationSpec` MUST 表达 PLC stopped/starting/running/faulted/emergency-stopped 全局状态、回零、轴通信/故障、报警复位和共享执行器所有权；L3 动作 MUST 受这些状态门禁。
- **REQ-020**: 延迟执行器/传感器转换 MUST 归属于具体 action_run，reset、interrupt、急停时 MUST 可取消，持久化后 MUST 可恢复或确定性取消，禁止重启后产生幽灵反馈。
- **REQ-021**: 行为 DSL MUST 提供气缸、泵/阀/罐体连续量和轴运动的标准模型；缺少物理参数时 MUST 使用明确近似并降低成熟度，不得把固定延时包装成 L4。
- **REQ-022**: L4 可选通信模型 MUST 支持会话断开、Bad quality、写入拒绝、订阅延迟和重连场景，用于 Edge 韧性测试；该模型不得改变 L0-L3 的默认确定性行为。
- **SEC-001**: 工程提取 MUST 是只读流程；工厂命令 MUST NOT 调用 `save_project()`、`set_pou_code()`、`download_program()` 或任何在线 PLC 操作。
- **SEC-002**: PLC 代码、注释、变量名和表格内容 MUST 视为不可信数据；它们不得改变系统提示、调用工具、读取环境变量或指定输出路径。
- **SEC-003**: AI Adapter MUST 只接收脱敏后的 `EvidenceBundle` 子集；发送外部模型 MUST 显式启用，凭证不得写入生成包、报告或日志。
- **SEC-004**: 生成包 MUST 通过路径穿越、重复 NodeId、PLC/Host 双写、任意代码、无界循环、负时长和资源耗尽校验后才允许启动。
- **CON-001**: PLC-SIM 运行时继续只支持 Python 3.11.x。
- **CON-002**: 二进制 InoProShop `.project` 的完整提取只在已安装兼容 InoProShop 与 MCP 的 Windows 主机可用；macOS/Linux 只能消费变量表、导出的 `EvidenceBundle` 或已生成设备包。
- **CON-003**: 工作流 DAG、业务资源锁、机器人轨迹、相机、视觉和其他直连设备仿真不进入生成包；它们只通过世界事件提供 PLC 可观察事实。
- **CON-004**: 无来源的物理时长、传感器因果关系、液体过程和安全联锁不得由 AI 以高置信度虚构。
- **CON-005**: 生成产物默认写入 `runtime_data_dir()/factory/<build_id>`，不得修改输入工程；只有显式 export 才复制到用户指定目录。
- **CON-006**: V1 MUST 先覆盖 OPC UA Boolean/整数/浮点/String/数组和现有握手协议，不要求模拟供应商 PLC 扫描抖动、机器人运动学或视觉结果。
- **GUD-001**: 生成器、运行时和传输层使用三个独立深模块；工厂外部 interface 只暴露 `inspect`、`infer`、`build`、`validate` 和 `benchmark` 请求/结果。
- **GUD-002**: 确定性证据优先于 AI 建议，人工确认优先于两者；合并顺序固定为 `extracted < inferred < reviewed`，每次覆盖均写入 provenance。
- **GUD-003**: 缺失信息通过机器可读 `ReviewQuestion` 输出，不通过日志警告后继续猜测。
- **GUD-004**: 设备包的 interface 是 OPC UA 节点、握手时序、世界事件和诊断快照；测试 MUST 通过这些 interface 断言结果，不测试生成器或运行时私有状态。
- **GUD-005**: 任何自动化比例都必须由版本化 benchmark corpus 计算，不以单个成功案例作为产品声明。
- **PAT-001**: 在 `SimulationFactory` seam 注入 `ProjectEvidenceAdapter` 和 `TableEvidenceAdapter`，两者生成相同 `EvidenceBundle`。
- **PAT-002**: 在 AI seam 定义 `InferencePort`；生产使用无 shell 的 JSON stdin/stdout `CommandInferenceAdapter`，测试使用固定输出 `InMemoryInferenceAdapter`。
- **PAT-003**: 使用 data-over-code：AI 生成版本化规范，`SimulationPackageCompiler` 确定性地产生 YAML/JSON，`GeneratedPackageRuntime` 解释执行，V1 禁止 AI 生成 Python hook。
- **PAT-004**: 延续 `PtlcPlant + PtlcSensorEngine` 的职责分离，将动作/执行器规则与外部事实/延迟传感器投影分别放在 `PlantRuntime` 和 `SensorRuntime` 内部 seam。

## 2. Implementation Steps

### Implementation Phase 1

- GOAL-001: 固定证据模型、仿真规范、成熟度和 benchmark；完成标准是 PTLC 与 SZLab 两个基准包均可表示为 Schema 合法的 golden spec，且指标计算可重复。

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-001 | 新增 `PLC-Sim/simulation_factory/schemas/plc-evidence-v1.schema.json`，定义 source、hashes、nodes、objects、POUs、declarations、implementations、diagnostics 和 provenance；为每个数组设置显式数量上限。依赖：无。 |  |  |
| TASK-002 | 新增 `PLC-Sim/simulation_factory/schemas/simulation-spec-v1.schema.json`，定义 package、controller、devices、channels、actions、resources、plant、sensors、world_events、scenarios、assumptions 和 maturity；枚举所有 DSL operation。依赖：TASK-001。 |  |  |
| TASK-003 | 新增 `PLC-Sim/simulation_factory/models.py`，使用 Pydantic 2 实现 `BuildRequest`、`EvidenceBundle`、`SimulationSpec`、`SimulationSpecPatch`、`ReviewQuestion`、`BuildReport` 和 `BuildResult`，并禁止未知字段。依赖：TASK-001、TASK-002。 |  |  |
| TASK-004 | 新增 `PLC-Sim/tests/fixtures/simulation_factory/`，保存 PTLC 与 SZLab 的最小脱敏 evidence、人工审阅 golden spec 和预期指标；不得保存私有完整工程。依赖：TASK-003。 |  |  |
| TASK-005 | 新增 `PLC-Sim/simulation_factory/metrics.py`，实现八项覆盖率、L0-L4 成熟度和 precision/recall 计算；对空分母返回 `not_applicable` 而非 100%。依赖：TASK-003、TASK-004。 |  |  |

### Implementation Phase 2

- GOAL-002: 建立两种只读输入 Adapter；完成标准是工程输入与变量表输入产生同 Schema 的稳定 `EvidenceBundle`，重复构建哈希一致，且不调用任何工程写操作。

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-006 | 新增 `PLC-Sim/simulation_factory/project_input.py` 的 `ProjectEvidenceAdapter.inspect(path)`；调用 `InoToolkit.open_project()`、`get_project_structure()`、`warm_all_code()`，复用 `parse_warm_dump()`、`extract_gvl_variables()` 和 DUT 展开逻辑，输出 POU/ST、变量、注释、符号所有权和对象路径。依赖：TASK-003。 |  |  |
| TASK-007 | 在 `ProjectEvidenceAdapter` 中增加只读调用审计：允许的方法白名单固定为 open/get/probe/extract，遇到 save/set/create/delete/download 名称立即失败；报告工程 SHA-256、InoProShop profile 和提取器版本。依赖：TASK-006。 |  |  |
| TASK-008 | 新增 `PLC-Sim/simulation_factory/table_input.py` 的 `TableEvidenceAdapter.inspect(path, column_map)`；CSV/TSV 复用 `common.load_csv()`，XLSX 只读首个非空工作表并映射 Name/EnglishName/NodeType/DataType/NodeLanguage/NodeId/Comment/Owner。依赖：TASK-003。 |  |  |
| TASK-009 | 新增 `PLC-Sim/simulation_factory/normalize.py`，统一 IEC/OPC UA 类型、路径、数组索引、中文/英文名称、Host/PLC/maintenance 所有权；重复 NodeId、同名异型和无法解析类型输出阻断性 `ReviewQuestion`。依赖：TASK-006、TASK-008。 |  |  |
| TASK-010 | 扩展 `PLC-Sim/pyproject.toml`：注册 `plc_sim.simulation_factory` 包和 Schema package-data；把 XLSX 读取库放入 `factory` optional dependency，核心安装在不导入 XLSX 时不得要求该依赖。依赖：TASK-008。 |  |  |

### Implementation Phase 3

- GOAL-003: 从证据确定性生成设备、通道和动作草案；完成标准是所有映射均有 provenance，无证据项均出现在审查清单且不会被标记为 L2。

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-011 | 新增 `PLC-Sim/simulation_factory/rules.py`，把 `common.parse_suffix()`、位置节点规则、PTLC L2 命名和可配置通用握手模板收敛为 `RuleInferenceEngine.infer(evidence)`。依赖：TASK-009。 |  |  |
| TASK-012 | 实现设备分组：按 GVL/DUT/结构路径优先，NodeId 公共前缀次之，中文后缀与编号最后；跨组共享节点必须声明 resource_owner，不得复制。依赖：TASK-011。 |  |  |
| TASK-013 | 实现 ST 静态证据扫描：解析赋值、CASE/IF 条件、定时器调用、Step 变量、完成/错误/复位写入和 POU 调用关系；V1 使用保守 tokenizer 与引用图，无法证明的控制流输出 unknown，不实现完整 IEC 编译器。依赖：TASK-006、TASK-011。 |  |  |
| TASK-014 | 新增 `PLC-Sim/simulation_factory/draft.py` 的 `build_draft(evidence)`，合并规则和 ST 扫描结果，生成设备/通道/动作草案、冲突列表和按影响排序的 `ReviewQuestion`。依赖：TASK-012、TASK-013。 |  |  |

### Implementation Phase 4

- GOAL-004: 接入受控 AI 语义补全；完成标准是 AI 只能产生 Schema 合法补丁，任一建议可追溯到证据，关闭 AI 时确定性流程仍可完成 L0/L1 构建。

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-015 | 新增 `PLC-Sim/simulation_factory/inference.py` 的 `InferencePort.infer(InferenceRequest) -> SimulationSpecPatch`、`CommandInferenceAdapter` 和 `InMemoryInferenceAdapter`；生产 Adapter 使用参数数组启动子进程，不经过 shell。依赖：TASK-003。 |  |  |
| TASK-016 | 新增 `PLC-Sim/simulation_factory/prompts/behavior-inference-v1.md`，规定 AI 只处理一个设备/通道分片，只引用 evidence_id，不生成路径、命令或 Python，不确定项必须返回 `review_questions`。依赖：TASK-014、TASK-015。 |  |  |
| TASK-017 | 新增 `PLC-Sim/simulation_factory/patches.py`，实现 `extracted < inferred < reviewed` 三层合并、字段级 provenance、置信度阈值和冲突保留；安全门禁、错误码、写入所有权和 reset 语义不得仅凭低置信度建议自动接受。依赖：TASK-015、TASK-016。 |  |  |
| TASK-018 | 实现 AI 分片与预算：每次请求最多一个设备、200 个节点或 80 KiB 文本；先发送调用图切片，再按 review question 精确补充证据；任何 PLC 注释中的指令文本按普通字符串转义。依赖：TASK-015。 |  |  |
| TASK-019 | 新增 `PLC-Sim/tests/test_factory_inference.py`，覆盖 prompt injection、非法字段、虚构 evidence_id、路径注入、冲突补丁、超限输入、Adapter 超时及无 AI 降级。依赖：TASK-015、TASK-018。 |  |  |

### Implementation Phase 5

- GOAL-005: 将审阅后的规范编译为离线设备包；完成标准是相同输入生成相同文件哈希，生成包不包含可执行代码且能被独立 validator 完整校验。

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-020 | 新增 `PLC-Sim/simulation_factory/compiler.py` 的 `SimulationPackageCompiler.compile(spec, destination)`，原子生成 `manifest.yaml`、`nodes.yaml`、`devices.yaml`、`handshakes/*.yaml`、`behaviors/*.yaml`、`scenarios/*.yaml`、`evidence.json`、`review.yaml` 和 `coverage.json`。依赖：TASK-017。 |  |  |
| TASK-021 | 新增 `PLC-Sim/simulation_factory/validator.py`，验证 Schema、NodeId/类型、所有权、引用、状态机终态、reset 路径、DSL 有界性、时长范围、共享执行器仲裁、覆盖率和成熟度门。依赖：TASK-020。 |  |  |
| TASK-022 | 新增 `PLC-Sim/simulation_factory/fingerprint.py`，对归一化 spec、生成器版本和模板版本计算 build_id；排除时间戳和绝对路径，保证可重复构建。依赖：TASK-020。 |  |  |
| TASK-023 | 新增 `PLC-Sim/simulation_factory/export.py`，只允许把已验证包复制到显式目标目录；拒绝目标为仓库根、用户主目录、文件系统根或输入工程目录。依赖：TASK-021、TASK-022。 |  |  |
| TASK-041 | 新增 `PLC-Sim/simulation_factory/factory.py` 的深模块 `SimulationFactory`；构造函数接收 Project/Table/Inference Adapter，对外只暴露 `inspect()`、`infer()`、`build()`、`validate()` 和 `benchmark()`，并为每次调用返回结构化结果而非直接启动进程。依赖：TASK-014、TASK-017、TASK-021、TASK-023。 |  |  |

### Implementation Phase 6

- GOAL-006: 用通用运行时执行生成包及每设备握手；完成标准是一个进程同时托管全部设备，L2/L3 动作均产生确定性反馈，未知动作失败关闭。

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-024 | 新增 `PLC-Sim/generated_package.py` 的 `GeneratedPackageRuntime.load(package_path, adapter, clock)`，复用 `PackageSimulationRuntime` 管理会话、并发动作、事件、世界状态和覆盖率。依赖：TASK-021。 |  |  |
| TASK-025 | 新增 `PLC-Sim/behavior_dsl.py` 的 `BehaviorExecutor.start/tick/reset/snapshot`，实现 REQ-010 的有界 operation；把全局 PLC/急停/轴状态与动作执行封装在 `PlantRuntime`，把 action-owned 延迟反馈和输入投影封装在 `SensorRuntime`，并实现共享执行器仲裁与可恢复快照。依赖：TASK-024。 |  |  |
| TASK-026 | 新增 `PLC-Sim/generic_handshake_agent.py`，从 `handshakes/*.yaml` 构造每设备 Handler，支持 edge-trigger、level-trigger、sequence-envelope、参数锁存、terminal hold 和 reset；全部 Handler 共用一个 package runtime。依赖：TASK-024、TASK-025。 |  |  |
| TASK-027 | 扩展 `PLC-Sim/server.py` 以加载生成包 `nodes.yaml`；扩展 `PLC-Sim/cli.py` 和 `PLC-Sim/gui/agent_routes.py` 以启动 `generic-handshake --package <path>`。依赖：TASK-026。 |  |  |
| TASK-028 | 将时间倍率、fault file、world file、state file 和外部 material/site/tool 事件接入 `GeneratedPackageRuntime`；故障类型覆盖急停、轴/通信故障、传感器 stuck/bounce、执行器 timeout 和 OPC UA 会话故障，并在快照中输出活动门禁、等待传感器和预计反馈时间。依赖：TASK-024、TASK-027。 |  |  |

### Implementation Phase 7

- GOAL-007: 建立自动验证与真机校准；完成标准是每个生成动作都有可运行场景，L3 发布门要求全部必需场景通过，L4 报告可量化仿真与真机差异。

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-029 | 新增 `PLC-Sim/scenario_runner.py`，通过 OPC UA interface 执行生成场景，采集请求、Step、节点变化、终态、耗时和诊断，不直接访问运行时内部状态。依赖：TASK-027、TASK-028。 |  |  |
| TASK-030 | 在编译阶段为每个动作生成 happy/guard/reset/timeout 场景，并为 L3 包生成急停、传感器 stuck、进程重启和共享资源竞争场景；若 evidence 无法构造前置世界，则生成阻断性 review question 而非伪造通过场景。依赖：TASK-020、TASK-029。 |  |  |
| TASK-031 | 新增 `PLC-Sim/simulation_factory/trace_diff.py`，读取真实 PLC NDJSON 和仿真事件，按节点/动作对齐，报告终态一致率、Step 差异、时序 P50/P95 偏差和未解释事件。依赖：TASK-029。 |  |  |
| TASK-032 | 新增 `PLC-Sim/tests/test_generated_package_runtime.py` 与 `test_factory_scenarios.py`，使用内存 Adapter 验证并发设备、共享执行器、复位、进程状态恢复、故障注入和所有生成场景。依赖：TASK-028、TASK-030。 |  |  |

### Implementation Phase 8

- GOAL-008: 在 GUI 提供可审阅构建流程；完成标准是用户可以从输入到 L2/L3 包全程操作，且任何阻断项都有证据和明确修复入口。

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-033 | 新增 `PLC-Sim/gui/factory_routes.py`，提供 `POST /api/factory/inspect`、`infer`、`review`、`build`、`validate`、`benchmark` 和 `serve`，以及 `GET /api/factory/builds/{build_id}`；所有入口调用 `SimulationFactory`，长任务使用 build_id 查询状态，禁止请求内后台失联任务。依赖：TASK-019、TASK-032、TASK-041。 |  |  |
| TASK-034 | 新增 `PLC-Sim/gui/static/factory.js` 并扩展 `index.html`/样式，提供输入选择、设备树、通道/动作矩阵、证据侧栏、置信度筛选、问题表单、成熟度和覆盖率报告。依赖：TASK-033。 |  |  |
| TASK-035 | 扩展 `PLC-Sim/gui/backend_state.py`，保存当前 build_id、阶段、进度、错误和产物路径；GUI 重启后从运行目录恢复，但不自动重启 AI 任务。依赖：TASK-033。 |  |  |
| TASK-036 | 更新 `PLC-Sim/README.md` 和根 `README.md`，记录两种输入、自动化边界、Windows 工程提取限制、成熟度、数据隐私、CLI/GUI 流程和 Edge 联调方法。依赖：TASK-034、TASK-035。 |  |  |

### Implementation Phase 9

- GOAL-009: 用 PTLC、SZLab 和未知第三个工站完成试点；完成标准是 benchmark 报告达到已声明指标，至少一个生成包由 Edge 完成全动作联调，未达标指标在发布说明中降级而非豁免。

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-037 | 新增 `PLC-Sim/simulation_factory/benchmark.py`，分别以 project-evidence、table-only evidence 运行 PTLC/SZLab golden corpus，输出精确率、召回率、覆盖率、成熟度和人工问题数量。依赖：TASK-005、TASK-021、TASK-032。 |  |  |
| TASK-038 | 选择一个未参与规则开发的第三工站进行盲测；保存脱敏 evidence、人工修订 diff、总耗时和失败分类，禁止在评分前把该工站专用名称加入规则。依赖：TASK-037。 |  |  |
| TASK-039 | 用 Uni-Lab Edge 对一个 L3 设备包执行设备发现、所有 Action、至少一个 Workflow、reset、故障和超时场景；把结果写入版本化验收报告。依赖：TASK-036、TASK-038。 |  |  |
| TASK-040 | 在 `.github/workflows/ci.yml` 增加 factory Schema、golden build、可重复哈希、生成场景和 benchmark 回归门；指标下降超过 3 个百分点或出现新阻断问题时失败。依赖：TASK-037、TASK-039。 |  |  |

## 3. Alternatives

- **ALT-001**: 让 AI 直接读取工程后生成 Python 握手脚本。拒绝：不可重复、难审计、容易引入任意代码和每项目分叉，无法形成稳定验证底座。
- **ALT-002**: 只增强现有变量名正则并完全不用 AI。拒绝：可以生成节点和标准握手，但无法从 POU 控制流、注释和跨变量关系恢复动作语义，非标准工程覆盖率过低。
- **ALT-003**: 为每个设备生成独立进程。拒绝：共享 PLC 状态、执行器和传感器会形成多写者；每设备握手应是一个设备包会话内的 Handler。
- **ALT-004**: 在 PLC-SIM 内复制 Uni-Lab 工作流引擎和机器人模拟器。拒绝：会产生双调度器和职责漂移；PLC-SIM 只模拟 PLC 可观察行为。
- **ALT-005**: 要求所有项目先人工编写完整行为 YAML。拒绝：准确但无法达到自动化目标；人工应只处理证据不足和校准项。

## 4. Dependencies

- **DEP-001**: 现有 `PLC-Sim/ino_mcp/` 与兼容 InoProShop 安装提供 `.project` 只读结构、声明和 POU/ST 提取。
- **DEP-002**: 现有 `PLC-Sim/common.py`、`server.py`、`package_simulation.py` 和 OPC UA VariableAdapter 提供节点加载及设备包运行基础。
- **DEP-003**: Pydantic 2 与 PyYAML 继续承担模型和 YAML 校验；JSON Schema 文件随 Python 包分发。
- **DEP-004**: XLSX 输入使用仅在 `factory` extra 中安装的只读解析库；CSV/TSV 路径不得依赖该库。
- **DEP-005**: AI 由用户配置的 JSON stdin/stdout 命令提供；无 AI Adapter 时系统仍须完成 inspect、deterministic draft、build L0/L1 和 validate。
- **DEP-006**: PTLC、SZLab 脱敏 golden evidence 和第三工站盲测数据必须获得相应仓库/项目的数据使用授权。
- **DEP-007**: Uni-Lab Edge 提供最终设备发现、Action 与 Workflow 联调验收环境，但不作为 PLC-SIM 单元测试依赖。

## 5. Files

- **FILE-001**: `PLC-Sim/simulation_factory/models.py` 定义工厂外部 interface 与版本化模型。
- **FILE-002**: `PLC-Sim/simulation_factory/schemas/*.schema.json` 定义 evidence、spec、patch 和 package Schema。
- **FILE-003**: `PLC-Sim/simulation_factory/project_input.py` 实现 `.project` 只读 Adapter。
- **FILE-004**: `PLC-Sim/simulation_factory/table_input.py` 实现 CSV/TSV/XLSX 变量表 Adapter。
- **FILE-005**: `PLC-Sim/simulation_factory/normalize.py` 统一节点、类型、路径和所有权。
- **FILE-006**: `PLC-Sim/simulation_factory/rules.py` 实现确定性设备/握手识别。
- **FILE-007**: `PLC-Sim/simulation_factory/draft.py` 生成初始 SimulationSpec 和审查问题。
- **FILE-008**: `PLC-Sim/simulation_factory/inference.py` 定义 AI seam 及两个 Adapter。
- **FILE-009**: `PLC-Sim/simulation_factory/patches.py` 校验并合并 AI/人工补丁。
- **FILE-010**: `PLC-Sim/simulation_factory/compiler.py` 与 `validator.py` 生成并验证设备包。
- **FILE-011**: `PLC-Sim/simulation_factory/metrics.py` 与 `benchmark.py` 计算自动化效果。
- **FILE-012**: `PLC-Sim/simulation_factory/trace_diff.py` 实现真机轨迹差分。
- **FILE-013**: `PLC-Sim/generated_package.py` 加载并托管完整生成设备包。
- **FILE-014**: `PLC-Sim/behavior_dsl.py` 解释动作、执行器和传感器 DSL。
- **FILE-015**: `PLC-Sim/generic_handshake_agent.py` 构造设备 Handler 并驱动 OPC UA 握手。
- **FILE-016**: `PLC-Sim/scenario_runner.py` 从 OPC UA interface 执行生成场景。
- **FILE-017**: `PLC-Sim/gui/factory_routes.py` 提供构建与审查路由。
- **FILE-018**: `PLC-Sim/gui/static/factory.js` 提供工厂 UI 交互。
- **FILE-019**: `PLC-Sim/pyproject.toml` 注册包、Schema、可选 XLSX 依赖与 CLI。
- **FILE-020**: `PLC-Sim/tests/fixtures/simulation_factory/` 保存脱敏 benchmark corpus。
- **FILE-021**: `PLC-Sim/tests/test_factory_*.py`、`test_generated_package_runtime.py` 和 `test_factory_scenarios.py` 验证工厂与运行时。
- **FILE-022**: `PLC-Sim/README.md` 与根 `README.md` 记录产品使用和能力边界。
- **FILE-023**: `PLC-Sim/simulation_factory/factory.py` 是证据抽取、推断、编译和验证的统一深模块。

## 6. Testing

- **TEST-001**: Schema round-trip：所有 golden evidence/spec/patch/package 必须加载、序列化并重新加载为相同内容；未知字段必须失败。
- **TEST-002**: Project Adapter 只读审计：Fake MCP 记录调用，断言无 save/set/create/delete/download/online 调用。
- **TEST-003**: Table Adapter 参数化测试：UTF-8 CSV、UTF-16 TSV、XLSX、中文列、数组、重复 NodeId、未知类型和列映射。
- **TEST-004**: 确定性推断 golden test：PTLC/SZLab 的设备、通道、动作和所有权结果与人工 spec 比较并输出 precision/recall。
- **TEST-005**: AI 安全测试：prompt injection、恶意路径、伪造证据、越权字段、巨大输出、超时、进程失败和敏感信息脱敏。
- **TEST-006**: 可重复构建测试：相同 evidence/spec/tool version 连续构建两次，除运行目录外所有文件字节及 build_id 完全一致。
- **TEST-007**: Validator 负向测试：双写、无终态、无 reset、无界等待、负时长、未定义节点、共享执行器冲突和任意代码字段均拒绝。
- **TEST-008**: Runtime interface 测试：一个会话并行运行不同设备、串行化共享执行器，并验证 accepted/running/completed/error/reset、传感器延迟和快照。
- **TEST-009**: 场景覆盖测试：每个 L2/L3 动作具备 happy/guard/reset/timeout；L3 额外具备急停、传感器 stuck、进程重启和共享资源竞争场景，必需场景通过率必须为 100%。
- **TEST-010**: OPC UA 集成测试：生成包节点可被 Edge 风格客户端读写，Host/PLC 所有权、类型、数组和终态保持符合 spec。
- **TEST-011**: 轨迹差分测试：已知真机/仿真 fixture 产生稳定的终态、Step、时序和未解释事件指标。
- **TEST-012**: GUI 测试：构建阶段恢复、建议接受/拒绝、阻断问题、成熟度、启动生成包和错误呈现。
- **TEST-013**: 全量回归：从 `PLC-Sim` 运行 `python -m pytest -q`、Ruff、前端 `node --check` 和 `git diff --check`，要求零失败。
- **TEST-014**: Edge 验收：一个生成 L3 包连续完成全部设备 Action、一个跨设备 Workflow、reset、故障和超时，无需修改生成代码。

## 7. Risks & Assumptions

- **RISK-001**: 二进制工程可提取性依赖 InoProShop 版本和 MCP；缓解方式是固定 profile、记录工具版本并允许导出 EvidenceBundle 后跨平台继续。
- **RISK-002**: 变量表只描述“有什么变量”，通常不描述“为什么变化”；系统可能达到 L0/L1 但无法可靠达到 L3，报告必须明确缺失证据而不是夸大 AI 能力。
- **RISK-003**: ST 静态扫描无法完整处理指针、动态数组、第三方库、复杂 FB 内部状态和厂商扩展；无法证明的路径必须降级为 unknown。
- **RISK-004**: AI 可能产生看似合理但错误的安全联锁或物理因果；关键字段要求证据、人工审阅和场景验证三者共同通过。
- **RISK-005**: PTLC/SZLab 规则过拟合会抬高内部 benchmark；必须保留未知第三工站盲测并报告规则专用命中率。
- **RISK-006**: 生成握手可以让 Edge 流程跑通，但错误的传感器初态会掩盖真实门禁问题；L3 必须包含 guard-failure 与 world-state 场景。
- **RISK-007**: 外部 AI 可能带来 PLC 源码泄露；默认禁用外发，使用显式配置、证据切片、脱敏和审计记录。
- **RISK-008**: DSL 逐步扩张可能变成第二套 PLC 语言；只加入至少两个设备包需要的 operation，单项目特殊逻辑保持 unsupported 或要求人工建模。
- **ASSUMPTION-001**: 输入工程或变量表的使用者有权将其中内容用于仿真生成和可选 AI 分析。
- **ASSUMPTION-002**: OPC UA NodeId、数据类型和写入所有权是 Edge 联调的最低可信契约。
- **ASSUMPTION-003**: 完整 `.project` 中可读取的 POU/ST 比变量表提供更多动作证据，因此两种输入的自动化承诺必须分别统计。
- **ASSUMPTION-004**: Uni-Lab OS Backend 仍是 Workflow、资源锁和跨设备协调的唯一状态真源。
- **ASSUMPTION-005**: 每设备握手描述可以共享一个 PLC 设备包世界状态和一个进程生命周期。

## 8. Related Specifications / Further Reading

[PTLC PLC 仿真能力评估](../docs/ptlc-plc-simulation-gap-assessment.md)

[设备包级仿真运行时计划](./architecture-device-package-simulation-runtime-v1.md)

[PTLC PLC 仿真实施记录](./feature-ptlc-plc-simulation-v4.md)

[InoProShop 工程提取器](../PLC-Sim/ino_mcp/extractor.py)

[设备包仿真运行时](../PLC-Sim/package_simulation.py)
