# PLC-Sim 产品使用说明书

产品定位、业务能力和核心对象统一见[认识 PLC-Sim](overview.md)。本页只用于选择适合当前任务的阅读入口。

<div class="manual-meta">
适用环境：本机或受控实验室网络　·　手册版本：2026.09.10　·　PLC-Sim 0.2.6
</div>

<div class="entry-links">
<p><strong>PLC-Sim 页面</strong>：安装完成后打开 <code>http://127.0.0.1:18765/</code>。</p>
<p><strong>OPC UA Endpoint</strong>：默认 <code>opc.tcp://127.0.0.1:4855/xuse_sim/</code>。页面能打开不等于协议服务已就绪。</p>
</div>

:::{warning}
不要把本地开发端口直接暴露到公网。默认 OPC UA 允许匿名访问且使用 `NoSecurity`；Web GUI 也没有登录鉴权。生产或共享环境应限制监听地址，并在入口前配置网络访问控制。
:::

## 从哪里开始

- **已有可用环境**：先读[认识产品](overview.md)，再进入[仿真器页面上手](console.md)。
- **从零安装**：先完成[系统安装](installation.md)，用示例点表启动 Server，再按[OPC UA Server](opcua-server.md)核对 Endpoint。
- **从工程导出点表**：按[变量表与工程导出](variables.md)提取 CSV，再导入 Server。
- **联调设备包**：启动握手代理后，按[与 Uni-Lab OS 联调](os-integration.md)接到 OS 启动图。
- **验收与排查**：使用[故障排查](troubleshooting.md)和[命令行参考](cli-reference.md)。

## 本手册如何描述能力

同一功能在安装包、源码和当前运行实例中可能处于不同阶段。本手册统一使用以下状态：

| 状态 | 含义 |
| --- | --- |
| <span class="status status-ready">当前可用</span> | 本次安装已启动、连接并完成对应检查。 |
| <span class="status status-config">需要配置</span> | 代码已实现，但依赖点表、工程、凭证、外部软件或运行参数。 |
| <span class="status status-limited">当前受限</span> | 只有 CLI 或特定平台支持，或能力有明确限制。 |
| <span class="status status-unavailable">当前不可用</span> | 接口已关闭、依赖未安装，或界面只是占位。 |
| <span class="status status-experimental">实验验证</span> | 只在隔离测试中验证，尚未批准用于生产。 |

“源码已定义”不等于“当前安装已启动”，仿真通过也不等于真机已完成安全验收。各页会写明这些边界。

```{toctree}
:caption: 认识与快速上手
:maxdepth: 1

overview
console
```

```{toctree}
:caption: 系统安装与配置
:maxdepth: 1

installation
environment
```

```{toctree}
:caption: 使用
:maxdepth: 1

opcua-server
variables
handshake
os-integration
```

```{toctree}
:caption: 参考
:maxdepth: 1

troubleshooting
cli-reference
```
