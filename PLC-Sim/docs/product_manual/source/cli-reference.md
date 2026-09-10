# 命令行参考

:::{admonition} 阅读角色
- **业务负责人**：不直接把本页命令用于生产设备。
- **开发或运维人员**：在本机或 CI 中复现 GUI 等价操作。
- **验收人员**：要求命令、参数和退出码进入证据包。
:::

日常联调优先使用 Web 页面。批量启动、无界面运行和自动化再使用命令行。完整参数以 `plc-sim <command> --help` 为准。

:::{warning}
当前默认配置没有通用用户认证。不要把公网地址、真实密码或生产 Endpoint 写进脚本仓库。
:::

## 统一入口

```text
plc-sim                 # 启动 Web GUI
plc-sim gui --help
plc-sim server --help
plc-sim szlab-handshake --help
plc-sim ptlc-handshake --help
plc-sim xuse-handshake --help
plc-sim ino --help
plc-sim --version
```

| 命令 | 作用 |
| --- | --- |
| `gui` | Web GUI，默认命令 |
| `server` | CSV 或 PTLC 节点的 OPC UA Server |
| `szlab-handshake` | SZLab 握手代理 |
| `ptlc-handshake` | PTLC V2 L2 握手代理 |
| `xuse-handshake` | XUSE 握手代理 |
| `handshake` | `szlab-handshake` 的旧别名 |
| `ino` | 可选 InoProShop MCP CLI |

等价模块入口：`python -m plc_sim`。

### Server

```bash
plc-sim server --host 127.0.0.1 --port 4855 \
  --csv data/szlab_plc_0810.csv \
  --ns-uri urn:xuse:sim --ns-index 4
```

```bash
plc-sim server --profile ptlc --host 127.0.0.1 --port 4855
```

可重复 `--csv`。`--no-occupancy-true` 禁止占位/空闲类节点默认 `TRUE`。

### SZLab 代理

```bash
plc-sim szlab-handshake list --config config/szlab_handshake.yaml
plc-sim szlab-handshake check --url opc.tcp://127.0.0.1:4855/xuse_sim/ --workflow all
plc-sim szlab-handshake --url opc.tcp://127.0.0.1:4855/xuse_sim/ --time-scale 10
```

常用参数：`--config`、`--package-config`、`--state-file`、`--s1-host`、`--s1-port`、`--no-s1-http`、`--position`、`--pump`、`--time-scale`、`--delay-ms`、`--poll-ms`、`--legacy-workflow-mode`。

### PTLC 代理

```bash
plc-sim ptlc-handshake list
plc-sim ptlc-handshake --config config/ptlc_handshake.yaml \
  --sensor-mode standalone --time-scale 10
```

常用参数：`--fault-file`、`--world-file`、`--state-file`、`--sensor-mode`。

### InoProShop MCP

```bash
plc-sim ino structure --project C:\project\XUSE.project
plc-sim ino extract --project C:\project\XUSE.project --out extracted.csv --all
```

源码目录也可运行 `python -m ino_mcp.cli`。仅 Windows，且依赖 InoProShop 与 MCP bundle。

## 源码启动器

| macOS | Windows | 用途 |
| --- | --- | --- |
| `start.command` | `start.bat` | 只启动 OPC UA Server |
| `start_szlab_handshake.command` | `start_szlab_handshake.bat` | 只启动 SZLab 代理 |
| `start_all.command` | `start_all.bat` | Server + SZLab 代理 |
| `start_gui.command` | `start_gui.bat` | Web GUI |
| — | `pick.bat` | 选择一份或多份 CSV |
| 启动器自动完成 | `setup_venv.bat` | 创建 `.venv` |

`PLCSIM_PORT=4860 ./start_all.command` 会让 Server 与代理使用同一端口。

## GUI HTTP 摘录

PLC-Sim GUI 在本机提供只供页面使用的管理接口，例如代理状态和 PTLC 世界更新。这些接口没有鉴权，不要对公网开放。远程挂接自检：

```bash
python tests/integration/remote_attach_check.py
```

## 测试入口

```bash
.venv/bin/python -m pytest
```

设置 `SZLAB_REFERENCE_ROOT` 或 `PTLC_REFERENCE_ROOT` 时，对应合同测试会核对比对仓库是否漂移。不要把参考仓库路径写进生产配置。
