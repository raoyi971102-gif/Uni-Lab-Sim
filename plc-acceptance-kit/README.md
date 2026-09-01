# SZLab PLC 自动化验收包

本目录是从 `PLC-Sim/main` 建立的首个项目化验收包，以 SZLab 的
`szlab_plc_0810.csv`、正式 OPC UA NodeId 规则和现有 Handshake Agent 为对象。
它实现三件事：

1. L0 对比项目协议、逻辑变量、1,591 个标量节点总数和已选节点类型；
2. 通过正式 OPC UA Endpoint 执行机器人边沿闭环、S041 参数锁存和 100 轮复位回归；
3. 输出 JSON、JUnit XML、HTML 和逐次读写 `timeline.jsonl`，并绑定配置、点表、
   Git 提交及可选 PLC 候选包哈希。

## 安装

需要 Python 3.11。开发环境同时安装 PLC-Sim 和验收包：

```bash
cd /path/to/Uni-Lab-Sim
python3.11 -m venv PLC-Sim/.venv
PLC-Sim/.venv/bin/python -m pip install -e './PLC-Sim[test]' -e './plc-acceptance-kit[test]'
```

## 运行

只做 L0 静态检查，不连接 PLC：

```bash
PLC-Sim/.venv/bin/plc-acceptance validate
```

一键启动仓库已有 OPC UA Server 与 SZLab Handshake Agent，并执行 L1 仿真门禁：

```bash
PLC-Sim/.venv/bin/plc-acceptance verify-simulator
```

连接供应商软 PLC 时，复制并修改 `environments/soft-plc.yaml` 的 Endpoint，以及
`mappings/szlab.yaml` 中的 CSV 路径、Namespace URI 和节点前缀，随后显式确认已进入
受控测试模式：

```bash
PLC-Sim/.venv/bin/plc-acceptance run \
  --environment soft-plc \
  --endpoint opc.tcp://192.168.1.10:4840/ \
  --confirm-safe-test-mode \
  --plc-artifact /path/to/immutable-plc-candidate.zip
```

没有 `--confirm-safe-test-mode` 时，可能产生机器人或磁搅物理效果的用例会返回
`BLOCKED`，不会下发动作。`BLOCKED` 和 `ABORTED` 均不算通过。
非仿真环境没有 `--plc-artifact`（或文件不存在）时也会在连接前返回 `BLOCKED`，
保证 L2/L3 报告与不可变 PLC 候选包绑定。
`--case` 只用于局部诊断；只要还有必跑用例未执行，整次运行会明确返回 `BLOCKED`，
不能用筛选用例取得门禁通过。

## 当前证据边界

- L1 通过只证明 PLC-Sim 正式双进程路径与当前 SZLab 兼容握手相符，不是软 PLC、
  台架或真实硬件验收。
- `szlab_plc_0810.csv` 当前没有显式故障、初始化、心跳和参数校验错误节点；相关
  R6、R12、HS-C-002、HS-D-001 门禁在 `protocol/requirements-coverage.yaml`
  中标记为 `blocked`，没有伪造成通过结果。
- 真 PLC 环境默认校验 OPC UA `AccessLevel`；PLC 输出对测试身份可写会使 CT-001 失败。
- 物理安全、互锁、碰撞、急停和真实完成条件仍由供应商在 L3/L4 提供见证。

框架设计、配置接缝、状态与扩展方式见 [FRAMEWORK.md](./FRAMEWORK.md)。
