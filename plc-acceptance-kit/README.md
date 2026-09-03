# SZLab PLC 自动化验收包

本目录是从 `PLC-Sim/main` 建立的首个项目化验收包，以 SZLab 的
`szlab_plc_0810.csv`、正式 OPC UA NodeId 规则和现有 Handshake Agent 为对象。
它实现五件事：

1. L0 对比项目协议、逻辑变量、1,591 个标量节点总数和已选节点类型；
2. 通过正式 OPC UA/HTTP Endpoint 执行覆盖 SZLab 九个设备的清单，包括机器人、
   S04 六工位、S05、S06、S07、S08、S09、PLC 整包状态和 S1 HTTP 状态；
3. 输出 JSON、JUnit XML、HTML 和逐次读写 `timeline.jsonl`，并绑定配置、点表、
   Git 提交及可选 PLC 候选包哈希。
4. 提供安装后自动打开浏览器的单屏 GUI，以及 Windows 10/11 x64 自包含安装包。
5. 对 L3/L4 强制记录现场位置、监护/见证人和安全确认，L4 另行绑定指定物料或批次。

## 使用者安装（不需要 Python）

从仓库 Release 或 `SZLab PLC 自动验收 Windows 安装包` 工作流下载：

`SZLab-PLC-Acceptance-Setup-Windows-x64-v*.exe`

双击安装程序即可安装到当前 Windows 用户目录，不需要管理员权限。安装结束后保持
“启动 SZLab PLC 自动验收”勾选，程序会自动打开本地 GUI；也可从开始菜单或可选的
桌面快捷方式再次启动。

安装后启动“SZLab PLC 自动验收”。程序会自动打开本地 GUI：

1. 保持默认“内置仿真”，点击“运行完整验收”，即可一键执行 L1；
2. 验收结束后直接打开 HTML 报告或下载完整 ZIP 证据包；
3. 连接供应商软 PLC 时，切换到 L2，填写 Endpoint 与 Namespace URI、选择不可变候选包
   并确认受控测试模式；
4. 连接真机时，选择 L3 台架或 L4 FAT/SAT。L3 必填现场位置和监护/见证人，L4 还
   必须填写物料或批次标识。确认后，运行器会直接向该 Endpoint 下发当前清单中的动作。

GUI、Python 运行时、PLC-Sim Server、SZLab 握手代理、点表、用例和报告器均已包含在
安装包中，不需要用户安装 Python、pip、Git、Visual C++ 开发环境或源码。当前 Windows
安装程序没有商业代码签名，首次运行可能出现 SmartScreen 提示；可先用同一 Release 中
的 `SHA256SUMS.txt` 校验下载文件。

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

连接供应商软 PLC 时，选择 `soft-plc` 环境并覆盖 Endpoint，以及
`plc_acceptance/bundles/szlab/mappings/szlab.yaml` 中的 Namespace URI 和节点前缀，
随后显式确认已进入受控测试模式：

```bash
PLC-Sim/.venv/bin/plc-acceptance run \
  --environment soft-plc \
  --endpoint opc.tcp://192.168.1.10:4840/ \
  --namespace-uri urn:xuse:sim \
  --confirm-safe-test-mode \
  --plc-artifact /path/to/immutable-plc-candidate.zip
```

连接真 PLC 台架执行 L3 时使用 `bench` 环境，并记录现场监护和位置：

```bash
PLC-Sim/.venv/bin/plc-acceptance run \
  --environment bench \
  --endpoint opc.tcp://192.168.1.10:4840/ \
  --namespace-uri urn:xuse:sim \
  --confirm-safe-test-mode \
  --plc-artifact /path/to/immutable-plc-candidate.zip \
  --supervisor "供应商张工" \
  --test-location "SZLab 真机台架"
```

完整设备和指定物料的 L4 使用 `fat-sat`，并额外传入物料身份：

```bash
PLC-Sim/.venv/bin/plc-acceptance run \
  --environment fat-sat \
  --endpoint opc.tcp://192.168.1.10:4840/ \
  --namespace-uri urn:xuse:sim \
  --confirm-safe-test-mode \
  --plc-artifact /path/to/immutable-plc-candidate.zip \
  --supervisor "供应商张工 / UniLab 李工" \
  --test-location "SZLab FAT 工位" \
  --material-reference "批次 B-20260903"
```

没有 `--confirm-safe-test-mode` 时，可能产生机器人或磁搅物理效果的用例会返回
`BLOCKED`，不会下发动作。`BLOCKED` 和 `ABORTED` 均不算通过。
非仿真环境没有 `--plc-artifact`（或文件不存在）、安全确认或环境要求的现场字段时，
都会在连接前返回 `BLOCKED`，保证 L2/L3/L4 报告与不可变 PLC 候选包及现场证据绑定。
`--case` 只用于局部诊断；只要还有必跑用例未执行，整次运行会明确返回 `BLOCKED`，
不能用筛选用例取得门禁通过。

## 当前证据边界

- L1 通过只证明 PLC-Sim 正式双进程路径与当前 SZLab 兼容握手相符，不是软 PLC、
  台架或真实硬件验收。
- 九设备矩阵中，S1 当前只在 L1 通过本地 HTTP stand-in 自动执行；其外部服务地址、认证
  和真实机构动作不属于 PLC 候选包。S05 点表没有独立拍照请求节点，当前只能自动验证
  在位、完成和 OK/NG 结果。这两项在覆盖矩阵中保持 `partial`。
- L3/L4 会连接用户填写的真机 Endpoint 并执行当前自动清单。`PASSED` 只表示该清单
  在报告记录的环境中通过；覆盖矩阵中的 `manual / partial / blocked` 项仍须现场关闭，
  不能据此直接宣称完整 FAT/SAT 或功能安全通过。
- `szlab_plc_0810.csv` 当前没有显式故障、初始化、心跳和参数校验错误节点；相关
  R6、R12、HS-C-002、HS-D-001 门禁在
  `plc_acceptance/bundles/szlab/protocol/requirements-coverage.yaml`
  中标记为 `blocked`，没有伪造成通过结果。
- 真 PLC 环境默认校验 OPC UA `AccessLevel`；PLC 输出对测试身份可写会使 CT-001 失败。
- 外部环境可在 GUI 或命令行覆盖 Namespace URI；运行器会在连接后浏览 Namespace Array，
  用实际 Namespace Index 重写冻结 NodeId，并把 Endpoint、URI 与 NodeId 前缀共同绑定到
  `runtime_mapping` 指纹。
- 物理安全、互锁、碰撞、急停和真实完成条件仍由供应商在 L3/L4 提供见证。验收包
  不部署 PLC 程序、不强写 PLC 所有变量，也不旁路任何安全回路。

框架设计、配置接缝、状态与扩展方式见 [FRAMEWORK.md](./FRAMEWORK.md)。

## 构建安装包

GitHub Actions 工作流 `.github/workflows/plc-acceptance-installers.yml` 只为自动化验收包
构建 Windows 10/11 x64 产物。它会在 Windows Runner 上依次执行源码测试、冻结目录
完整 L1 验收、Inno Setup 构建、静默安装、从真实安装目录再次执行完整 L1 验收，并卸载
测试实例。手动触发工作流即可取得安装程序；创建 `plc-acceptance-v0.4.0` 形式的标签会
发布 Windows x64 安装程序和 `SHA256SUMS.txt`。

这里的单平台范围只约束 `plc-acceptance-kit`。PLC-Sim 与 Modbus-Sim 继续遵循各自现有
的多平台交付策略，本工作流不修改或替代它们的发行流程。
