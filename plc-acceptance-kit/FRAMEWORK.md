# PLC 自动化验收框架说明

## 1. 责任边界

当前实现（Current Implementation）的验收运行器属于 PLC-Sim 仓库，只负责从协议配置
解析逻辑变量、经正式 OPC UA 接缝发出测试上位机刺激、验证可观察握手并保存证据。
SZLab 的设备动作、实验室配置和库位（Site）事实仍由 `Uni-Lab-SZLab` 持有；
Uni-Lab OS 仍负责工作流（Workflow）编排、调度器（Scheduler）、执行安全和权威状态。
本包不导入两个兄弟仓库源码，也不创建第二套工作流或库存权威。

本次证据基线为 PLC-Sim `main@b8de3c7`、Uni-Lab-SZLab
`main@640c7bb` 和 SZLab 运行环境实际安装的 `unilabos==0.11.3`。跨仓库只读取
公开配置、部署图和已安装接口作为设计证据；验收运行不扫描或导入兄弟仓库源码。

供应商 PLC/控制器继续负责原子动作内部的机构协作、防干涉、互锁、急停、安全恢复和真实
完成条件。测试上位机只通过已冻结通讯层观察行为，不能进入 PLC 扫描周期控制低层 I/O。

## 2. 结构与数据流

```text
protocol/plc-interface.yaml       逻辑变量、类型、唯一写入方
          │
mappings/szlab.yaml              点表、Namespace URI、NodeId 规则
          │
tests/common + tests/project     只引用逻辑 ID 的刺激和断言
          │
plc_acceptance                   L0 校验 → PREFLIGHT → RUNNING → 报告
          │
          └── 正式 OPC UA Endpoint ── PLC-Sim / 软 PLC / 真 PLC
```

物理 NodeId 只在映射层产生。用例引用 `robot.task_number`、`s041.done` 等逻辑 ID；
运行时先从 CSV 发现中文变量与类型，再按 `node_id_prefix` 解析实际 NodeId。更换同类型
PLC 实例时只替换映射和环境，不修改公共用例。

## 3. 运行状态与门禁

一次运行遵循：

```text
CREATED → L0 → PREFLIGHT → RUNNING → PASSED
             │          ├────────→ FAILED
             └──────────┴────────→ BLOCKED
```

- `FAILED`：已经执行且断言不成立，或清理闭环失败；
- `BLOCKED`：连接、环境、安全前置或必需能力不足，未形成有效通过结论；
- `ABORTED`：人工停止或外部保护触发；
- `PASSED`：本清单要求的用例全部通过，仅代表报告中的环境等级。

P0 任一失败都阻断后续 FAT/SAT。P1 失败必须有双方确认的缺陷、措施和关闭时间；运行器
不会把 P1 自动降级成通过。`--case` 是诊断筛选，不是门禁豁免；任何未运行的必跑用例
都会生成 `MANIFEST = BLOCKED`。

## 4. 当前 SZLab 垂直切片

首版选择三个可以通过实际点表和现有仿真器证明的切片：

| 用例 | 当前自动证据 | 结束状态 |
| --- | --- | --- |
| `CT-001` | 点表可解码、标量总数、选中节点、类型和 NodeId 可解析 | 不改 PLC |
| `CT-002` | 用例只写 `owner: host` 的逻辑变量 | 不改 PLC |
| `HS-A-001` | S03 取烧杯与放回形成两个完整机器人任务闭环 | 源位与夹爪恢复初态 |
| `HS-A-002` | 任务请求保持或重复写 `True` 时不重入 | 源位与夹爪恢复初态 |
| `HS-C-001` | S041 在提交沿锁存 250 ms；执行中把中转值改为 2,000 ms 不改变本轮 | 请求、完成、允许加工复位 |
| `FL-003` | S041 仿真连续 100 轮，每轮都经历新完成与复位 | 无完成位和请求位残留 |

这里的 `Robot_任务写入完成` 和 `S041参数写入完成` 是 SZLab 当前实现（Current
Implementation）的兼容 wire 标识。规范目标仍以《PLC 接入规范》四类握手及唯一变量
所有权为准；不能把兼容名称推广成新的公共模板。

## 5. 安全与证据完整性

1. 仿真门禁用两个独立进程启动现有 `plc-sim server` 与 `plc-sim szlab-handshake`，
   测试仍通过 OPC UA，不调用 Handshake Agent 内部状态机。
2. 非仿真环境中的物理效果用例必须显式传 `--confirm-safe-test-mode`；该开关只记录人工
   已确认安全前置，不旁路 PLC 安全逻辑。
3. 用例只能写协议声明为 `host` 的变量；清理也遵守同一规则，禁止强写 PLC 完成、传感器
   或故障信号来制造通过。
4. 报告记录协议、映射、清单、覆盖表、点表、Git 提交和候选包指纹。配置或候选包变化后
   必须生成新报告；L2/L3 缺少不可变 PLC 候选包时在连接前直接 `BLOCKED`。
5. 不可逆动作、危险物料、加热、压力、碰撞、急停和功能安全仅能在批准的 L3/L4 环境
   运行，并保留供应商人工监护与物理见证。

## 6. 扩展一个项目或用例

新增项目时按以下顺序：

1. 复制并版本化 `plc-interface.yaml`，声明逻辑 ID、中文变量、类型和唯一写入方；
2. 在 `mappings/` 指向供应商签收点表，记录 Namespace URI 和 NodeId 规则；
3. 在 `tests/common/` 复用公共握手，在 `tests/project/` 添加项目动作和串联异常用例；
4. 把用例加入 `test-manifest.yaml`，不能通过删除 P0 用例取得通过；
5. 在 `requirements-coverage.yaml` 为每条自然语言要求记录 `automated / partial /
   manual / blocked / planned` 和真实证据；
6. 先通过 L0 和 L1，再接供应商同源 PLC 程序的 L2；L1 仿真结果不得升级描述为硬件通过。

新增步骤动作应保持小接口。目前 DSL 只有 `write`、`assert`、`wait` 和 `sleep`；复杂故障
注入应通过供应商提供的受控环境适配器实现，不能在公共运行器里硬编码实验室设备常量。

## 7. 已知缺口与下一步

当前点表缺少显式故障、参数校验错误、初始化和心跳节点，因此故障完成闭环、非法参数、
初始化和通讯丢失安全态尚不能自动验收。下一步应由双方先冻结这些公共接口，再补充：

- HS-C-002 非法参数的确定失败回执；
- HS-D-001 初始化闭环；
- ERR-001/ERR-002 故障、超时与安全态；
- RC-001/RC-002/RC-003 上位机、PLC 与 OPC UA Server 重启；
- L2 供应商同源软 PLC，以及 L3 传感器、互锁和真实完成条件见证。
