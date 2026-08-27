# PLC-Sim

面向 Uni-Lab 设备联调的 OPC UA 仿真、握手代理与测试工具。

## 命名约定

- 仓库、产品、桌面应用和原生安装包统一使用 `PLC-Sim`；
- 命令行入口和 Release 标签分别使用 `plc-sim`、`plc-sim-v*`；
- Python 分发包与导入模块分别为 `unilab-plc-sim`、`plc_sim`；
- 配置环境变量统一使用 `PLCSIM_*` 前缀。

## 目录

- [`PLC-Sim`](./PLC-Sim/)：CSV 驱动的 OPC UA Server、PTLC/SZLab 仿真运行时及 Web GUI。PTLC 模式是 Uni-Lab OS 的 PLC-only 仿真后端，覆盖 55 个 PLC 动作、延迟执行器反馈和 IX8..IX12 传感器变化；支持独立调试自动补位及联合仿真幂等物料事件。工作流编排由 OS Backend 负责，机器人、相机、视觉和其他直连设备由独立模块仿真。SZLab 默认一次启动整个设备包，常驻 Robot、S04-S09 和 S1 HTTP Adapter。

项目已经包含公开演示变量表和 Python 依赖声明。源码运行仅支持 Python 3.11.x：

- pip：使用 Python 3.11 执行 `python -m pip install ./PLC-Sim`，然后运行 `plc-sim`；
- macOS：在 Finder 中双击 `start_gui.command`；启动器会自动创建环境并安装依赖；
- Windows：运行 `setup_venv.bat`，再运行 `start_all.bat`。

也可以直接安装运行依赖：

```powershell
python -m pip install -r PLC-Sim\requirements.txt
```
