---
version: 1
slug: "modbus-sim-gui-static-index-html"
primary_target: "Modbus-Sim/gui/static/index.html"
related_targets: ["Modbus-Sim/gui/static/style.css","Modbus-Sim/gui/static/app.js"]
---

# Modbus-Sim 主工作台

- Scope: `Modbus-Sim/gui/static/index.html` 及其同一 GUI 的样式和交互脚本。
- Visitor mode: Operate。
- Audience: 在本机或实验室局域网进行联调的 PLC/自动化工程师与 Uni-Lab 设备接入开发者。
- Job: 配置一个 Modbus 从站模型，选择传输方式并启动服务，在同一工作区观察和修改寄存器、确认通信请求及错误。
- Primary action: 校验当前配置后启动仿真服务；运行时主操作变为停止服务和编辑可写值。
- Content: 四种传输配置、设备列表、四类数据区、寄存器地址/别名/初值/实时值、Tx/Rx/Error 计数、通信报文、YAML 导入导出。
- Constraints: 中文优先；桌面高密度但响应式；默认无鉴权；不虚构硬件证据；只读区不可从 GUI 写入；状态不能只靠颜色。
- Direction: 用户已确认以方案 B 的多文档工程桌面为主，采用方案 C 的设备树。以 Modbus Poll 为工艺标杆，保持表格优先、状态常驻、短操作路径，避免营销后台卡片化。
- Memorable moment: 选择 TCP、RTU-485、RTU-232 或 ASCII 后，连接参数、端点摘要和寄存器工作区立即同步；启动后同一表格从配置初值自然转为带通信状态的实时数据。
- Composition: 顶部全局状态与命令带；左侧传输选择及“从站 → 四类数据区”设备树；中央最多并排两个寄存器文档；右侧连接检查器；底部常驻通信流量。
- Resolution: 构图选择已完成，无待定视觉方向。
