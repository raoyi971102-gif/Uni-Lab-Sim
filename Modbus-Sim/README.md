# Modbus-Sim

Modbus-Sim 是 Uni-Lab-Sim 中独立的 Modbus 从站仿真应用。一份设备模型可以在四种传输方式之间切换：

- Modbus RTU over RS-485
- Modbus RTU over RS-232
- Modbus TCP
- Modbus ASCII

它提供本地 Web GUI 和无界面 CLI，适合在真实设备不可用时联调 Modbus 主站、驱动及 Uni-Lab 设备接入代码。

## 环境

- Python 3.11.x（当前不支持 3.10 或 3.12）
- 串行模式需要操作系统可访问的串口或 USB 转串口设备

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
- 导入或导出可版本管理的 YAML 配置；
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

## 串口说明

- RS-485 与 RS-232 使用相同的 Modbus RTU 帧格式，但对应不同物理层；应用分别保存配置并在 RS-485 模式启用多设备总线支持。
- ASCII 使用 ASCII framer，示例缺省为 7E1；实际参数应与主站和串口适配器一致。
- 应用验证的是 Modbus 协议和操作系统串口路径。终端电阻、偏置、收发方向控制、线序、电平及隔离仍需在真实 RS-485/RS-232 硬件上验收。

## 开发与测试

```bash
python3.11 -m pip install -e '.[test]'
python3.11 -m pytest
python3.11 -m build
```

协议测试使用真实 PyModbus 客户端。Linux 上安装 `socat` 后还会通过成对伪终端验证 RTU RS-485、RTU RS-232 和 ASCII 的串行帧往返；没有 `socat` 时这些用例会跳过。

## 边界

Modbus-Sim 只负责 Modbus 传输、从站数据模型、运行控制与可观测性。它不承诺真实硬件电气层正确性，也不承担 PLC 工作流编排、机器人或视觉设备仿真；这些能力应由 Uni-Lab OS 或其他独立仿真模块负责。
