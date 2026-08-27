# Uni-Lab-Sim

Uni-Lab-Sim 是面向 Uni-Lab 设备接入、工业协议和实验室联调的仿真工具仓库。每个一级目录都是边界清晰、可以独立安装、运行和测试的应用。

## 应用

| 应用 | 协议/场景 | 主要能力 |
| --- | --- | --- |
| [`PLC-Sim`](./PLC-Sim/) | OPC UA、PTLC、SZLab | CSV 变量表、PLC 动作与传感器仿真、握手代理、设备包运行时和 Web GUI |
| [`Modbus-Sim`](./Modbus-Sim/) | Modbus TCP、RTU RS-485、RTU RS-232、ASCII | 多从站设备模型、四类数据区、实时读写、报文监视、YAML 配置和 Web GUI |

## 快速开始

两个应用当前均要求 Python 3.11.x。建议分别创建虚拟环境，避免依赖和运行配置相互影响。

### PLC-Sim

```bash
python3.11 -m venv PLC-Sim/.venv
PLC-Sim/.venv/bin/python -m pip install ./PLC-Sim
PLC-Sim/.venv/bin/plc-sim
```

Windows、macOS 启动器以及 PTLC/SZLab 运行方式见 [`PLC-Sim/README.md`](./PLC-Sim/README.md)。

### Modbus-Sim

```bash
python3.11 -m venv Modbus-Sim/.venv
Modbus-Sim/.venv/bin/python -m pip install ./Modbus-Sim
Modbus-Sim/.venv/bin/modbus-sim
```

默认 GUI 地址为 <http://127.0.0.1:18865>。Windows 可运行 `Modbus-Sim/start_gui.bat`，macOS 可双击 `Modbus-Sim/start_gui.command`，Linux/macOS 终端可运行 `Modbus-Sim/start_gui.sh`。

Modbus-Sim 使用同一份设备模型支持四种传输方式：

- Modbus TCP
- Modbus RTU over RS-485
- Modbus RTU over RS-232
- Modbus ASCII

GUI 采用多文档寄存器工作台和设备树，可配置传输参数、从站、线圈、离散输入、保持寄存器及输入寄存器，并查看真实 Tx/Rx 报文。完整配置格式、无界面 CLI 和串口说明见 [`Modbus-Sim/README.md`](./Modbus-Sim/README.md)。

## 仓库结构

```text
Uni-Lab-Sim/
├── PLC-Sim/       # OPC UA 与实验室 PLC 仿真
├── Modbus-Sim/    # Modbus 从站与串口/TCP 仿真
└── .github/       # 各应用独立 CI
```

## 开发验证

```bash
PLC-Sim/.venv/bin/python -m pytest PLC-Sim

python3.11 -m venv Modbus-Sim/.venv
Modbus-Sim/.venv/bin/python -m pip install -e './Modbus-Sim[test]'
Modbus-Sim/.venv/bin/python -m pytest Modbus-Sim
```

Modbus-Sim 的自动化测试包含真实 TCP 客户端往返；安装 `socat` 的 Unix 环境还会通过成对伪终端验证 RTU RS-485、RTU RS-232 和 ASCII 帧。伪终端测试不等同于真实串口电气层验收，终端电阻、偏置、方向控制、电平和线序仍需在物理硬件上确认。

## 边界

- 本仓库负责设备和工业协议仿真，不承担 Uni-Lab OS 的工作流调度、物料/库位权威状态或机器人运动控制。
- `PLC-Sim` 与 `Modbus-Sim` 不通过源码路径互相导入；配置、依赖、CLI 和测试保持独立。
- 默认 GUI 面向本机和可信实验室网络，不包含公网鉴权能力。
