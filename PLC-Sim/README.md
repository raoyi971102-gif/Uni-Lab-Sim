# PLC公共包命名空间与旧命令兼容

此增量只新增可安装的 `unilab-plc-sim` 分发包、`plc_sim` 模块及 `plc-sim` 命令。版本 `0.2.6` 对应明确锁定的兼容运行时 `unilab-opcua-sim==0.2.6`，不表示尚未迁入的公共时钟、契约、设备模型已经发布。

原 `OpcUaSim/` 源码、`opcua_sim` 导入、`opcua-sim` 命令及其版本保持不变。新CLI只委托原CLI；没有复制或双重映射设备/GUI模块。仅 `import plc_sim` 不加载旧产品；调用兼容命令才加载 `opcua_sim.cli`。

当前委托范围：默认GUI、`gui`、`server`、`szlab-handshake`（旧别名`handshake`）、可选的`ino`。行为、错误与退出码仍归原产品；`plc-sim --version`明确输出兼容运行时身份，`--help`标明下面显示的是旧CLI。未添加PTLC、Modbus或新仿真能力。

Python支持沿用原产品的 `>=3.11,<3.12`。从同一仓分别构建并安装 `OpcUaSim/` 与 `PLC-Sim/` 的wheel；后者显式依赖前者，不依赖源码路径或editable映射。旧GUI安装器/一键脚本本组不变，也不授予新桌面安装器资格。

来源映射见 `migration/package-identity.json`。后续公共模块按功能加入本目录；不经兼容CLI隐式重导出旧设备模块。

## 独立公共核心

`plc_sim.model_loading` 提供显式模型入口及来源锁核对；`plc_sim.cosimulation`
提供原确认步进协调器；`plc_sim.physics_mailbox` 提供私有目录跨进程确认传输。
三者只依赖标准库及 `unilabos-sim-contracts==0.1.0`，导入不加载旧 OPC UA 产品、
设备包、GUI 或 SDK。安装时需提供 OS 的 `packages/simulation-contracts` wheel；
既有 CLI 兼容依赖仍保留，命令范围没有新增。

基础来自 #27 的合同消费者与原协调器；随后 #31 切片加入停止请求、墙钟健康、
确认来源屏障、轨迹事件及消费同一 session 的节拍宿主。纯一阶计算参考不持有物料或时钟。
不包含设备行为、物料世界、恢复或本次实际 SDK 验收。测试从已安装 wheel 导入公共 API。
详见 docs/clock-health-trace.md、docs/source-barrier.md 和 docs/session-host.md。

## 公共信号装配

`plc-sim signals --help` 是本包的新入口，其他命令仍委托原 OPC UA 产品。
工厂必须显式接受 `source=ModelSource` 并返回 `SignalAssembly`；CLI 校验提交、
来源锁和实际文件，原会话负责推进，端点拥有者决定每个信号的写权限。
`--steps` 用于有界执行；不指定时按原会话目标节拍等待。退出关闭本装配端点。

`reference_signals.build_reference` 是显式 DI 参考 API，要求调用方指定 mode 和
endpoint；它并非能直接传给 CLI 的 source-only 工厂。参考 CSV 随 wheel 安装。
当前公共切片覆盖信号类型、来源、质量/TTL、会话所有权及回环端点；S06 目录、
原驱动及规范物料效果接线属于后续设备/OS 功能组。本次没有重跑真实 Isaac，
也未授予 S06 物理资格。`tools/verify_signals.py` 核对已安装 wheel 后运行回归。
