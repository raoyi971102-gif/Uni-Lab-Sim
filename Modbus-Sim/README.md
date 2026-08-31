# Modbus-Sim

Modbus-Sim 是 Uni-Lab-Sim 中独立的 Modbus 从站仿真应用。一份设备模型可以在四种传输方式之间切换：

- Modbus RTU over RS-485
- Modbus RTU over RS-232
- Modbus TCP
- Modbus ASCII

它提供本地 Web GUI 和无界面 CLI，适合在真实设备不可用时联调 Modbus 主站、驱动及 Uni-Lab 设备接入代码。

## 环境

- Python 3.11.x（当前不支持 3.10 或 3.12）
- 串行模式可以使用真实串口、USB 转串口设备，或应用管理的虚拟串口对

```bash
python3.11 -m venv .venv
.venv/bin/python -m pip install .
```

Windows 激活环境后使用 `.venv\Scripts\python.exe`。也可以直接运行 `start_gui.bat`；启动器会查找 Python 3.11（包括常见的 Miniforge 环境），首次运行时自动创建项目独立的 `.venv` 并联网安装依赖，后续启动不依赖网络。如需指定解释器，可将 `MODBUSSIM_PYTHON` 设置为 Python 3.11 可执行文件的完整路径。macOS/Linux 可以运行 `./start_gui.sh`，macOS Finder 用户可双击 `start_gui.command`。

## GUI

```bash
modbus-sim
# 等价于
modbus-sim gui
```

默认在 <http://127.0.0.1:18865> 打开工程工作台。界面以多文档寄存器桌面为主，左侧设备树组织从站和四类数据区，底部常驻真实报文流量：

- 选择 TCP、RTU RS-485、RTU RS-232 或 ASCII，并编辑对应连接参数；
- 添加、编辑或删除从站，修改 Unit ID、名称、四个数据区大小及寄存器定义；
- 服务停止时编辑别名、初值、显示格式和说明；
- 服务运行时修改线圈和保持寄存器实时值，只读区会明确锁定；
- 通过 CSV 导入或导出设备与寄存器地址表，并保留现有连接设置；
- 导入或导出包含连接参数的完整 YAML 配置；
- 在 Linux/macOS 上直接创建临时 PTY 串口对，在 Windows 上安装可选 com0com 驱动并自动管理端口对；
- 查看 Tx/Rx/Error 计数、连接数、端点、运行时间及协议报文。

其他 GUI 参数：

```bash
modbus-sim gui --host 127.0.0.1 --port 18865 --config config/demo.yaml --no-open
```

GUI 默认不提供鉴权。不要把监听地址暴露到不可信网络。

## 无界面服务

```bash
modbus-sim validate --config config/demo.yaml
modbus-sim serve --config config/demo.yaml --transport tcp --host 0.0.0.0 --tcp-port 5020
modbus-sim serve --config config/demo.yaml --transport rtu-rs485 --serial-port /dev/ttyUSB0 --baudrate 9600
modbus-sim serve --config config/demo.yaml --transport rtu-rs232 --serial-port COM3
modbus-sim serve --config config/demo.yaml --transport ascii --serial-port /dev/ttyUSB0
```

环境变量可覆盖常用参数：`MODBUSSIM_CONFIG`、`MODBUSSIM_TRANSPORT`、`MODBUSSIM_TCP_HOST`、`MODBUSSIM_TCP_PORT`、`MODBUSSIM_SERIAL_PORT`、`MODBUSSIM_BAUDRATE`、`MODBUSSIM_GUI_HOST`、`MODBUSSIM_GUI_PORT`。

## 配置模型

默认示例见 [`config/demo.yaml`](config/demo.yaml)。`active_transport` 指定当前传输方式，`transports` 保存每种方式各自的参数，`devices` 是所有模式共享的从站模型。

每个从站包含四个标准数据区：

| 配置名 | Modbus 数据区 | 常用功能码 | 客户端权限 |
| --- | --- | --- | --- |
| `coils` | 线圈 | 01 / 05 / 15 | 读写 |
| `discrete_inputs` | 离散输入 | 02 | 只读 |
| `holding_registers` | 保持寄存器 | 03 / 06 / 16 | 读写 |
| `input_registers` | 输入寄存器 | 04 | 只读 |

协议请求使用从 0 开始的偏移地址。GUI 可以在协议偏移和 PLC 常见基址（00001、10001、30001、40001）之间切换显示。

## CSV 寄存器地址表

工作台顶部的“导入寄存器 CSV”和“导出 CSV”用于交换设备、数据区与显式点位定义。CSV 导入只替换 `devices`，当前传输方式和四种连接配置保持不变；完整工程备份仍使用 YAML。导入前需要停止仿真服务，文件上限为 5 MB，支持 UTF-8（含 BOM）及 GB18030。

CSV 表头固定为：

```csv
unit_id,device_name,area,area_size,address,alias,value,format,description
1,Demo PLC,coils,16,0,Run_Command,false,bool,运行命令
1,Demo PLC,discrete_inputs,16,,,,,
1,Demo PLC,holding_registers,32,0,Speed_Setpoint,1200,uint16,转速设定
1,Demo PLC,input_registers,32,0,Actual_Speed,1185,uint16,实际转速
```

- `area` 使用 `coils`、`discrete_inputs`、`holding_registers` 或 `input_registers`；每个设备必须各有一行。
- `address` 是从 0 开始的协议偏移；没有显式点位的数据区用空地址行保留 `area_size`。
- 位数据值支持 `true/false`、`on/off`、`1/0`；寄存器值支持十进制或 `0x` 十六进制。
- 位格式为 `bool`；寄存器格式可用 `uint16`、`int16`、`hex`、`binary`。
- 同一 Unit ID 的设备名、同一数据区的大小必须一致，地址不能重复或越界。含逗号的名称、别名和说明应按标准 CSV 使用双引号。

最稳妥的编辑流程是先从 GUI 导出当前 CSV，在 Excel 或文本编辑器中修改，再整表导入。

## 虚拟串口

GUI 右侧“虚拟串口”面板创建一对互联端口：Modbus-Sim 使用“仿真端”，Modbus Poll 或其他主站连接“主站端”。如果创建时已选择 RTU RS-485、RTU RS-232 或 ASCII，应用会自动把仿真端写入当前连接配置；选择 TCP 时，创建后再切换到串行模式并从端口列表选择。

### Linux / macOS

应用使用系统 PTY，无需额外软件或管理员权限。端口形如 `/dev/pts/5` 和 `/dev/pts/6`，只在本次 Modbus-Sim 进程中有效，关闭应用后自动释放。

### Windows

Windows 的真实 `COM` 设备名必须由内核驱动提供。原生 Modbus-Sim 安装包离线携带 [com0com 3.0.0.0 官方签名版本](https://sourceforge.net/projects/com0com/files/com0com/3.0.0.0/)及其对应源码和 GPLv2 许可证，并以未勾选的可选组件提供：

1. 安装 Modbus-Sim 时可勾选“安装 com0com 虚拟串口驱动”；如果跳过，也可稍后在 GUI 中点击“安装内置驱动”；
2. 安装驱动时 Windows 会显示 UAC 确认。Modbus-Sim 本身仍以普通用户权限运行；
3. 在面板中填写两个未占用的端口名（默认 `COM10` / `COM11`）并创建，创建和移除操作会按需再次请求 UAC；
4. Modbus-Sim 自动调用 `setupc.exe` 创建、识别和移除当前会话管理的端口对；Windows 串口对是持久的，不会随应用退出消失；
5. 卸载 Modbus-Sim 不会自动卸载 com0com，也不会删除无法确认归属的端口对，避免影响其他软件。请先在 GUI 中移除本应用创建的端口对；必要时使用 com0com Setup 管理工具清理。

驱动安装程序在执行前会校验固定 SHA-256，校验失败时不会提权或运行。com0com 的官方签名发行版较旧，仍需在目标 Windows 10/11 版本、Secure Boot 策略和企业驱动策略下做真机安装验收；无法安装时可不选该组件，改用真实串口或组织批准的虚拟串口驱动。

若 `setupc.exe` 不在常见安装目录，可设置 `MODBUSSIM_COM0COM_SETUPC` 为它的完整路径。开发环境也可用 `MODBUSSIM_COM0COM_INSTALLER` 指定经相同哈希校验的安装程序。已经用其他工具创建的虚拟 COM 端口会出现在串口设备列表中，可以直接选择，不要求由 Modbus-Sim 创建。完整第三方材料见 [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md)。

### 构建 Windows 安装包

`.github/workflows/modbus-sim-installers.yml` 在 Modbus-Sim 相关改动推送到 `main`、创建 `modbus-sim-v*` 标签或手动触发时构建 x64 安装包。构建过程运行 `packaging/prepare_com0com.py`，只从官方地址获取固定版本，验证签名二进制包、x64 安装程序和源码包的 SHA-256 后才交给 Inno Setup。第三方二进制不提交到 Git；最终安装包同时包含安装程序、对应源码、许可证、README 和校验清单。

本地 Windows 构建可依次执行：

```powershell
python -m pip install . pyinstaller==6.16.0
python packaging/prepare_com0com.py
pyinstaller packaging/frozen_entry.py --name Modbus-Sim --onedir --windowed --clean --noconfirm --collect-all modbus_sim --copy-metadata unilab-modbus-sim
& "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe" "/DMyAppVersion=0.2.0" "packaging\windows-installer.iss"
```

## 串口说明

- RS-485 与 RS-232 使用相同的 Modbus RTU 帧格式，但对应不同物理层；应用分别保存配置并在 RS-485 模式启用多设备总线支持。
- ASCII 使用 ASCII framer，示例缺省为 7E1；实际参数应与主站和串口适配器一致。
- 虚拟串口只建立操作系统串行字节通路；PTY 不保证模拟所有波特率、校验位和硬件流控行为。
- 应用验证的是 Modbus 协议和操作系统串口路径。终端电阻、偏置、收发方向控制、线序、电平及隔离仍需在真实 RS-485/RS-232 硬件上验收。

## 开发与测试

```bash
python3.11 -m pip install -e '.[test]'
python3.11 -m pytest
python3.11 -m build
```

协议测试使用真实 PyModbus 客户端。POSIX 环境会通过应用内置的 PTY 串口对验证 RTU RS-485、RTU RS-232 和 ASCII 的串行帧往返；Windows 测试通过受控命令替身验证 com0com 安装包哈希、UAC 调用、创建、冲突与移除逻辑。CI 还会启动冻结后的 Windows 应用并执行真实 Modbus TCP 往返。

## 边界

Modbus-Sim 只负责 Modbus 传输、从站数据模型、运行控制与可观测性。它不承诺真实硬件电气层正确性，也不承担 PLC 工作流编排、机器人或视觉设备仿真；这些能力应由 Uni-Lab OS 或其他独立仿真模块负责。
