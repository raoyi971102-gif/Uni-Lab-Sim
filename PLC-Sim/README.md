# PLC-Sim

一个由 CSV 变量表驱动的 OPC UA 仿真环境，包含：

- OPC UA Server：按 CSV 或 PTLC V2 节点快照创建节点，默认监听 `opc.tcp://0.0.0.0:4855/xuse_sim/`
- Handshake Agent：仿真 SZLab 工作流握手或 PTLC V2 通用 L2 握手
- Web GUI：管理变量提取、Server、Agent 以及可选的 InoProShop MCP 功能
- MCP CLI：打开/编辑/编译 InoProShop 工程并从 GVL 提取 CSV

核心 OPC UA 仿真不依赖其他仓库。InoProShop 工程操作属于可选功能，需要使用者
自行安装 InoProShop、Node.js，并提供有权使用的 MCP bundle。

## 命名约定

- 产品、桌面应用、源码目录和原生安装包：`PLC-Sim`；
- 统一命令行入口：`plc-sim`；
- Python 分发包：`unilab-plc-sim`；
- Python 导入模块：`plc_sim`；
- 配置环境变量前缀：`PLCSIM_*`；
- GitHub Release 标签：`plc-sim-v*`。

## 快速开始

### 原生安装包（无需 Python）

从 [GitHub Releases](https://github.com/raoyi971102-gif/PLC-Sim/releases) 下载与系统匹配的安装包：

- Windows 10/11 x64：`PLC-Sim-Setup-Windows-x64-*.exe`；
- Debian/Ubuntu 22.04+ x64：`PLC-Sim-Linux-x64-*.deb`；
- 其他 glibc 2.35+ Linux x64：`PLC-Sim-Linux-x64-*.tar.gz`；
- Apple Silicon Mac（M 系列芯片）：`PLC-Sim-macOS-arm64-*.dmg`；
- Intel Mac：`PLC-Sim-macOS-x64-*.dmg`。

Windows 安装后可从开始菜单启动。Debian/Ubuntu 安装 DEB 后可从应用菜单启动，
也可运行 `plc-sim`；其他 Linux 解压便携包后运行目录中的 `PLC-Sim`。
macOS 打开 DMG 后把 `PLC-Sim.app` 拖入“Applications”目录即可。应用会自动
打开 Web GUI，不需要另外安装 Python 或依赖。

Linux DEB 安装示例：

```bash
sudo apt install ./PLC-Sim-Linux-x64-v*.deb
plc-sim
```

Linux 便携包示例：

```bash
tar -xzf PLC-Sim-Linux-x64-v*.tar.gz
./PLC-Sim-Linux-x64-v*/PLC-Sim
```

当前安装包没有商业代码签名证书。Windows 可能显示 SmartScreen 提示；macOS
使用临时签名但尚未经过 Apple 公证，首次启动请按住 Control 点击应用，选择“打开”。

### pip 安装

仅支持 Python 3.11.x（不支持 3.10、3.12 或其他版本）。从已克隆的仓库安装：

```bash
python -m pip install ./PLC-Sim
```

也可以直接从 GitHub 安装（私有仓库需要本机 Git 已授权）：

```bash
python -m pip install \
  "git+https://github.com/raoyi971102-gif/PLC-Sim.git#subdirectory=PLC-Sim"
```

每个 `plc-sim-v*` GitHub Release 也会附带经过校验的 wheel 和源码包；下载
`unilab_plc_sim-*-py3-none-any.whl` 后可直接执行：

```bash
python -m pip install ./unilab_plc_sim-*-py3-none-any.whl
```

安装后使用统一命令；不传子命令时默认启动 Web GUI：

```bash
plc-sim
plc-sim gui --host 127.0.0.1 --port 18765
plc-sim server --host 127.0.0.1 --port 4855
plc-sim handshake --url opc.tcp://127.0.0.1:4855/xuse_sim/
plc-sim szlab-handshake --time-scale 10
plc-sim server --profile ptlc
plc-sim ptlc-handshake --config config/ptlc_handshake.yaml
```

如果系统没有将 Python Scripts 目录加入 `PATH`，可以等价运行：

```bash
python -m plc_sim
python -m plc_sim server --help
```

wheel 中的演示 CSV、YAML 配置和 GUI 静态文件为只读包资源。上传的 CSV、
提取结果和运行状态会写入用户数据目录：

- macOS：`~/Library/Application Support/PLC-Sim`；
- Windows：`%LOCALAPPDATA%\PLC-Sim`；
- Linux：`$XDG_DATA_HOME/plc-sim` 或 `~/.local/share/plc-sim`。

可用 `PLCSIM_DATA_DIR` 统一覆盖上述目录。在源码仓库中运行时使用
`PLC-Sim/data/` 路径，不影响 `.command` 和 `.bat` 启动器。

### macOS 一键启动

需要 Python 3.11.x。进入 `PLC-Sim` 目录后，在 Finder 中双击：

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

协议覆盖 Robot / S04–S09 与 S1 HTTP；默认一次启动整个 SZLab 设备包会话。

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
$env:PLCSIM_CSV = "C:\project\szlab_variables.csv"
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
`PLCSIM_PORT`；Server 与 Agent 会自动使用相同端口：

```bash
PLCSIM_PORT=4860 ./start_all.command
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

## PTLC V2 L2 握手仿真

正式架构由 **Uni-Lab OS FE + Uni-Lab OS Backend + PLC-SIM** 共同替代 PTLC_UI。
OS Backend 是工作流、资源锁和跨设备协调的唯一真源；PLC-SIM 是 PLC-only 仿真后端，
只维护 OPC UA 节点、L2 信封、PLC 时序、轴、气缸、泵、阀、液位和输入映像。
机器人、相机、视觉及其他直连设备由独立模拟模块负责，OS 连接 PTLC_UI 仿真只作为迁移期兼容路径。
当前能力边界、剩余差距和建设优先级见
[PTLC PLC 仿真能力评估](../docs/ptlc-plc-simulation-gap-assessment.md)。

PTLC profile 是 PLC-SIM 内置的协议快照，不会在运行时导入或修改 PTLC 仓库：

```bash
plc-sim server --profile ptlc --csv config/ptlc_nodes.yaml
plc-sim ptlc-handshake --config config/ptlc_handshake.yaml
```

Server 会创建
`Objects/DeviceSet/Inovance-ARM-Linux/Resources/Application/GlobalVars/Host_Computer`
嵌套 GVL，支持 Boolean、Byte、Int16、Int32、Float、Double、String 以及固定长度真数组。
代理响应 Sampling、Collect、Develop、PhotoScrape、FeedLift、Pump、Rail、StagingA
八个统一 L2 通道。`config/ptlc_behavior/` 固化各工位合法动作码、步序、错误码、
门禁、计时常量和原始注记；未知动作按对应派发器错误码 REJECTED，不再默认成功。
代理提供完整 `PLC_Deploy_*` PREPARING/SAFE/COMMITTED 状态机、动作级连续轴位置、
FeedLift 板堆/搜索、地轨数组索引、Develop 排液与抽吸、Collect 瓶互锁和多轮排液、
Sampling 泵行程门、PhotoScrape 对位安全门、延迟气缸到位反馈、PLC 输入字节合成及
确定性事件快照。执行器命令与传感器反馈使用不同的时钟：命令可立即写出，到位输入按
`plant.cylinder_s` 延迟变化；IX8..IX12 始终由当前物理事实重新合成。
八个 dispatcher 的 **55 个合法 PLC 动作全部建模**；合法动作会完成，或按行为规格进入
确定性 REJECTED/ERROR，不再以 `unmodeled` 无限等待。可用
`plc-sim ptlc-handshake list` 查看 55/55 覆盖率。代理重启会锁存现有 Start 电平并
保留 L2 序号/终态，避免重放保持为高电平的非幂等请求。

物料传感器支持两种模式，由 `config/ptlc_handshake.yaml` 的
`plant.sensor_mode` 选择：

- `standalone`：默认的一键 PLC 调试模式。Collect A22/A23 会在
  `external_transition_s` 后自动模拟外部取走/放入；FeedLift 成功取料/放废料后自动更新计数。
- `federated`：OS + 多设备联合仿真模式。PLC-SIM 不推断机器人动作，只接受机器人等
  外部模拟器提供的幂等现场事件。

GUI 启动 PTLC 代理时可直接选择模式，命令行可用
`--sensor-mode standalone|federated` 覆盖 YAML 默认值。

独立设备模拟器可通过 `--world-file` 向 PLC 输入层回灌现场事实；支持
`feed_count`、`waste_count`、双轴回零状态、`sensors` 以及带 `event_id` 的 `events`。
直接传感器值适合设定初态；运行期转运应优先使用事件：

```json
{
  "feed_count": 12,
  "waste_count": 0,
  "feed_homed": true,
  "waste_homed": true,
  "sensors": {
    "bottle_present": false,
    "staging_a_present": false,
    "rack_occupied": [true, true, false]
  },
  "events": [
    {
      "event_id": "robot-transfer-0001",
      "kind": "material_transfer",
      "source": "staging_a",
      "target": "collect_bottle"
    },
    {
      "event_id": "operator-rack-0002",
      "kind": "site_set",
      "site": "rack_03",
      "present": true
    }
  ]
}
```

可用站点包括 `collect_bottle`、`staging_a`、`staging_b`、两个取样托盘及
`rack_01`..`rack_12`；`external` 可作为转运源或目标，表示 PLC 边界之外。
`event_id` 在进程内去重，重复事件不会再次改变站点。绝对计数用于进程重启后的状态恢复。
这条输入 seam 只传递 PLC 能看到的现场事实，不接收工作流、机器人位姿或工具命令。
GUI 托管 PTLC 代理时会创建 `ptlc-world.json`，也可通过
`POST /api/agent/ptlc/world` 原子更新；仿真服务页也提供物料转移和站点存在状态控件。

GUI 中分别把节点模型和代理类型切换为 PTLC 即可。PLC 输出默认禁止从在线变量栏
写入，避免 GUI 与握手代理形成双写者；只有显式勾选“维护写入”才可临时覆盖。
PTLC 代理支持时间倍率，并可在 GUI 运行期按工位/动作码注入
`reject/error/hang/interrupt`。命令行也可使用：

```bash
plc-sim ptlc-handshake --time-scale 10 \
  --fault-file data/runtime/ptlc-faults.json \
  --world-file data/runtime/ptlc-world.json \
  --state-file data/runtime/ptlc-state.json
```

如需核对参考仓库漂移，可在测试环境设置 `PTLC_REFERENCE_ROOT` 指向 PTLC V2 仓库根目录：

```bash
PTLC_REFERENCE_ROOT=/path/to/pTLC_platformUI pytest tests/test_ptlc_contract.py
```

刷新节点和八份行为规格快照：

```bash
python tools/snapshot_ptlc_profile.py \
  /path/to/pTLC_platformUI/eit_ptlc/config/plc_nodes.yaml config/ptlc_nodes.yaml \
  --behavior-source /path/to/pTLC_platformUI/eit_ptlc/mock/behavior/specs \
  --behavior-destination config/ptlc_behavior
```

## PLC 工程版本、符号与安全下载

打开 `.project` 后，PLC-Sim 会在运行数据目录的 `plc-history/` 下建立按工程隔离的
全量内容寻址快照。保存、POU 修改、符号 pragma 修改和下载前都会自动留档；API 支持
列出、下载及校验后恢复历史版本。恢复前会关闭 InoProShop MCP 会话，完成后需重新开工程。

“符号导出”可逐变量增删 `{attribute 'symbol' := 'readwrite'}`，并可立即编译验证。
下载策略有明确区别：

- `save_compile`：只保存并编译，绝不登录 PLC；
- `online`：GUI 当前无条件关闭。旧的 `PLCSIM_ALLOW_ONLINE_DEPLOY` 开关和二次
  确认不构成部署授权；必须先接入 pTLC `PlcProgramService` 的维护门、目标绑定、
  一次性授权和 `PLC_Deploy_*` 握手，才能重新开放真实 PLC 下载。

## SZLab 设备包级仿真

`szlab_handshake_agent.py` 默认运行 **package mode**：一个进程常驻 Robot、S04
六个磁搅位、S05、S06、S07、S08、S09 全部 OPC UA 协议，同时启动 S1 HTTP
stand-in。工作流不再决定启用哪些处理器；Uni-Lab Edge 继续作为 Workflow 的权威
执行器，任何由已建模 Action 组成的 Workflow 都可以连接同一个仿真会话执行。

当前行为快照与 SZLab Catalog 对齐：19 个 Workflow、9 个真实设备、105 个真实
Action。其中 62 个物理 Action 由协议模型覆盖，10 个组合/管理 Action 委托既有
驱动逻辑，17 个查询 Action 直接读取仿真状态，16 个 S1 Action 由 HTTP Adapter
承接。未知动作按 `unsupported` 关闭失败，不会伪造完成信号。覆盖分类见
`config/szlab_behavior.yaml`。

兼容场景选择器同时保留两个机器人原子动作扩展场景。单任务和双 TASK 场景共用
一份夹爪负载物理见证：夹爪持料时拒绝再次取料、夹爪空闲时拒绝倒液、源库位
为空时拒绝取料、目标库位已占用时拒绝放料。被拒绝的任务保持 `Robot_Home`，
不写完成码，也不改变夹爪或库位传感器；这些传感器只模拟物理执行见证，不代替
OS 的库存（Inventory）结算。

单样品兼容场景和 `all` 默认预置完整物料堆栈：S03 的 18 个烧杯源位和 18 个
样品瓶源位、S10 的 20 个试剂瓶源位均为在位，S11 的两类 18 个目标位均为空。
Edge 只需写入不同的产品码和位置，同一个代理就会校验并更新对应库位（Site）；
如只需部分物料，可通过 `initial_values` 把不使用的传感器覆盖为 `false`。

先启动包含 SZLab 节点的 OPC UA Server，再运行：

```bash
python szlab_handshake_agent.py \
  --url opc.tcp://127.0.0.1:4855/xuse_sim/ \
  --time-scale 1 \
  --s1-host 127.0.0.1 \
  --s1-port 8055
```

Windows/macOS 仍可使用 `start_szlab_handshake.bat` 或
`start_szlab_handshake.command`。S1 驱动应配置到
`http://127.0.0.1:8055/api/v1`；需要执行调度、停止、清洗或补液动作时，
该驱动实例还需明确配置 `test_mode=false` 与 `allow_hardware_action=true`；
这些请求仍只会进入本地 stand-in，不会操作真实硬件。远程部署时把监听
地址改为可达地址并单独暴露端口。

| 参数 | 用途 |
|---|---|
| `--workflow` | `list/check` 时过滤 Workflow；`serve` 时仅选择兼容初始场景，所有协议仍常驻 |
| `--legacy-workflow-mode` | 临时恢复旧版按 Workflow 裁剪处理器的行为 |
| `--position` | 兼容场景的默认 S04 位置；package mode 仍同时监听 `1-6` |
| `--pump` | 初始在位的 S06 储液瓶，`1`、`2` 或 `3`（双泵） |
| `--time-scale` | Robot、S04-S09 参数时长和回退延时的统一倍率 |
| `--delay-ms` | 无设备时间参数动作的回退延时 |
| `--state-file` | 原子写入会话、运行、事件、世界状态和覆盖报告 JSON |
| `--s1-host` / `--s1-port` | S1 HTTP stand-in 监听地址和端口 |
| `--no-s1-http` | 明确关闭 S1 Adapter |

通用运行时位于 `package_simulation.py`，SZLab Adapter 位于
`szlab_package_runtime.py`。它们提供统一 `session_id/run_id`、有序事件、共享世界
状态、覆盖报告和原子快照。GUI 通过 `GET /api/agent/szlab/state` 读取同一份状态。

S04 完成时间优先采用驱动写入的毫秒时长；例如 `duration=30` 在 1× 倍率下等待
30 秒后反馈，在 10× 下等待 3 秒。S09 仍只以 `S09工艺完成` 为完成依据，工艺 9
按测密度次数写抽/放液天平数组。内置 `data/szlab_plc_0810.csv` 必须与 Edge 使用的
驱动节点合同一致。

`config/szlab_handshake.yaml` 保存 OPC 协议与进程参数，
`config/szlab_package.yaml` 保存世界初态，`config/szlab_behavior.yaml` 保存 Catalog
覆盖快照。更新 Uni-Lab-SZLab 后可在其 Python 3.11 环境中运行：

```bash
python tools/snapshot_szlab_profile.py /path/to/Uni-Lab-SZLab \
  --behavior config/szlab_behavior.yaml
```

命令返回非零表示设备、Action 或 Workflow 数量已经漂移，发布前必须补齐模型或明确
分类。`SZLAB_REFERENCE_ROOT=/path/to/Uni-Lab-SZLab python -m pytest` 会启用同一项
跨仓库合同检查。

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

### GUI 模块结构

GUI 不需要 Node.js 构建步骤。FastAPI 后端按功能域组合路由，浏览器端脚本按下面的
顺序直接加载；因此 wheel、Windows/macOS/Linux 安装包和源码启动保持同一套入口。

- `gui/backend.py`：应用装配、状态/版本接口、SSE 日志和 CLI 入口。
- `gui/project_routes.py`：工程打开、编辑、提取、编译和版本历史。
- `gui/server_routes.py`：OPC UA Server 生命周期、变量读取和维护写入。
- `gui/agent_routes.py`：SZLab/PTLC 握手代理生命周期和 PTLC 故障注入。
- `gui/backend_state.py` 与 `gui/processes.py`：跨路由共享状态和子进程回收。
- `gui/static/app.js`：通用请求、页面状态和标签切换。
- `project.js`、`simulation.js`、`variables.js`、`diagnostics.js`：工程、仿真、
  在线变量和诊断功能。浏览器模块按 `index.html` 的固定顺序复用 `app.js` 的请求与
  状态辅助函数；对后端的 interface 仍是既有 `/api/...` HTTP/SSE 路径。

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
设置相同的 `PLCSIM_CONNECTION_STATE` 绝对路径。该运行时目录不会提交到 Git。

## 可选：InoProShop MCP

系统要求：

- Windows
- Node.js 18 或更高版本
- InoProShop V1.9.1.6（SP11 内核）
- 仓库内已集成且与该版本匹配的 `bundle.min.js`

bundle 已放在自动发现位置：

```text
PLC-Sim/vendor/inoproshop-mcp/bundle.min.js
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
| `PLCSIM_MCP_BUNDLE` | `bundle.min.js` 路径 |
| `PLCSIM_INOPROSHOP_EXE` | `InoProShop.exe` 路径 |
| `PLCSIM_INOPROSHOP_PROFILE` | InoProShop profile |
| `PLCSIM_MCP_WORKSPACE` | MCP 工作区 |
| `PLCSIM_NODE` | `node` 命令或绝对路径 |
| `PLCSIM_MCP_CONFIG` | 自定义 MCP JSON 路径 |

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
PLC-Sim/
├── config/                       # SZLab/PTLC 协议、世界初态与行为覆盖快照
├── data/                         # 开箱即用的 CSV（含 szlab_plc_0810）
├── gui/                          # FastAPI 应用装配、功能路由与前端资源
│   ├── backend.py                # 应用、诊断、SSE 与 CLI 入口
│   ├── backend_state.py          # 跨路由共享状态
│   ├── processes.py              # 托管子进程生命周期
│   ├── project_routes.py         # 工程/MCP 路由
│   ├── server_routes.py          # OPC UA Server/变量路由
│   ├── agent_routes.py           # 握手代理/PTLC 故障与 PLC 输入世界路由
│   └── static/                   # 无构建步骤的模块化浏览器端资源
├── ino_mcp/                      # 可选 MCP 客户端、配置、业务封装和 CLI
├── scripts/                      # 启动器共用的内部脚本
├── tests/
│   ├── fixtures/                 # 测试数据
│   └── integration/              # 可独立运行的端到端检查
├── tools/                        # 诊断工具
├── vendor/inoproshop-mcp/        # 可选第三方 bundle 放置点
├── common.py
├── cli.py                         # pip 安装后的统一命令分发
├── ptlc_agent_cli.py              # PTLC 代理命令行生命周期
├── ptlc_behavior.py               # 八工位行为契约加载器
├── ptlc_effects.py                # 配置化变量副作用
├── ptlc_handshake_agent.py        # PTLC L2 状态机
├── ptlc_plant.py                  # PTLC PLC 设备仿真深模块（55 个动作）
├── ptlc_sensors.py                # PLC 输入、气缸反馈与外部物料事件
├── ptlc_runtime.py                # OPC 适配器、运行状态与故障模型
├── package_simulation.py          # 通用设备包会话、时钟、世界状态与事件
├── server.py
├── szlab_package_runtime.py       # SZLab Catalog 覆盖与协议事件 Adapter
├── szlab_s1_sim.py                # S1 连续流工作站 HTTP stand-in
├── szlab_handshake_agent.py       # SZLab 全设备包 OPC UA 仿真入口
├── pyproject.toml                 # unilab-plc-sim wheel 元数据
├── requirements.txt
├── setup_venv.bat
└── start*.bat
```

## 安全与部署说明

- OPC UA Server 默认允许匿名访问且使用 `NoSecurity`，仅适合开发、测试或受控网络。
- `0.0.0.0` 会监听所有网卡；只需本机使用时可传 `--host 127.0.0.1`。
- MCP 的在线下载属于非幂等设备操作；GUI 后端当前无条件拒绝，不会因环境变量、
  预检 SHA 或人工二次确认而绕过。真实部署必须由 pTLC `PlcProgramService` 完成
  维护门、目标绑定、一次性授权及 `PLC_Deploy_*` 握手，且不得自动重试。
