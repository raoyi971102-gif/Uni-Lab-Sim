# 握手代理

:::{admonition} 阅读角色
- **业务负责人**：确认动作名称、成功标准、超时和禁止伪造的信号。
- **开发人员**：选择代理类型、配置 YAML、启动后做 `list`/`check`。
- **验收人员**：验证完成、拒绝、超时、复位和双写入防护。
:::

握手代理是独立进程。它通过公开 OPC UA 或 HTTP 接口读写 Server 已创建的节点，按设备包协议产生可观察的状态变化。它不是某一条固定业务流程的脚本，也不拥有 Uni-Lab OS 的工作流或库存权威。

先启动 Server，再启动代理。代理不读取 CSV。

## 选择哪一种代理

| 代理 | 命令 | 适用 |
| --- | --- | --- |
| SZLab | `plc-sim szlab-handshake` | Uni-Lab-SZLab 设备包，Robot / S04–S09 / S1 HTTP |
| PTLC V2 | `plc-sim ptlc-handshake` | PTLC L2 通道和 PLC-only 工位动作 |
| XUSE | `plc-sim xuse-handshake` | XUSE 全局握手 |
| `handshake` | 兼容别名 | 等价于 `szlab-handshake` |

一次只启动一个写入代理。GUI 托管代理与命令行代理不要同时针对同一 Endpoint 运行。

## 推荐状态机

一个动作至少要有：可观察的就绪状态、明确的请求边沿、独立的接受或 Busy（如果协议提供）、独立的完成或错误、可重复的复位。

```text
IDLE/READY → ACCEPTED/BUSY → DONE 或 ERROR → RESET/READY
```

代理不得写入 PLC 拥有的完成位、传感器、安全信号或故障位来制造通过；不得把普通完成信号解释成故障；不得用固定 `sleep` 替代可观察握手。未知动作必须返回明确的“不支持”，不能静默忽略。重复请求不得重入。

## SZLab

默认运行设备包模式：一个进程常驻 Robot、S04 六个磁搅位、S05–S09，并启动 S1 HTTP stand-in。工作流不再决定启用哪些处理器。Uni-Lab Edge 仍是 Workflow 的权威执行器。

当前行为快照与 SZLab Catalog 对齐的数量以 `config/szlab_behavior.yaml` 为准。未知动作按 `unsupported` 失败，不会伪造完成信号。

```bash
plc-sim szlab-handshake list --config config/szlab_handshake.yaml
```

只读先决条件检查，不进入动作循环：

```bash
plc-sim szlab-handshake check \
  --url opc.tcp://127.0.0.1:4855/xuse_sim/ \
  --config config/szlab_handshake.yaml \
  --workflow all
```

设备包模式：

```bash
plc-sim szlab-handshake \
  --url opc.tcp://127.0.0.1:4855/xuse_sim/ \
  --config config/szlab_handshake.yaml \
  --package-config config/szlab_package.yaml \
  --state-file data/runtime/szlab-package-state.json \
  --s1-host 127.0.0.1 --s1-port 8055 \
  --time-scale 10
```

S1 本地 stand-in 通常使用 `127.0.0.1:8055`。已有外部 S1 服务时，不要同时启动两个监听者；可加 `--no-s1-http`。

| 配置文件 | 内容 |
| --- | --- |
| `config/szlab_handshake.yaml` | 连接、默认工作流和时序 |
| `config/szlab_package.yaml` | 设备包就绪场景和世界初态 |
| `config/szlab_behavior.yaml` | Catalog 覆盖、动作延时和可观察状态 |

夹爪和库位传感器只模拟物理执行见证，不代替 OS 的库存结算。源库位为空时拒绝取料、目标库位占用时拒绝放料；被拒绝的任务保持 `Robot_Home`，不写完成码。

`--workflow` 在 `list`/`check` 时过滤；`serve` 时只选择兼容初始场景，所有协议仍常驻。`--legacy-workflow-mode` 才恢复按工作流裁剪处理器的旧行为。

更新 Uni-Lab-SZLab 后，在其 Python 3.11 环境中核对快照是否漂移：

```bash
python tools/snapshot_szlab_profile.py /path/to/Uni-Lab-SZLab \
  --behavior config/szlab_behavior.yaml
```

命令非零表示设备、Action 或 Workflow 数量已经变化，发布前必须补齐模型或明确分类。

## PTLC V2

正式架构由 Uni-Lab OS 前端、OS 后端和 PLC-Sim 共同替代 PTLC_UI。OS 后端是工作流和资源锁的唯一真源；PLC-Sim 只维护 OPC UA 节点、L2 信封、PLC 时序、轴、气缸、泵、阀、液位和输入映像。机器人、相机和视觉由独立模块负责。

```bash
plc-sim server --profile ptlc --host 127.0.0.1 --port 4855
plc-sim ptlc-handshake --config config/ptlc_handshake.yaml
plc-sim ptlc-handshake list
```

代理响应 Sampling、Collect、Develop、PhotoScrape、FeedLift、Pump、Rail、StagingA 八个 L2 通道。`config/ptlc_behavior/` 固化各工位合法动作码、步序、错误码和门禁。未知动作按对应派发器错误码 `REJECTED`，不再默认成功。八个 dispatcher 的 55 个合法 PLC 动作全部建模；可用 `list` 查看覆盖率。

物料传感器由 `config/ptlc_handshake.yaml` 的 `plant.sensor_mode` 选择：

| 模式 | 含义 |
| --- | --- |
| `standalone` | 默认一键 PLC 调试。Collect 在延时后自动模拟外部取放 |
| `federated` | OS + 多设备联合仿真。不推断机器人动作，只接受外部幂等现场事件 |

`--sensor-mode standalone|federated` 可覆盖 YAML。独立设备模拟器可通过 `--world-file` 回灌 `feed_count`、`waste_count`、轴回零、`sensors` 和带 `event_id` 的 `events`。这条输入只传递 PLC 能看到的现场事实，不接收工作流或机器人位姿。

故障注入：

```bash
plc-sim ptlc-handshake --time-scale 10 \
  --fault-file data/runtime/ptlc-faults.json \
  --world-file data/runtime/ptlc-world.json \
  --state-file data/runtime/ptlc-state.json
```

GUI 可按工位/动作码注入 `reject`、`error`、`hang`、`interrupt`。命令可立即写出，气缸到位输入按 `plant.cylinder_s` 延迟变化。

能力边界和剩余差距见仓库 `docs/ptlc-plc-simulation-gap-assessment.md`。

## 最小联调步骤

1. `list` 确认代理认识哪些动作。
2. `check` 确认节点先决条件可读。
3. 启动 `serve`，在无硬件副作用的前提下写入测试命令。
4. 观察“命令已接收 → 执行中 → 完成”。
5. 复位并确认回到空闲。
6. 分别模拟拒绝、超时和故障，确认主站明确失败。
7. 重启代理或 Server，确认恢复策略符合设备包规定。

## 常见问题

| 现象 | 检查重点 |
| --- | --- |
| 代理找不到节点 | Server 是否加载同一点表，名称和 Namespace 是否一致 |
| 一直等待 | `check` 输出、就绪位、提交边沿、Busy/完成顺序、是否有第二个写入方 |
| 动作速度不对 | `--time-scale`、`--delay-ms`、`--poll-ms` 只影响仿真 |
| 重启后状态异常 | `--state-file`、上一轮提交边沿、是否先回到安全初始态 |
| S1 冲突 | `8055` 是否已有监听者 |
