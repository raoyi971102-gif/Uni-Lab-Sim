"""
ino_mcp —— PLC-Sim ↔ InoProShop MCP 桥接层
============================================================================
职责（仿照 pTLC_platformUI 的分层思想，把 IDE 交互从服务器/仿真里彻底剥离）:

    driver 层  :  client.py       —— 通用 MCP stdio JSON-RPC 客户端
    controller :  toolkit.py      —— 项目/POU/GVL/编译/下载 的业务级 API
    extractor  :  extractor.py    —— 项目结构遍历 + GVL → CSV 提取
    api / cli  :  cli.py          —— 命令行入口（open/edit/compile/download/extract/pipeline）

上层只用 toolkit 与 extractor，绝不直接摸 MCP 传输细节，方便日后替换到
其它 CODESYS 变体或直接走文件系统。
"""

from .client import McpClient, McpError
from .toolkit import InoToolkit, DownloadStrategy

__all__ = ["McpClient", "McpError", "InoToolkit", "DownloadStrategy"]
