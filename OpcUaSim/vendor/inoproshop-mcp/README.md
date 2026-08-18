# InoProShop MCP bundle

此目录包含 PLC-Sim 使用的 InoProShop LIMIT MCP 运行包：

```text
OpcUaSim/vendor/inoproshop-mcp/bundle.min.js
```

项目会自动发现该路径，不需要设置 `OPCUASIM_MCP_BUNDLE` 或用户 MCP JSON。
仍可通过环境变量或 CLI 的 `--bundle` 显式指定其他版本。

PLC-Sim 默认实际启动同目录的 `persistent-launcher.js`。它保持一个 InoProShop
IronPython 宿主和已打开工程，在多次 MCP 工具调用之间复用；`persistent_host.py`
是该宿主脚本。上游 `bundle.min.js` 保持不修改，由启动适配器在运行时接入常驻会话。

来源：项目维护者提供的 InoProShop LIMIT MCP 1.0.0 运行包。该包未随附标准开源
许可证，仅应在已获授权的环境中使用；向项目外复制或公开发布前必须确认授权。

OPC UA Server、Handshake Agent 和 Web GUI 的仿真功能不依赖该 bundle；只有
打开、编辑、编译 InoProShop 工程和提取 GVL 时需要它。
