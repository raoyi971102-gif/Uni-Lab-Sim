# 故障排查

:::{admonition} 阅读角色
- **业务负责人**：提供使用场景和预期结果，不直接改调试接口。
- **开发或运维人员**：使用日志、CLI 和端口检查定位问题。
- **验收人员**：确认失败路径有记录，且没有用仿真结果冒充真机结论。
:::

先判断问题属于安装、页面、协议端点、点表、握手还是 OS 启动图。保留仿真器版本、点表路径、Endpoint、时间、动作名称和进程日志，再进行操作。

## 安装与本地启动

### `plc-sim` 命令不存在

先激活 PLC-Sim 的虚拟环境，再检查 `python -m pip show unilab-plc-sim`。源码安装应确认 `pip install -e .` 已成功。不要在系统 Python 和虚拟环境之间混装。Python 必须是 3.11.x。

### `.venv` 提示版本不对

启动器发现旧环境不是 3.11 时会停止。移走该目录后重新运行 `setup_venv.bat` 或对应 `.command`。

### GUI 打不开

1. 确认监听的是 `127.0.0.1:18765`，URL 带最后的 `/`。
2. 看启动终端是否报端口占用。
3. 安装包路径确认没有被系统隔离；macOS 用 Control-点击打开。
4. 源码路径确认 FastAPI/Uvicorn 已安装在同一个解释器中。

### Health 正常但工程页失败

工程打开不是 GUI 的必要条件。缺少 InoProShop、Node.js 或 MCP bundle 时，跳过工程，直接导入 CSV。

## 协议连接

### 页面可用但 OPC UA 连不上

网页只证明 GUI 进程正常。检查 Server 是否启动、Endpoint 是否写成 `opc.tcp://...` 而不是 `http://127.0.0.1:18765/`、Host 是否为客户端可达地址。

### BadNodeIdUnknown

点表未加载、NodeId 被改写，或 Namespace Index/URI 与 Server 不一致。重新导出点表，不要在代理里猜测 NodeId。

### 写入类型错误

客户端写入的 Python/JSON 类型与 CSV `DataType` 不一致，例如把布尔写成整数。以点表为准。

### 代理连接后一直等待

按这个顺序查：

1. `szlab-handshake check` 或 PTLC `list` 的只读结果；
2. 是否有 GUI 维护写入、第二个代理或外部控制器同时写同一节点；
3. 就绪位、提交边沿、Busy 和完成的顺序；
4. 动作超时是否过长，或完成位从未变化。

### 端口占用

`4855`、`18765`、`8055` 被旧实例占用时，先停旧进程。不要让多个 Server 共享同一 OPC UA 端口。

### OS 设备离线

按[与 Uni-Lab OS 联调](os-integration.md)做三层检查。OS 页面在线不等于 PLC 节点已建立会话。读取 OS 设备运行日志，并核对启动图中的 Endpoint 与当前仿真器完全一致。

## 快速健康检查

```bash
plc-sim --version
plc-sim server --help
plc-sim szlab-handshake list --config config/szlab_handshake.yaml
```

源码目录下的最小协议测试：

```bash
.venv/bin/python -m pytest tests/test_szlab_handshake_agent.py -q
.venv/bin/python -m pytest tests/test_szlab_handshake_opcua_integration.py -q
```

AI 和 CI 必须用 Server + Agent 的真实进程路径验证，不得以进程内假 OPC UA 对象作为唯一证据。

## 结果记录

每次变更至少记录：配置版本、CSV 摘要、Endpoint、Namespace URI、动作名称、请求/完成时间、结果和清理状态。出现真实机构、急停、互锁或物料/库位问题时，交给 PLC 供应商和设备包公开接口的所有者，不能在仿真器中伪造现场结论。
