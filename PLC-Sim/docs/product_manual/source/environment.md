# 环境与运行配置

:::{admonition} 阅读角色
- **业务负责人**：确认部署目的、点表来源、监听范围和成功标准。
- **开发或运维人员**：执行配置、启动、升级和故障处理。
- **验收人员**：核对版本、端口、数据目录、失败路径和回滚能力。
:::

PLC-Sim 的“环境”分成三层：软件安装决定有哪些命令，运行数据目录决定点表和状态写到哪里，启动参数决定监听地址和本次加载哪份配置。新手最容易把 GUI 地址填进 OPC UA Endpoint，或让两个实例抢同一端口。

## 默认端口

| 服务 | 默认地址 | 谁使用 |
| --- | --- | --- |
| GUI | `http://127.0.0.1:18765/` | 浏览器 |
| OPC UA Server | `opc.tcp://127.0.0.1:4855/xuse_sim/` | Uni-Lab OS、握手代理、其它 OPC UA 客户端 |
| SZLab S1 HTTP stand-in | `http://127.0.0.1:8055/api/v1` | SZLab S1 驱动 |

HTTP 管理页面地址不能填入 OPC UA `endpoint`。测试配置与生产配置分开保存，生产 PLC 地址不得出现在模拟启动参数中。

单机调试把 Server Host 设为 `127.0.0.1`。只有在防火墙和网络隔离已确认时才监听 `0.0.0.0`。源码里 Server 的默认 Host 是 `0.0.0.0`，本手册的本机步骤一律改成回环地址。

## 运行数据目录

wheel 和安装包内的演示 CSV、YAML 和 GUI 静态文件是只读包资源。上传的 CSV、提取结果和运行状态写入用户数据目录：

| 平台 | 路径 |
| --- | --- |
| Windows | `%LOCALAPPDATA%\PLC-Sim` |
| macOS | `~/Library/Application Support/PLC-Sim` |
| Linux | `$XDG_DATA_HOME/plc-sim` 或 `~/.local/share/plc-sim` |

`PLCSIM_DATA_DIR` 可统一覆盖数据目录。在源码目录中运行时使用 `data/`，不影响安装包行为。

日志、CSV 上传、状态快照和用户提取文件属于运行数据，不要提交到 Git。已忽略 `data/uploads/` 和 `data/runtime/`。

## 常用环境变量

这些变量由 shell 或系统环境读取，程序不会自动加载 `.env` 文件。

| 变量 | 作用 |
| --- | --- |
| `PLCSIM_CSV` | Server 默认加载的 CSV |
| `PLCSIM_PORT` | `start_all` 启动器中 Server 与代理共用的端口 |
| `PLCSIM_DATA_DIR` | 覆盖用户数据目录 |
| `PLCSIM_CONNECTION_STATE` | Server 与 GUI 共享的客户端连接状态文件，两者必须指向同一绝对路径 |
| `PLCSIM_MCP_BUNDLE` | `bundle.min.js` 路径 |
| `PLCSIM_INOPROSHOP_EXE` | `InoProShop.exe` 路径 |
| `PLCSIM_INOPROSHOP_PROFILE` | InoProShop profile |
| `PLCSIM_MCP_WORKSPACE` | MCP 工作区 |
| `PLCSIM_NODE` | `node` 命令或绝对路径 |
| `PLCSIM_MCP_CONFIG` | 自定义 MCP JSON |

账号、私钥和证书密码通过受控环境或密钥系统注入，不写入启动图、CSV 或本手册示例。

## 进程模型

PLC-Sim 至少可能同时存在三个进程：

1. **GUI**：只提供页面和启停入口；
2. **OPC UA Server**：按点表创建节点并监听 Endpoint；
3. **握手代理**：作为 OPC UA 客户端连接 Server。

Server 和代理必须是两个进程。代理不读取 CSV；因此 Server 必须先加载与工程或供应商点表一致的文件。换表顺序为：停止代理 → 停止 Server → 更换 CSV → 启动 Server → 启动代理。

远程 Linux 上如果 Server 和代理已由 systemd 或 Supervisor 托管，GUI 可以只挂接现有 Endpoint，不再占用端口或结束外部进程：

```bash
plc-sim gui --host 0.0.0.0 --port 18765 --no-open \
  --attach-url opc.tcp://127.0.0.1:4855/xuse_sim/ \
  --attach-csv data/demo_variables.csv
```

挂接模式保留在线变量读写，但禁用 GUI 内的 Server/代理启停按钮。两个进程的工作目录不同时，为两者设置相同的 `PLCSIM_CONNECTION_STATE`。

## 安全配置

| 项目 | 当前状态 | 说明 |
| --- | --- | --- |
| OPC UA 安全策略 | <span class="status status-limited">当前受限</span> | 默认 `NoSecurity`、允许匿名，仅适合开发或受控网络 |
| Web GUI 登录 | <span class="status status-unavailable">当前不可用</span> | 第一版不提供鉴权 |
| 在线下载 PLC 程序 | <span class="status status-unavailable">当前不可用</span> | GUI 无条件拒绝；旧环境变量不能重新打开 |
| 证书与用户名 | <span class="status status-config">需要配置</span> | 由主站和现场策略决定，不写进示例启动图 |

真实 PLC 下载必须由现场批准的部署流程完成，例如 pTLC `PlcProgramService` 的维护门、目标绑定、一次性授权和 `PLC_Deploy_*` 握手。仿真器不得自动重试下载。

## 时间倍率

`--time-scale` 只改变仿真等待。`10` 表示协议里的 30 秒在仿真中等 3 秒。真实 PLC 不应靠倍率改变物理时序。验收记录里必须写明本次倍率，避免把加速结果当成现场节拍。
