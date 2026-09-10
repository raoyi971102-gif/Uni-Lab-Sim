# OPC UA Server

:::{admonition} 阅读角色
- **业务负责人**：确认点表版本、监听地址和不允许写入的点。
- **开发人员**：启动 Server、核对 Endpoint、命名空间和节点数量。
- **验收人员**：完成进程层和协议层就绪检查，并保存日志。
:::

OPC UA Server 读取点表并创建节点。它是握手代理和 Uni-Lab OS 共同连接的协议端点。没有这份服务，后面的联调都不能开始。

## 设备包或联调项目必须交付的内容

对照 Uni-Lab OS 产品手册中的 PLC 仿真器要求，Server 侧至少要能指出：

| 内容 | 说明 |
| --- | --- |
| 仿真器名称与版本 | `plc-sim --version` 或安装包版本 |
| 启动与停止方式 | 工作目录、命令和正常退出方法 |
| 点表 | 变量名、数据类型、读写方向、初始值和业务含义 |
| OPC UA 配置 | Endpoint、Namespace URI 或 Index、安全策略 |
| 健康检查 | 如何判断进程和协议服务就绪 |

缺少其中任何一项时，只能完成软件安装，不能声称 PLC 联调已经完成。握手规则和启动图对照表见[握手代理](handshake.md)和[与 Uni-Lab OS 联调](os-integration.md)。

## 启动

本机推荐命令：

```bash
plc-sim server \
  --host 127.0.0.1 \
  --port 4855 \
  --csv data/demo_variables.csv \
  --ns-uri urn:xuse:sim \
  --ns-index 4
```

Windows 源码目录也可使用 `start.bat`。`start_all.bat` / `start_all.command` 会同时拉起 Server 和 SZLab 代理；AI 和 CI 应分开启动，以便记录参数和退出码。

| 参数 | 含义 |
| --- | --- |
| `--host` / `--port` | 监听地址和端口。本机联调用 `127.0.0.1` |
| `--csv` | 可重复，后表中的同名节点按实现跳过或按界面提示处理 |
| `--profile csv` | 默认。按 CSV 建扁平节点 |
| `--profile ptlc` | 按 PTLC V2 嵌套 GVL 和真数组建节点，默认读 `config/ptlc_nodes.yaml` |
| `--ns-uri` | 命名空间 URI，优先使用 |
| `--ns-index` | 命名空间索引。只能用 Index 时，必须确认重启后编号不变 |
| `--no-occupancy-true` | 禁止把名称以“占位”或“空闲”结尾的节点初始化为 `TRUE` |
| `--connection-state` | 客户端连接状态 JSON，供 GUI 展示 |

默认 Endpoint：

```text
opc.tcp://127.0.0.1:4855/xuse_sim/
```

`ns=4` 和 `urn:xuse:sim` 是仓库示例，不是所有工程的事实。实际值以点表和工程 Namespace Array 为准。

## 点表规则

CSV 最小格式：

```text
Name,EnglishName,NodeType,DataType,NodeLanguage,NodeId
工站初始化,Station_Initialize,VARIABLE,BOOLEAN,Chinese,ns=4;s=uniab|工站初始化
```

支持的数据类型为 `BOOLEAN`、`INT16`、`INT32`、`FLOAT` 和 `STRING`。PTLC profile 另外支持 Byte、Double 和固定长度真数组。

- 点位名称、数据类型和读写方向必须与设备包点表一致；
- NodeId 不能根据中文名、英文名或数组下标拼接；
- Server 运行期间不要替换 CSV；
- 多份表合并前确认 NodeId 唯一。

从工程导出点表见[变量表与工程导出](variables.md)。

## 三层就绪检查

| 层级 | 要看什么 | 通过标准 |
| --- | --- | --- |
| 进程层 | `plc-sim server` 或 GUI 托管的 Server | 进程运行，日志没有持续重启 |
| 协议层 | OPC UA Endpoint | 可连接，命名空间和关键点位存在 |
| 系统层 | Uni-Lab OS 或其它主站 | 设备在线，订阅和写入没有类型错误 |

GUI 的“客户端连接”页可以看 Session 数和来源 IP。客户端源端口由操作系统临时分配，重连后可能变化。连接数为 0 时，系统层尚未通过。

## 最小只读检查

1. 用任意 OPC UA 客户端或 Uni-Lab OS 连接 Endpoint。
2. 读取一个只读状态点，核对类型和值。
3. 确认 Namespace URI 或 Index 与点表一致。
4. 还没有握手需求时，到此停止，不要写命令点。

写入测试属于握手和 OS 联调范围，见后续章节。

## 常见问题

| 现象 | 检查重点 |
| --- | --- |
| 页面可用但 Endpoint 连不上 | Server 是否启动、Host/Port 是否与客户端一致、防火墙是否拦截 |
| BadNodeIdUnknown | 点表未加载、NodeId 或命名空间不一致 |
| 写入类型错误 | 客户端参数类型与点表数据类型不一致 |
| 节点数量不对 | 是否加载了预期 CSV，多表合并是否丢掉重复节点 |
| 端口占用 | 是否已有旧 Server；先停旧进程，不要共享 `4855` |

完成 Server 检查后，需要工站行为时进入[握手代理](handshake.md)；需要接到 OS 时进入[与 Uni-Lab OS 联调](os-integration.md)。
