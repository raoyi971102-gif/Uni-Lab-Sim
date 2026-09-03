---
version: 1
slug: "plc-acceptance-gui-static-index-html"
primary_target: "plc-acceptance-kit/plc_acceptance/static/index.html"
related_targets: ["plc-acceptance-kit/plc_acceptance/static/style.css","plc-acceptance-kit/plc_acceptance/static/app.js"]
---

# SZLab PLC 自动验收证据台

- Scope: `plc-acceptance-kit/plc_acceptance/static/index.html` 及其同一 GUI 的样式和交互脚本。
- Visitor mode: Operate。
- Audience: 没有 Python 或前端环境、需要对 SZLab PLC 仿真、供应商软 PLC、真机台架或 FAT/SAT 现场执行标准验收的自动化工程师与交付人员。
- Job: 安装后一次点击运行完整门禁，持续看见运行状态、逐条结果、覆盖缺口和可归档报告。
- Primary action: 默认启动内置 PLC-Sim 的 L1 完整验收；切换到 L2/L3/L4 时必须填写 Endpoint 与 Namespace URI、绑定不可变候选包并确认受控测试模式；L3/L4 增加现场证据，L4 增加物料身份。
- Content: L0 基线、L1-L4 环境、按环境冻结的协议检查轮数、运行 ID、耗时、用例诊断、现场证据、覆盖边界、HTML 报告、ZIP 证据包和本机历史。
- Constraints: 中文优先；安装后无需代码环境；状态不能只靠颜色；任何通过结论必须标明证据等级；L3/L4 可写真机动作但只证明当前自动清单；人工/阻塞项和功能安全不得自动判为通过；不可跳过必跑用例。
- Form: Established PLC-Sim industrial console；这是现有产品的局部扩展，不发起概念赛，seed key 为 `not-applicable-local-extension`。
- First viewport: 左侧完成环境选择和一键运行，主按钮下常驻证据边界；右侧展示本次状态、进度、关键事实、逐条用例和报告操作。
- Visual system: 继承 PLC-Sim 的深色全局状态带、白色高密度工作面、细分隔线、表格优先和克制的青绿色主操作；PASSED、FAILED、BLOCKED 同时使用文字与颜色。
- Interaction: 默认无需配置；L2-L4 配置按需展开；真机模式明确警告将写入动作，运行时锁定输入并轮询状态；配置与历史读取失败各自在所属区域提供重试，不混入运行错误。
- Truth: 覆盖缺口保持显式，报告链接只有在真实报告生成后才可用，供应商候选包先计算并绑定摘要后再运行。
- Responsive: 窄屏纵向堆叠操作区与证据区，首屏仍能看到主操作和证据边界；宽表允许横向滚动，不压缩诊断内容。
- Memorable moment: 同一环境选择器从内置仿真一路展开到现场 FAT/SAT；每次运行都从 READY 进入 RUNNING，再以对应证据等级、可审计结果和可下载 ZIP 收束。
- Resolution: 方向合同已固化，无待定视觉方案。
