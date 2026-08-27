# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

主要用户是 PLC/自动化工程师与 Uni-Lab 设备接入开发者。他们在实验室设备尚未到位、不可占用或联调成本较高时，需要在本机复现设备通信端点并验证主站、驱动和控制逻辑。

## Product Purpose

Uni-Lab-Sim 在一个仓库中提供边界清晰、可独立安装的设备与工业协议仿真应用。当前新增的 Modbus-Sim 用同一套设备和寄存器模型支持 Modbus RTU over RS-485、Modbus RTU over RS-232、Modbus TCP 与 Modbus ASCII，使用户无需真实从站即可完成连接、读写和协议联调。

成功意味着用户可以配置一个符合现场参数的仿真从站，启动目标传输方式，观察真实协议请求与寄存器变化，并把经过验证的配置重复用于自动化测试。

## Positioning

Modbus-Sim 不是通用串口终端。它把四种 Modbus 传输方式统一到同一份设备、线圈、离散输入、保持寄存器和输入寄存器定义中，同时提供面向设备联调的实时操作界面和可复现配置。

## Operating Context

- 本地 Web GUI 是主要操作入口，命令行用于自动化、无界面运行和 CI。
- 用户需要配置传输参数、设备地址与寄存器初值，启动或停止仿真，实时查看和修改可写数据，并检查通信报文。
- 配置以 YAML 导入、导出和版本管理。
- 第一版默认单机使用，可按显式监听地址供实验室局域网访问。

## Capabilities and Constraints

- 支持 Modbus RTU over RS-485、Modbus RTU over RS-232、Modbus TCP 和 Modbus ASCII。
- GUI 覆盖传输参数配置、设备与寄存器编辑、启动停止、实时读写监控、通信报文日志及 YAML 导入导出。
- 中文优先，支持 Windows、macOS 与 Linux。
- 默认不提供登录鉴权；暴露到不可信网络不属于第一版使用场景。
- 第一版不包含复杂 PLC 行为编排、云端协作或真实硬件验收。
- RS-232/RS-485 的电气层与收发方向控制由操作系统、串口和适配器承担；仿真应用负责标准 Modbus 串行帧和设备语义。

## Brand Commitments

- 产品名使用 `Modbus-Sim`，与同仓库的 `PLC-Sim` 平级。
- Python 分发包、导入模块、命令行和环境变量分别使用 `unilab-modbus-sim`、`modbus_sim`、`modbus-sim` 与 `MODBUSSIM_*`。
- 产品文案保持工程化、准确、中文优先，不把仿真结果描述成真实硬件验证。

## Evidence on Hand

- `PLC-Sim/` 提供同仓库独立 Python 应用、CLI、本地 Web GUI、配置与测试的现有工程约定。
- 当前没有 Modbus-Sim 的既有视觉资产、客户证明或真实硬件测试记录；后续界面不得虚构这些证据。

## Product Principles

- 一份设备模型贯穿所有受支持传输方式。
- 默认配置应可理解、可验证、可版本管理。
- 运行状态、可写边界和协议错误必须清晰可见。
- GUI 与 CLI 使用同一运行时合同，不维护两套协议逻辑。
- 仿真证据与真实硬件证据始终明确区分。

## Accessibility & Inclusion

Web GUI 应支持键盘操作、清晰焦点、语义化控件、足够颜色对比度以及减少动态效果的系统偏好。
