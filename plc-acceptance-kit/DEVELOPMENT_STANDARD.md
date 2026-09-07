# 自动化验收包开发规范

版本：0.1（候选规范）  
适用包：`plc-acceptance-kit`  适用对象：SZLab 及其他通过公开 PLC/HTTP 接口交付的领域设备

本文是自动化验收包的开发合同，不是 PLC 控制程序规范，也不是 Uni-Lab-OS 设备驱动规范。
文中的“必须”“禁止”“应”“可以”分别表示强制要求、不可接受做法、默认要求和可选做法。

## 1. 依据与术语

本规范参照以下已存在的当前实现（Current Implementation）和已接受约定：

- `Uni-Lab-OS/docs/developer_guide/add_device.md`：设备类、状态（Topic）、动作（Action）、
  `ResourceSlot`/`DeviceSlot`、类型注解、动作结果和测试要求；
- `Uni-Lab-OS/docs/developer_guide/add_registry.md`：Registry schema、动作映射、初始化参数、
  自动发现和验证方式；
- `Uni-Lab-OS/docs/developer_guide/add_PLC.md`：OPC UA 点表、数据类型、NodeId、订阅/缓存、
  握手、复位和 PLC 接入清单；
- `Uni-Lab-SZLab/docs/CONFORMANCE.md`：根单包、静态发现无副作用、Graph 权威、资产闭包和
  workspace/clean-wheel 一致性；
- `Uni-Lab-SZLab/docs/SZLab设备包拆分打包与Edge通信总结.md`：设备包与 OS 的边界、单 PLC 多业务
  设备、公开 Action/Workflow 接缝和分层验证；
- `Uni-Lab-SZLab/docs/HARDWARE_BRINGUP.md`：真机安全、联锁、重启、物料和人工见证要求。

本文使用以下中文优先术语：

| 术语 | 定义 |
|---|---|
| 设备包（Device Package） | 由领域仓库持有设备、资源、动作、工作流和资产的可安装分发包。 |
| 自动化验收包（Acceptance Bundle） | 由验收仓库持有协议、映射、测试清单、环境门禁、运行器和证据报告的可安装包。 |
| 逻辑变量（Logical Node） | 用例稳定引用的变量 ID，例如 `s041.done`；不包含物理 NodeId。 |
| 点表（Point Table） | 供应商导出的 CSV，是中文变量名、数据类型、NodeId 和访问属性的接口事实。 |
| 映射（Runtime Mapping） | 将逻辑变量解析为当前 Endpoint/Namespace/NodeId 的版本化配置。 |
| 工作流（Workflow） | Uni-Lab-OS 中的可复用流程定义；验收包只验证其可观察设备接缝，不拥有工作流权威。 |
| 库位（Site）/物料（Material） | 由 Uni-Lab-OS 或 SZLab 设备包拥有；验收包只能验证公开见证，不建立第二套库存真相。 |
| 证据等级（Evidence Level） | L0 静态、L1 协议仿真、L2 供应商软 PLC、L3 台架、L4 FAT/SAT。 |

## 2. 责任边界

### 2.1 仓库所有权

| 行为或事实 | 唯一拥有者 | 自动验收包的角色 |
|---|---|---|
| 通用 Registry、PackageCatalog、调度器（Scheduler）、执行安全和公共通信接口 | Uni-Lab-OS | 使用已发布接口，不复制 OS 内部实现。 |
| SZLab 设备类、动作、资源、工作流、PLC 业务语义、模型和协议事实 | `Uni-Lab-SZLab` | 根据公开接口设计用例；不导入 SZLab 源码或实例化其内部类。 |
| PLC 程序、扫描周期、互锁、急停、机构动作和真实完成条件 | PLC/设备供应商 | 提供同源候选包、点表、故障注入和人工见证。 |
| 逻辑变量合同、测试清单、环境门禁、运行器和报告格式 | 自动化验收包 | 只拥有“如何观察和判定”，不拥有设备行为。 |
| OPC UA/HTTP 仿真服务 | PLC-Sim 或项目指定仿真器 | 作为已标记的仿真证据源，不等同于软 PLC 或真机证据。 |

禁止通过源码路径导入兄弟仓库、修改 `sys.path`、复制兄弟仓库实现、调用握手代理内部函数，
或在验收包内建立替代的动作、工作流、物料/库位状态机。跨仓库只允许通过已安装分发包、进程、
OPC UA、HTTP、CLI 或其他版本化公开接口连接。

### 2.2 权威与证据

每个验收事实必须能回答五个问题：

1. 中文身份（Identity）：逻辑变量、用例、点表和运行的稳定 ID 是什么？
2. 中文权威（Authority）：哪个仓库或 PLC 组件可以改变它？
3. 中文生命周期（Lifecycle）：何时开始、完成、复位和安全结束？
4. 中文持久事实（Durable Fact）：重启、断线或重放后仍可追溯的证据是什么？
5. 中文失败语义（Failure Semantics）：是 `FAILED`、`BLOCKED`、`ABORTED`、人工项还是未知？

验收包不得把仿真通过、局部用例通过或人工未提供的见证，描述成更高等级的真机通过。

## 3. 仓库布局合同

自动验收包应保持一个可安装的根包；以 SZLab bundle 为例：

```text
plc-acceptance-kit/
├── pyproject.toml                         # distribution、依赖、CLI、package-data
├── plc_acceptance/
│   ├── bundles/<project>/
│   │   ├── protocol/plc-interface.yaml    # 逻辑变量合同
│   │   ├── protocol/test-manifest.yaml    # 必跑清单和环境适用范围
│   │   ├── protocol/requirements-coverage.yaml
│   │   ├── mappings/<project>.yaml        # CSV、Namespace URI、NodeId 前缀
│   │   ├── environments/*.yaml            # L1-L4 环境、门禁和重复次数
│   │   ├── tests/common/*.yaml            # 可复用握手
│   │   ├── tests/devices/*.yaml           # 设备/工位用例
│   │   └── tests/project/*.yaml           # 项目串联和异常用例
│   ├── runner.py                          # 唯一运行路径
│   ├── validator.py                       # L0 静态门禁
│   ├── reporting.py                       # JSON/JUnit/HTML/timeline 证据
│   └── simulator.py                       # 通过公开进程启动仿真
├── tests_py/                              # 运行器、配置、报告和真实接缝测试
├── packaging/                             # Windows 冻结、安装和产物校验
├── reports/.gitkeep                       # 运行输出目录，不提交运行数据
├── README.md                              # 使用者安装和运行说明
├── FRAMEWORK.md                           # 架构与当前证据边界
└── DEVELOPMENT_STANDARD.md                # 本开发规范
```

设备、资源、工作流和模型的源文件仍放在领域设备包；验收包只保存验收所需的逻辑合同、映射和
证据规则。运行日志、数据库、凭据、现场标定和候选 PLC 二进制不得进入 Git 或 wheel。

## 4. 配置分层和单一事实来源

### 4.1 协议合同 `plc-interface.yaml`

每个节点至少声明：

```yaml
protocol_version: "<major>.<minor>"
project_id: szlab-poly-studio-plc
nodes:
  - id: s041.done                 # 稳定逻辑 ID
    name: S041工艺完成             # 点表中文名
    data_type: BOOLEAN             # 规范化类型
    owner: plc                     # 唯一写入方：host 或 plc
    required: true
    description: "..."
```

规则：

- 用例只能引用逻辑 ID；禁止在用例 YAML、Python 测试或 GUI 中散写 NodeId、中文点表名或
  Namespace index。
- `owner` 必须唯一。`owner: host` 才允许验收包写入；`owner: plc` 只能读取和等待。
- `data_type` 必须与点表、OPC UA Variant 和供应商合同一致；不能为了写入方便转换成宽泛类型。
- 同一逻辑 ID 在一个 bundle 中只能声明一次；修改语义必须提升 `protocol_version` 并保留兼容说明。

### 4.2 映射合同 `mappings/*.yaml` 与 CSV 点表

映射文件至少声明 `csv_path`、`namespace_uri`、`node_id_prefix` 和 `expected_scalar_nodes`。
CSV 必须来自 PLC 工程或 OPC UA Server 导出，至少包含变量名、英文别名、节点类型、数据类型、
语言和 NodeId。NodeId 不得由验收包猜测或拼接。

映射层负责：

1. 读取 CSV，确认节点名称、类型、NodeId 和标量总数；
2. 将逻辑 ID 映射到点表中文名；
3. 在连接后发现真实 Namespace Array，并生成当前运行的 `runtime_mapping` 指纹；
4. 校验 AccessLevel、读写权限和所有权；
5. 将 Endpoint、URI、前缀、CSV 摘要和候选 PLC 摘要写入报告。

更换供应商实例只能改环境或映射，不得复制一份用例并把物理 NodeId 填进去。

### 4.3 清单和覆盖合同

`test-manifest.yaml` 登记每条用例的稳定 ID、是否必跑、安全等级和适用环境。`required: true`
的用例不得通过 `--case` 筛选绕过；未执行的必跑用例必须生成 `MANIFEST = BLOCKED`。

`requirements-coverage.yaml` 对每条需求记录：

```text
automated | partial | manual | blocked | planned
```

每个 `partial`、`manual`、`blocked` 项必须写原因、当前可观察接口、补齐所需的供应商输入和
关闭条件。缺少故障、初始化、心跳或非法参数节点时必须保持 `blocked`，不能伪造失败信号或
把普通完成信号当成故障证据。

## 5. 用例开发合同

### 5.1 用例结构

一条用例必须包含稳定 `id`、名称、`level`、`safety_level`、适用 `environments`、是否有物理
效果、`given`、`when` 和 `cleanup`。步骤只使用受支持的 DSL：`write`、`assert`、
`assert_greater`、`wait`、`sleep` 和声明服务根地址下的 `http`。

```yaml
- id: HS-C-001
  name: S041 参数在提交边沿整体锁存并完成复位
  level: L1
  safety_level: P0
  environments: [simulator, soft_plc, bench, fat_sat]
  physical_effect: true
  given:
    - {action: assert, node: s041.allow, equals: true}
  when:
    - {action: write, node: s041.process, value: 1}
    - {action: write, node: s041.duration_ms, value: 250}
    - {action: write, node: s041.params_committed, value: true}
    - {action: wait, node: s041.status, equals: 2, timeout_ms: 2000}
    - {action: write, node: s041.duration_ms, value: 2000}
    - {action: wait, node: s041.done, equals: true, timeout_ms: 2000}
  cleanup:
    - {action: write, node: s041.params_committed, value: false}
    - {action: write, node: s041.process, value: 0}
    - {action: wait, node: s041.allow, equals: true, timeout_ms: 2000}
```

### 5.2 握手规则

验收用例遵循 Uni-Lab-OS 的 PLC 握手范式：

- 脉冲触发：写入 `True`，观察独立完成信号，再复位为 `False`；
- 参数下发：先等待 PLC 请求，写入完整参数，等待“已接收/Busy”，再等待完成；
- 编号任务：写入任务编号，逐阶段等待对应完成信号，不合并不同阶段的完成位。

必须遵守：

1. 先复位上一轮残留，再开始本轮；
2. 参数提交后，必须等待 PLC 明确进入已接收/Busy 状态，再修改任何中转缓存；禁止用固定
   `sleep(50)` 代替可观察握手；
3. 开始、完成、允许加工、参数已接收必须有独立语义；不能用同一节点同时表示多个阶段；
4. 等待必须有超时；超时要报告逻辑 ID、最后读值、已耗时和清理结果；
5. `cleanup` 必须在成功、失败、异常和人工停止后执行；清理失败时整条用例为 `FAILED`；
6. 不得写 PLC 拥有的完成、传感器、故障或安全信号来制造通过；
7. 不得调用 Handshake Agent、设备类或 PLC 驱动的内部方法绕过 OPC UA/HTTP 接缝。

### 5.3 设备覆盖

每个实际设备至少需要：

- 可接单/就绪或状态可观察用例；
- 一个代表性动作闭环（开始、执行、完成、复位）；
- 物理效果存在时的 P0 安全前置和清理见证；
- 失败、超时、重启或非法参数用例，若接口不存在则在覆盖表标记 `blocked`；
- 设备动作与相关 Material/Site 结果可观察时的物料见证。

SZLab 当前矩阵覆盖 Robot、S04、S05、S06、S07、S08、S09、主 PLC 和 S1 HTTP 服务。S1 外部
服务认证/真实地址未冻结、S05 点表缺少独立拍照请求时，必须保持 `partial`，不能仅凭仿真结果
宣称完整设备验收。

用例应按设备能力编排，而不是只复制某一条工作流。工作流串联用例可以补充业务路径，但不能
取代设备级动作覆盖。

## 6. 证据等级和门禁

| 等级 | 目标 | 必须使用的真实接缝 | 可以证明 | 不能证明 |
|---|---|---|---|---|
| L0 | 静态合同 | 不连接 PLC | 协议、点表、类型、清单、所有权和配置自洽 | 任何动作完成 |
| L1 | 协议仿真 | 独立 PLC-Sim Server + 握手代理进程，经 OPC UA/HTTP | 跨进程协议与用例逻辑闭环 | 供应商软 PLC、机构安全、真机功能 |
| L2 | 供应商软 PLC | 同源 PLC 程序/候选包，经公开 Endpoint | PLC 程序对当前点表、握手和动作的兼容性 | 真实 I/O、电机、互锁和材料实物 |
| L3 | 台架 | 受控现场真机、监护人、位置、安全确认 | 台架接口、动作、互锁和人工见证范围内的事实 | 未覆盖的 FAT/SAT、完整生产能力 |
| L4 | FAT/SAT | 指定现场、批次/物料、供应商和 Uni-Lab 见证 | 报告清单内的现场验收证据 | 清单外需求和未关闭人工项 |

等级只能逐级取得，低等级通过不能自动升级。L2/L3/L4 必须在建立 OPC UA 会话前完成前置
校验：不可变 PLC 候选包、Endpoint、Namespace URI、安全确认；L3 还要有现场位置和监护/见证人，
L4 还要有物料或批次标识。物理效果用例在未获环境许可时必须 `BLOCKED`。

## 7. 状态、失败与报告

运行状态固定为：

```text
CREATED → L0 → PREFLIGHT → RUNNING → PASSED
                     │          ├── FAILED
                     └──────────┴── BLOCKED
                                      └── ABORTED（人工停止/保护触发）
```

- `PASSED`：本环境必跑清单全部通过，不代表整台设备或整条产线无缺陷；
- `FAILED`：已执行且断言失败，或清理闭环失败；
- `BLOCKED`：缺少连接、候选包、安全前置、现场证据或接口能力，未形成通过结论；
- `ABORTED`：人工停止、急停或外部保护触发，不能当作通过。

报告至少包含：运行 ID、开始/结束时间、证据等级、环境、协议/映射/清单/覆盖文件摘要、
Git 提交、候选 PLC SHA-256、Endpoint、Namespace URI、runtime mapping 指纹、每条用例的迭代
和状态、错误/最后值、清理结果，以及逐次 `timeline.jsonl`。报告输出至少支持 JSON、JUnit
XML、HTML 和完整证据 ZIP。

时间线中的每次 `connect`、`read`、`write`、`wait` 和 `http_request` 必须绑定逻辑 ID 或
服务逻辑 ID；不得把原始凭据、密码、Token 或完整敏感 payload 写入报告。

## 8. 开发、测试与交付门禁

新增项目或设备按以下顺序实现：

1. 先提交协议版本、逻辑变量、类型和唯一写入方；
2. 提交供应商点表和映射，运行 L0；
3. 编写公共握手和设备用例，加入 manifest 与 coverage；
4. 为每个动作补充配置解析、DSL、清理和报告单元测试；
5. 用独立进程经 OPC UA/HTTP 跑 L1；禁止内存假 PLC 作为唯一 E2E 证据；
6. 构建 clean wheel，验证安装包内 YAML、CSV、静态资源和版本一致；
7. 在 Windows x64 上冻结 GUI，运行冻结目录 L1、静默安装 L1、卸载和哈希校验；
8. 获得供应商同源 PLC 候选包后运行 L2，再按安全评审进入 L3/L4；
9. 把日志、报告、人工见证和未关闭项归档到外部证据库，不把现场运行数据提交到仓库。

每次提交至少通过：

```bash
python -m pytest plc-acceptance-kit/tests_py
python -m pytest PLC-Sim/tests
python -m plc_acceptance.cli validate
```

Windows 安装包还必须通过 GitHub Actions 的源码回归、PyInstaller 冻结 L1、Inno Setup、
静默安装运行、卸载和 `SHA256SUMS.txt`。自动验收包当前只提供 Windows 安装交付；PLC-Sim 和
Modbus-Sim 的多平台交付策略不受本规范改变。

## 9. 版本、变更和兼容

- 协议语义、逻辑 ID、所有权、数据类型和清单 ID 是公共合同；删除或改义必须提升主版本并
  提供迁移说明；
- 增加可选节点或非破坏性诊断用例可以提升次版本；
- 点表 NodeId、Namespace、服务根地址或候选 PLC 变化必须更新映射版本并重新生成报告；
- 同一用例 ID 的判定语义不能静默改变；需要新语义时新建 ID，旧 ID 保留兼容或明确废弃；
- 仿真 profile 只能模拟公开协议，不得成为真实 PLC 行为的新权威；
- 每次发布要记录验收包版本、PLC-Sim 版本、SZLab 协议版本、点表摘要和运行器版本。

## 10. 合入检查表

### 合同和边界

- [ ] 逻辑 ID、数据类型、唯一写入方和协议版本已登记；
- [ ] 用例不包含物理 NodeId、Namespace index、硬编码现场地址或凭据；
- [ ] 没有兄弟仓库源码导入、内部函数调用或第二套物料/库位权威；
- [ ] `partial/manual/blocked` 项有原因和关闭条件。

### 用例和安全

- [ ] 每个设备至少有状态/就绪、代表性动作、清理和物料见证；
- [ ] 所有写操作符合 owner，所有等待有超时；
- [ ] 参数提交后等待可观察“已接收/Busy”，不依赖固定短休眠；
- [ ] 成功、失败、异常和中止都会执行清理；
- [ ] P0/P1、物理效果、L3/L4 现场证据要求已登记。

### 交付和证据

- [ ] L0、L1 通过正式跨进程 OPC UA/HTTP 接缝；
- [ ] clean wheel 与 workspace 发现结果一致；
- [ ] Windows 冻结目录和真实安装目录都跑过完整 L1；
- [ ] 报告含版本、指纹、时间线、清理结果和未关闭项；
- [ ] 安装包、哈希和发布说明可由使用者独立下载和验证。

## 11. 当前实现与后续目标

当前实现（Current Implementation）：SZLab bundle 已具备 L0/L1、L2/L3/L4 前置门禁、九设备
矩阵、JSON/JUnit/HTML/timeline 证据、GUI 和 Windows 安装流水线。当前点表仍缺显式故障、
初始化、心跳、非法参数和通讯丢失节点，相关覆盖应保持 `blocked`，由供应商先冻结公开接口。

目标设计（Target Design）：补齐故障/恢复/重启/安全态和真实机构见证后，才能把相应 coverage
项从 `blocked/partial/manual` 变为 `automated`，并形成完整 L3/L4 FAT/SAT 证据。目标设计不应
在缺少 PLC 接口或现场批准时提前写成“已支持”。

