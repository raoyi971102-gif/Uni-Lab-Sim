# SZLab PLC 自动化验收包

本目录是从 `PLC-Sim/main` 建立的首个项目化验收包，以 SZLab 的
`szlab_plc_0810.csv`、正式 OPC UA NodeId 规则和现有 Handshake Agent 为对象。
它实现四件事：

1. L0 对比项目协议、逻辑变量、1,591 个标量节点总数和已选节点类型；
2. 通过正式 OPC UA Endpoint 执行机器人边沿闭环、S041 参数锁存和 100 轮复位回归；
3. 输出 JSON、JUnit XML、HTML 和逐次读写 `timeline.jsonl`，并绑定配置、点表、
   Git 提交及可选 PLC 候选包哈希。
4. 提供安装后自动打开浏览器的单屏 GUI，以及 Windows、Linux、macOS 自包含安装包。

## 使用者安装（不需要 Python）

从仓库 Release 或 `SZLab PLC 自动验收安装包` 工作流下载对应系统的产物：

- Windows 10/11 x64：`SZLab-PLC-Acceptance-Setup-Windows-x64-v*.exe`；
- Debian/Ubuntu 22.04+ x64：`SZLab-PLC-Acceptance-Linux-x64-v*.deb`；
- 其他 glibc 2.35+ Linux x64：`SZLab-PLC-Acceptance-Linux-x64-v*.tar.gz`；
- macOS：按处理器选择 `arm64` 或 `x64` 的 DMG。

安装后启动“SZLab PLC 自动验收”。程序会自动打开本地 GUI：

1. 保持默认“内置仿真”，点击“运行完整验收”，即可一键执行 L1；
2. 验收结束后直接打开 HTML 报告或下载完整 ZIP 证据包；
3. 连接供应商软 PLC 时，切换到 L2，填写 Endpoint、选择不可变候选包并确认受控测试模式。

GUI、Python 运行时、PLC-Sim Server、SZLab 握手代理、点表、用例和报告器均已包含在
安装包中，不需要用户安装 Python、pip、Git 或源码。Windows/macOS 产物当前没有商业
代码签名或 Apple 公证，首次运行可能出现系统安全提示。

## 开发环境安装

需要 Python 3.11。开发环境同时安装 PLC-Sim 和验收包：

```bash
cd /path/to/Uni-Lab-Sim
python3.11 -m venv PLC-Sim/.venv
PLC-Sim/.venv/bin/python -m pip install -e './PLC-Sim[test]' -e './plc-acceptance-kit[test]'
```

源码启动 GUI：

```bash
PLC-Sim/.venv/bin/plc-acceptance-gui
```

## 命令行运行

只做 L0 静态检查，不连接 PLC：

```bash
PLC-Sim/.venv/bin/plc-acceptance validate
```

一键启动仓库已有 OPC UA Server 与 SZLab Handshake Agent，并执行 L1 仿真门禁：

```bash
PLC-Sim/.venv/bin/plc-acceptance verify-simulator
```

连接供应商软 PLC 时，复制并修改
`plc_acceptance/bundles/szlab/environments/soft-plc.yaml` 的 Endpoint，以及
`plc_acceptance/bundles/szlab/mappings/szlab.yaml` 中的 Namespace URI 和节点前缀，
随后显式确认已进入受控测试模式：

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
  R6、R12、HS-C-002、HS-D-001 门禁在
  `plc_acceptance/bundles/szlab/protocol/requirements-coverage.yaml`
  中标记为 `blocked`，没有伪造成通过结果。
- 真 PLC 环境默认校验 OPC UA `AccessLevel`；PLC 输出对测试身份可写会使 CT-001 失败。
- 物理安全、互锁、碰撞、急停和真实完成条件仍由供应商在 L3/L4 提供见证。

框架设计、配置接缝、状态与扩展方式见 [FRAMEWORK.md](./FRAMEWORK.md)。

## 构建安装包

GitHub Actions 工作流 `.github/workflows/plc-acceptance-installers.yml` 会在 Windows、
Ubuntu 和 macOS 原生 Runner 上冻结应用，执行完整 L1 冒烟验收，再生成并校验安装包。
手动触发工作流即可取得临时产物；创建 `plc-acceptance-v0.2.0` 形式的标签会同时创建
GitHub Release 和 `SHA256SUMS.txt`。
