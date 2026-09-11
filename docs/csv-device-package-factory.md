# 从 PLC 变量表生成设备包

PLC-Sim 现在把“设备包生成”作为继变量提取、功能块编辑、OPC UA 仿真之后的第四个产品功能。它沿用 `common.load_csv()` 的编码、SZLab
类型映射和握手后缀规则，把变量表编译成一个数据化设备包：

```powershell
python PLC-Sim/cli.py factory inspect C:\path\上位通讯变量.csv
python PLC-Sim/cli.py factory build C:\path\上位通讯变量.csv --out .\runtime_data\factory\my-device
python PLC-Sim/cli.py factory validate .\runtime_data\factory\my-device\simulation-spec.json
```

生成目录包含 `manifest.yaml`、`nodes.yaml`、`devices.yaml`、`handshakes.yaml`、
`evidence.json` 和 `review.yaml`。`evidence.json` 保存输入文件 SHA-256、编码、源行号和
单元格地址，便于审查和重复构建；`handshakes.yaml` 只从现有的初始化、参数下发、动作、
加工等中文后缀推导确定性通道。

完整第四功能的流水线是：结构体路径归组 → `SimulationSpecPatch`（AI 只能返回此结构）→
按设备输出 OS 设备包 Python 模块 → 把 SZLab `device.py` 作为 `plc.py` 通信驱动放进包内 →
设备业务层统一通过 `self.plc` 读写 OPC UA → 单进程通用握手运行时 →
happy/guard/reset/timeout 场景验证。
变量表不能证明的内容会进入 `review.yaml`，例如没有成对写入/完成节点的通道、未识别的
状态变量、未知数据类型和重复变量名。生成包默认标为 `L1`，不会把猜测的动作语义当成
Python 代码执行，也不会修改原始 CSV。后续接入 AI 时，AI 只需要针对这些审阅项返回带
证据引用的结构化补丁，再由编译器升级握手、行为和场景；这与 SZLab 的常驻 package
runtime 兼容，运行时仍由一个会话托管整包设备。
