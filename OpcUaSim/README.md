# OpcUaSim

一个由 CSV 变量表驱动的 OPC UA 仿真环境，包含：

- OPC UA Server：按 CSV 创建节点，默认监听 `opc.tcp://0.0.0.0:4855/xuse_sim/`
- Handshake Agent：仿真 Type A/B/C/D 四类 PLC 握手
- Web GUI：管理变量提取、Server、Agent 以及可选的 InoProShop MCP 功能
- MCP CLI：打开/编辑/编译 InoProShop 工程并从 GVL 提取 CSV

核心 OPC UA 仿真不依赖其他仓库。InoProShop 工程操作属于可选功能，需要使用者
自行安装 InoProShop、Node.js，并提供有权使用的 MCP bundle。

## 快速开始

### 原生安装包（无需 Python）

从 [GitHub Releases](https://github.com/raoyi971102-gif/PLC-Sim/releases) 下载与系统匹配的安装包：

- Windows 10/11 x64：`OpcUaSim-Setup-Windows-x64-*.exe`；
- Debian/Ubuntu 22.04+ x64：`OpcUaSim-Linux-x64-*.deb`；
- 其他 glibc 2.35+ Linux x64：`OpcUaSim-Linux-x64-*.tar.gz`；
- Apple Silicon Mac（M 系列芯片）：`OpcUaSim-macOS-arm64-*.dmg`；
- Intel Mac：`OpcUaSim-macOS-x64-*.dmg`。

Windows 安装后可从开始菜单启动。Debian/Ubuntu 安装 DEB 后可从应用菜单启动，
也可运行 `opcua-sim`；其他 Linux 解压便携包后运行目录中的 `OpcUaSim`。
macOS 打开 DMG 后把 `OpcUaSim.app` 拖入“Applications”目录即可。应用会自动
打开 Web GUI，不需要另外安装 Python 或依赖。

Linux DEB 安装示例：

```bash
sudo apt install ./OpcUaSim-Linux-x64-v*.deb
opcua-sim
```

Linux 便携包示例：

```bash
tar -xzf OpcUaSim-Linux-x64-v*.tar.gz
./OpcUaSim-Linux-x64-v*/OpcUaSim
```

当前安装包没有商业代码签名证书。Windows 可能显示 SmartScreen 提示；macOS
使用临时签名但尚未经过 Apple 公证，首次启动请按住 Control 点击应用，选择“打开”。

### pip 安装

仅支持 Python 3.11.x（不支持 3.10、3.12 或其他版本）。从已克隆的仓库安装：

```bash
python -m pip install ./OpcUaSim
```

也可以直接从 GitHub 安装（私有仓库需要本机 Git 已授权）：

```bash
python -m pip install \
  "git+https://github.com/raoyi971102-gif/PLC-Sim.git#subdirectory=OpcUaSim"
```

每个 `opcua-sim-v*` GitHub Release 也会附带经过校验的 wheel 和源码包；下载
`unilab_opcua_sim-*-py3-none-any.whl` 后可直接执行：

```bash
python -m pip install ./unilab_opcua_sim-*-py3-none-any.whl
```

安装后使用统一命令；不传子命令时默认启动 Web GUI：

```bash
opcua-sim
opcua-sim gui --host 127.0.0.1 --port 18765
opcua-sim server --host 127.0.0.1 --port 4855
opcua-sim handshake --url opc.tcp://127.0.0.1:4855/xuse_sim/
opcua-sim szlab-handshake --workflow szlab_s09_pipetting_workflow
```

如果系统没有将 Python Scripts 目录加入 `PATH`，可以等价运行：

```bash
python -m opcua_sim
python -m opcua_sim server --help
```

wheel 中的演示 CSV、YAML 配置和 GUI 静态文件为只读包资源。上传的 CSV、
提取结果和运行状态会写入用户数据目录：

- macOS：`~/Library/Application Support/OpcUaSim`；
- Windows：`%LOCALAPPDATA%\OpcUaSim`；
- Linux：`$XDG_DATA_HOME/opcua-sim` 或 `~/.local/share/opcua-sim`。

可用 `OPCUASIM_DATA_DIR` 统一覆盖上述目录。在源码仓库中运行时仍保留原有
`OpcUaSim/data/` 路径，不影响 `.command` 和 `.bat` 启动器。

### macOS 一键启动

需要 Python 3.11.x。进入 `OpcUaSim` 目录后，在 Finder 中双击：

- `start_gui.command`：推荐入口，启动 Web GUI 并自动打开浏览器；
- `start_all.command`：同时启动 OPC UA Server 和 SZLab Handshake Agent。

首次启动会自动创建 `.venv` 并安装 `requirements.txt`，后续启动会复用环境；
依赖文件变化时会自动同步，不需要手动运行 Python 文件。
如果旧 `.venv` 由其他 Python 版本创建，启动器会停止并提示；移走该目录后，
用 Python 3.11 重新双击启动器即可。

如果 macOS 首次阻止打开，按住 Control 点击 `.command` 文件，选择“打开”，再确认一次。
也可以从终端运行：

```bash
./start_gui.command
# 或同时启动 Server + Agent
./start_all.command
```

加载自己的变量表：

```bash
./start_all.command "/path/to/xuse_variables.csv"
```

macOS 支持 OPC UA Server、Handshake Agent 和 Web GUI。InoProShop 本体仅支持
Windows，因此 GUI 中依赖 InoProShop 的工程编辑、编译和下载功能在 macOS 上不可用。

### Windows 一键安装

需要 Python 3.11.x：

```bat
setup_venv.bat
start_all.bat
```

`setup_venv.bat` 会在当前目录创建 `.venv` 并安装 `requirements.txt`。
`start_all.bat` 会分别启动 Server 和 Handshake Agent。
如果已有 `.venv` 不是 Python 3.11，`setup_venv.bat` 会明确报错；移走旧
`.venv` 后重新运行即可。

运行端到端验证：

```bat
.venv\Scripts\python.exe -m pytest tests\test_szlab_handshake_agent.py -q
```

协议覆盖 Robot / S04–S09 及单样品原子流等 SZLab 握手场景。

### 手动安装

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe server.py --csv data/szlab_plc_0810.csv
```

另开一个终端：

```powershell
.\.venv\Scripts\python.exe szlab_handshake_agent.py
```

## 默认变量表

仓库包含 [`data/szlab_plc_0810.csv`](data/szlab_plc_0810.csv)，与 Uni-Lab-SZLab
官方部署图一致。克隆仓库后 GUI / `start_all` 默认使用该点表。

另附 [`data/demo_variables.csv`](data/demo_variables.csv) 作为最小演示 CSV。
使用自己的 CSV 有三种方式：

```powershell
# 临时指定
.\start_all.bat "C:\project\szlab_variables.csv"

# Python CLI
python server.py --csv "C:\project\szlab_variables.csv"
python szlab_handshake_agent.py --url opc.tcp://127.0.0.1:4855/xuse_sim/

# 环境变量（Server 读取默认 CSV）
$env:OPCUASIM_CSV = "C:\project\szlab_variables.csv"
.\start.bat
```

Server 必须加载与真机/驱动一致的点表；SZLab 握手代理按节点名读写，不单独加载 CSV。

CSV 至少包含以下列：

```csv
Name,EnglishName,NodeType,DataType,NodeLanguage,NodeId
工站初始化,Station_Initialize,VARIABLE,BOOLEAN,Chinese,ns=4;s=uniab|工站初始化
```

支持的数据类型为 `BOOLEAN`、`INT16`、`INT32`、`FLOAT`、`STRING`。

## 常用启动入口

| macOS | Windows | 用途 |
|---|---|---|
| `start.command` | `start.bat` | 只启动 OPC UA Server |
| `start_szlab_handshake.command` | `start_szlab_handshake.bat` | 只启动 SZLab Poly Studio 握手仿真 |
| `start_all.command` | `start_all.bat` | 同时启动 Server 和 SZLab Agent |
| `start_gui.command` | `start_gui.bat` | 启动 Web GUI，默认地址 `http://127.0.0.1:18765/` |
| — | `pick.bat` | 通过文件选择器加载一份或多份 CSV |
| 启动器自动完成 | `setup_venv.bat` | 创建项目虚拟环境并安装依赖 |

macOS 启动器按以下顺序选择 Python：

1. 项目内 `.venv`
2. `PYTHON` 环境变量
3. `PATH` 中的 Python 3.11

`start_all.command` 默认使用端口 `4855`。如需改端口，可在终端设置
`OPCUASIM_PORT`；Server 与 Agent 会自动使用相同端口：

```bash
OPCUASIM_PORT=4860 ./start_all.command
```

GUI 诊断脚本位于 `tools/diagnose.ps1`。

Windows 启动脚本按以下顺序选择 Python：

1. 项目内 `.venv`
2. `PYTHON` 环境变量
3. 已知的 Miniforge 路径
4. `PATH` 中的非 WindowsApps Python

## 命令行参数

Server：

```powershell
python server.py --host 0.0.0.0 --port 4855 `
  --csv data/szlab_plc_0810.csv `
  --ns-uri urn:xuse:sim --ns-index 4
```

可重复传入 `--csv` 合并多份变量表。`--no-occupancy-true` 可禁止将名称以
“占位”或“空闲”结尾的节点初始化为 `TRUE`。

Handshake Agent：

```powershell
python szlab_handshake_agent.py `
  --url opc.tcp://127.0.0.1:4855/xuse_sim/ `
  --config config/szlab_handshake.yaml
```

`config/szlab_handshake.yaml` 可覆盖延时、工作流与初值。

## SZLab Poly Studio 握手仿真

`szlab_handshake_agent.py` 以官方 Uni-Lab-SZLab 当前驱动和
`scripts/szlab_workflow_handshake.py` 为协议基线，覆盖全部 19 个官方 Python
工作流、2 个 PLC-SIM 双 TASK 扩展场景和 37 个唯一动作。状态机通过最小变量读写 interface 运行，OPC UA
只是其中一个 adapter，因此协议测试不需要启动网络服务。

覆盖范围包括 Robot 标准任务 `1/3-25`、S02-S11 物料在位传感器、S04
磁搅、S05 拍照、S06 加液、S07 扫码/转位/注粉、S08 开关盖、S09 移液，
以及标准物料转运、单样品物料感知全流程、无 S07 扫码原子流程、机器人原子动作流程和使用 S0722
交接位的烧杯五工位搬运。
S07/S09 天平读数、S08 瓶盖暂存位、
S09 TIP 盒/试剂瓶工位和液体余量都会随协议初始化或动作完成更新。
真机没有可靠的 `S09天平读数稳定`，握手代理不会读写该点。
S09 完成只以 `S09工艺完成` 为准；工艺 9 按 `S09测密度次数` 写入抽/放液天平数组前 N 项。

Robot 单任务和双 TASK 场景共用一份夹爪负载物理见证。普通动作进入握手前会校验：
夹爪持料时拒绝再次取料、夹爪空闲时拒绝倒液、源库位为空时拒绝取料、目标库位
已占用时拒绝放料。被拒绝的任务保持 `Robot_Home`，不写完成码，也不改变夹爪或
库位传感器；Edge 撤回 `Robot_任务写入完成` 后代理复位，待物理条件满足再重新发起。
这些传感器只模拟物理执行见证，不代替 OS 的库存（Inventory）结算。

所有单样品握手场景及 `all` 场景默认预置完整物料堆栈：S03 的 18 个烧杯源位和 18 个样品瓶
源位、S10 的 20 个试剂瓶源位均为在位，S11 的两类 18 个目标位均为空。因此，
工作流（Workflow）动作不变时，Edge 只需写入不同的产品码和位置，同一个握手
代理就会校验并更新对应库位（Site），无需为每组物料另建双 TASK 场景。若只想
提供部分仿真物料，可通过 `initial_values` 将不使用的传感器覆盖为 `false`。

双 TASK 机器人原子动作场景可独立启动；它复用 A/B 两条物料通道，将机器人事件映射为
`transfer_material_atomic` 和 `pick_pour_place_atomic`，并继续使用同一份共享夹爪见证：

```bash
python szlab_handshake_agent.py \
  --workflow s_z_lab_双任务单样品原子流程_机器人原子动作
```

先启动包含 SZLab 节点的 OPC UA Server，再运行：

```bat
start_szlab_handshake.bat
```

也可以指定其他 endpoint：

```bat
start_szlab_handshake.bat opc.tcp://127.0.0.1:4855/xuse_sim/
```

GUI 的“握手代理”默认即 SZLab Poly Studio，并选用内置
`data/szlab_plc_0810.csv`。可从 Uni-Lab-SZLab 当前 19 个官方工作流和两个双 TASK 扩展场景中
选择一个定向调试。代理只解析、初始化和轮询该工作流实际使用的节点；选择
“全部官方工作流”时同时启用所有协议模块。

命令行也支持同样的选择和参数覆盖：

```bash
python szlab_handshake_agent.py \
  --workflow s04_robot_stirring_workflow \
  --position 2 \
  --pump 1 \
  --delay-ms 250 \
  --poll-ms 40 \
  --s09-remaining-volume-ml 100
```

| 参数 | 用途 |
|---|---|
| `--workflow` | `all`、19 个官方工作流 ID 或两个双 TASK 扩展场景；旧 S07/S09 ID 仍作为别名 |
| `--position` | S04 调试位置，范围 `1-6` |
| `--pump` | S06 储液瓶，`1`、`2` 或 `3`（双泵） |
| `--delay-ms` | 统一覆盖无设备时间参数的动作延时；S04 磁搅优先使用本次动作的磁搅时间 |
| `--poll-ms` | OPC UA 轮询间隔，最小 5 ms |
| `--s09-remaining-volume-ml` | S09 1-5 号液体瓶的初始余量 |
| `--s07-balance-reading` | S07 注粉完成时写入的模拟天平值 |
| `--s09-balance-reading` | S09 放液/测密度完成时写入的模拟天平值 |

仿真驱动优先使用实机格式
`ns=4;s=上位机通讯|<变量名>`，找不到时按 BrowseName 递归匹配。
缺少所选工作流需要的节点时会明确报错，不会静默跳过并让 Edge 一直等待。

`szlab_stack_s05_s06_workflow` 已恢复为官方定义：读取堆栈状态后执行 S05
拍照和 S06 加液，不再复用旧的 `szlab-parallel-robot-lock-rev-1` 私有 revision。

延时、工作流和 PLC 侧初始值位于 `config/szlab_handshake.yaml`。命令行或 GUI
显式参数优先于该配置：

- `workflow`：`all` 或指定工作流 ID；
- `position`：S04 定向调试位置；
- `pump`：初始化为在位的 S06 储液瓶，取值 `1`、`2` 或 `3`（两瓶）；
- `s06_robot_workflow`：兼容开关；选择 S06 机器人或物料工作流时会自动启用；
- `s09_pipetting_workflow`：兼容开关；选择 S09 或单样品工作流时会自动启用；
- `s09_remaining_volume_ml`：S09 1-5 号液体瓶的初始余量；
- `s07_balance_reading` / `s09_balance_reading`：动作完成时反馈的模拟天平值；
- `cleanup_on_exit`：正常停止时清理仿真器拥有的 PLC 输出，但保留 PC 写入的任务号、
  工艺号和参数标志。

S04 磁搅接单后会读取 `磁搅时间设置_上位机[position-1]`，该值单位为
毫秒。例如单点动作的 `duration=30` 会写入 `30000`，代理在 30 秒后才反馈
S04 加工完成。如果 Server 不提供该节点，代理使用 `delays.stirrer` 的固定延时。
其他协议模块分别使用 `delays.robot/pump/s07/s08/s09`。

S09 按新版驱动保持 `S09参数写入完成=True` 直到本工艺完成，代理只有同时看到
有效工艺号和参数完成信号才接单；Edge 将二者清零后，代理才复位完成码并允许下一轮。

内置 `data/szlab_plc_0810.csv` 与官方部署图一致，并由测试校验包含全部握手变量。
启动仿真 Server 时应加载这份表，或加载从更新 PLC 工程提取且包含同等节点的 CSV。
S09 点表已去掉 `S09天平读数稳定`，并包含测密度次数与抽/放液天平数组。

## Web GUI

```text
macOS:  start_gui.command
Windows: start_gui.bat
```

GUI 提供三个独立工作区：

- **提取变量**：发现 GVL、预览并导出 OPC UA 变量 CSV。
- **编辑程序块**：浏览和修改 POU、GVL、DUT。
- **OPC UA 仿真**：管理 Server/Agent；从全部变量中搜索、勾选节点并加入
  监控栏，在监控栏中定时读取或手动刷新，并进行变量写入。写入值会按 CSV
  声明的数据类型校验，并在写入后回读确认。GUI 会根据规范化变量定义计算
  CSV 指纹，并在当前浏览器中分别保存每份变量表的监控列表；刷新页面或切回
  相同 CSV 后会自动恢复。
- **客户端连接**：展示当前 TCP 连接数、已激活的 OPC UA Session 数，以及
  客户端 IP、源端口、Session 状态和连接时长。客户端源端口由客户端操作系统
  临时分配，重连后可能变化。

即使没有 MCP bundle，GUI 仍能启动 Server 和 Agent。项目打开、POU 编辑、编译、
下载尝试和 GVL 提取需要配置下面的 MCP 依赖。

### 远程 Linux 挂接

当 OPC UA Server 和 Agent 由 Supervisor 或 systemd 托管时，GUI 可以只挂接现有
服务，不再尝试占用端口或结束外部进程：

```bash
python -m gui.backend \
  --host 0.0.0.0 \
  --port 18765 \
  --no-open \
  --attach-url opc.tcp://127.0.0.1:4855/xuse_sim/ \
  --attach-csv data/demo_variables.csv
```

挂接模式保留在线变量读取和写入，但会禁用 GUI 内的 Server/Agent 启停按钮。
浏览器和服务器不在同一台机器时，可在 GUI 上传 CSV；文件会保存到
`data/uploads/`，该目录不会提交到 Git。远程挂接的完整自检入口为：

```bash
python tests/integration/remote_attach_check.py
```

客户端连接遥测默认写入 `data/runtime/server-connections.json`。外部托管时，
Server 与 GUI 必须使用同一项目目录；如果两个进程的运行目录不同，请为两者
设置相同的 `OPCUASIM_CONNECTION_STATE` 绝对路径。该运行时目录不会提交到 Git。

## 可选：InoProShop MCP

系统要求：

- Windows
- Node.js 18 或更高版本
- InoProShop V1.9.1.6（SP11 内核）
- 仓库内已集成且与该版本匹配的 `bundle.min.js`

bundle 已放在自动发现位置：

```text
OpcUaSim/vendor/inoproshop-mcp/bundle.min.js
```

该 bundle 已作为项目运行依赖集成，无需另行设置路径或用户级 MCP JSON。它没有
随附标准开源许可证；向仓库外复制、发布或制作公开安装包前，必须先确认相应授权。

仓库同时提供 `persistent-launcher.js` 和 `persistent_host.py`。默认打开工程时只启动
一次 InoProShop，并在同一进程中复用已打开的工程；提取变量、读取 POU、保存和编译
不会再重复冷启动。只有点击 GUI 的“关闭”按钮、切换到另一个工程或停止后端时，
PLC-Sim 才关闭该工程会话。若常驻进程异常退出，下一次 MCP 调用会自动重建会话。

配置优先级为：显式参数 > 环境变量 > 有效的用户 MCP JSON > 自动探测。用户 MCP
JSON 中已不存在的 bundle 或 InoProShop 路径会被忽略，并自动回退到仓库副本或
系统安装位置。

支持的环境变量：

| 变量 | 说明 |
|---|---|
| `OPCUASIM_MCP_BUNDLE` | `bundle.min.js` 路径 |
| `OPCUASIM_INOPROSHOP_EXE` | `InoProShop.exe` 路径 |
| `OPCUASIM_INOPROSHOP_PROFILE` | InoProShop profile |
| `OPCUASIM_MCP_WORKSPACE` | MCP 工作区 |
| `OPCUASIM_NODE` | `node` 命令或绝对路径 |
| `OPCUASIM_MCP_CONFIG` | 自定义 MCP JSON 路径 |

也会自动检查：

- `%USERPROFILE%\.cursor\mcp.json`
- `%USERPROFILE%\.mcp.json`
- 常见 `C:\Program Files` / `D:\Program Files` InoProShop 安装路径

参考配置见 [`.env.example`](.env.example)。

### MCP CLI 示例

```powershell
python -m ino_mcp.cli structure `
  --project "C:\project\XUSE.project"

python -m ino_mcp.cli extract `
  --project "C:\project\XUSE.project" `
  --out extracted\XUSE.csv --all
```

每条命令也支持：

```text
--bundle
--codesys-path
--codesys-profile
--workspace
--node
--mcp-server
```

## 目录结构

```text
OpcUaSim/
├── config/                       # SZLab 握手配置
├── data/                         # 开箱即用的 CSV（含 szlab_plc_0810）
├── gui/                          # FastAPI Web GUI 与前端资源
├── ino_mcp/                      # 可选 MCP 客户端、配置、业务封装和 CLI
├── scripts/                      # 启动器共用的内部脚本
├── tests/
│   ├── fixtures/                 # 测试数据
│   └── integration/              # 可独立运行的端到端检查
├── tools/                        # 诊断工具
├── vendor/inoproshop-mcp/        # 可选第三方 bundle 放置点
├── common.py
├── cli.py                         # pip 安装后的统一命令分发
├── server.py
├── szlab_handshake_agent.py      # SZLab Robot / S04-S09 握手仿真
├── pyproject.toml                 # unilab-opcua-sim wheel 元数据
├── requirements.txt
├── setup_venv.bat
└── start*.bat
```

## 安全与部署说明

- OPC UA Server 默认允许匿名访问且使用 `NoSecurity`，仅适合开发、测试或受控网络。
- `0.0.0.0` 会监听所有网卡；只需本机使用时可传 `--host 127.0.0.1`。
- MCP 的在线下载属于非幂等设备操作；当前工具的可靠路径仍是保存和编译，真实下载
  前应在 InoProShop 中确认目标设备与工程版本。
