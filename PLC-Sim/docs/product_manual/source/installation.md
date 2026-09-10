# 系统安装

:::{admonition} 阅读角色
- **首次使用者**：先安装并验证仿真器，再导入自己的点表。
- **开发或运维人员**：选择安装方式，维护环境、源码版本和启动配置。
- **验收人员**：确认安装来源、版本、示例启动和停止流程均可追溯。
:::

本页按“**安装 PLC-Sim → 启动示例点表 → 确认页面和协议端口**”的顺序操作。安装时不要求提前准备 Uni-Lab OS 或真实 PLC。

:::{important}
第一次启动固定使用应用自带的示例点表。它只用于验证安装和本机协议端口，不能代替设备包联调，也不能代替真机安全验收。
:::

## 1. 选择安装路径

| 路径 | 适合谁 | 得到什么 |
| --- | --- | --- |
| **原生安装包** | 只要启动 GUI、Server 和握手代理，不修改源码 | 可双击启动的桌面应用，不需要本机 Python |
| **pip / 源码可编辑安装** | 需要改配置、跑测试或接入 CI | `plc-sim` 命令和可编辑源码 |

源码路径要求 Python 3.11.x；安装包把解释器打进应用，使用者不必单独装 Python。3.10、3.12 和其它版本当前不受支持。

## 2. 安装前检查

| 项目 | 要求 |
| --- | --- |
| 操作系统 | Windows 10/11 x64、Ubuntu 22.04+ 或等价 glibc 2.35+ Linux x64、macOS 10.15+（Apple Silicon 或 Intel） |
| 磁盘空间 | 至少 1 GB |
| 网络 | 源码安装时能访问 PyPI 和 Git 仓库；安装包路径只需下载对应文件 |
| 本机端口 | 默认占用 `18765`（GUI）、`4855`（OPC UA）、可选 `8055`（S1 HTTP） |
| InoProShop | 只有打开和导出 `.project` 时才需要，且仅 Windows |

如果只要导入已有 CSV，不要把 InoProShop 列成安装前置条件。

## 3. 路径一：使用原生安装包

从 [GitHub Releases](https://github.com/raoyi971102-gif/Uni-Lab-Sim/releases) 下载与系统匹配的 PLC-Sim 安装包，标签为 `plc-sim-v*`。

| 平台 | 安装包 |
| --- | --- |
| Windows 10/11 x64 | `PLC-Sim-Setup-Windows-x64-*.exe` |
| Debian/Ubuntu 22.04+ x64 | `PLC-Sim-Linux-x64-*.deb` |
| 其它 Linux x64 | `PLC-Sim-Linux-x64-*.tar.gz` |
| Apple Silicon | `PLC-Sim-macOS-arm64-*.dmg` |
| Intel Mac | `PLC-Sim-macOS-x64-*.dmg` |

当前安装包没有商业代码签名。Windows 可能显示 SmartScreen；macOS 使用临时签名且尚未经过 Apple 公证。校验同一 Release 中的 `SHA256SUMS.txt` 后再打开。macOS 首次启动按住 Control 点击应用，选择“打开”。

Windows 安装后从开始菜单启动。Debian/Ubuntu 安装 DEB 后可运行 `plc-sim`。其它 Linux 解压便携包后运行目录中的 `PLC-Sim`。macOS 把 `.app` 拖入“应用程序”即可。

Linux DEB 示例：

```bash
sudo apt install ./PLC-Sim-Linux-x64-v*.deb
plc-sim
```

完成后继续第 5 节“验证安装”。

## 4. 路径二：pip 或源码安装

在 `PLC-Sim` 目录创建独立虚拟环境：

```bash
python3.11 -m venv .venv
.venv/bin/python -m pip install -e .
```

Windows 把 `bin/python` 换成 `.venv\Scripts\python.exe`。

### Windows / macOS 启动器

```bat
setup_venv.bat
start_gui.bat
```

`setup_venv.bat` 会创建 `.venv` 并安装 `requirements.txt`。已有 `.venv` 不是 Python 3.11 时，启动器会停止并提示；移走旧目录后再运行。

macOS 可双击 `start_gui.command`。首次启动会创建 `.venv`；依赖文件变化时会自动同步。

### 统一命令

```bash
plc-sim --version
plc-sim --help
```

不传子命令时启动 Web GUI。系统没有把 Scripts 目录加入 `PATH` 时，使用 `python -m plc_sim --help`。

## 5. 验证安装

安装完成后至少记录以下结果：

| 检查 | 通过标准 |
| --- | --- |
| 命令或应用可启动 | `plc-sim --version` 能打印版本，或安装包能打开 GUI |
| GUI | 浏览器打开 `http://127.0.0.1:18765/`，不是连接失败页 |
| 示例 Server | 日志出现点表路径、节点数量和 Endpoint |
| 端口未冲突 | `4855`、`18765` 没有被其它实例占用 |
| 停止 | 先停代理（如已启动），再停 Server，进程不再监听 |

源码安装可用示例点表做最小启动：

```bash
plc-sim server --host 127.0.0.1 --port 4855 --csv data/demo_variables.csv
```

另开终端：

```bash
plc-sim gui --host 127.0.0.1 --port 18765 --no-open
```

不要仅凭网页能打开就判断协议链路正常。

## 6. 停止与卸载

关闭顺序：握手代理 → 确认节点回到安全初始态 → OPC UA Server → GUI。

Windows 安装包从“应用和功能”卸载。Linux DEB 使用 `sudo apt remove plc-sim`。源码安装删除对应 `.venv` 即可，不要提交运行数据目录。
