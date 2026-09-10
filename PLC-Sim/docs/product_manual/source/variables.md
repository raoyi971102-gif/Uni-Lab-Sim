# 变量表与工程导出

:::{admonition} 阅读角色
- **业务负责人**：确认点表来自哪份工程或供应商文件，以及导出范围。
- **开发人员**：从 GVL 导出 CSV，或整理已有点表后交给 Server。
- **验收人员**：核对 NodeId、数据类型和命名空间，禁止猜测补齐。
:::

“导出变量表”是把 PLC 工程或供应商点表变成 Server 能加载的 CSV。“导入”是把这份 CSV 交给 Server 建节点，不是把 CSV 写回 PLC 工程。

## 什么时候需要打开工程

| 已有材料 | 做法 |
| --- | --- |
| 供应商已提供完整 CSV，且 NodeId 可用 | 跳过工程，直接进入[OPC UA Server](opcua-server.md) |
| 只有 InoProShop `.project` | 在 Windows 上打开工程并提取 GVL |
| 只有截图或中文名称列表 | 不够。补齐类型、读写方向、单位和 NodeId 后再继续 |

打开工程需要同时满足：

- Windows；
- InoProShop V1.9.1.6（SP11 内核）；
- Node.js 18 或更高版本；
- 仓库内 `vendor/inoproshop-mcp/bundle.min.js`，或授权的同版本 bundle。

缺任一条件时，工程页为 <span class="status status-unavailable">当前不可用</span>。不要扫描或导入 Uni-Lab-OS、设备包兄弟仓库来绕过 MCP 接口。

## 页面操作

1. 打开 `http://127.0.0.1:18765/`，在工程区域填写绝对路径的 `.project`，点击“打开工程”。
2. 等待工程结构和 GVL 列表加载。需要确认代码可编译时再点“保存”或“编译”。
3. 进入“提取变量”，刷新列表并选择包含通讯变量的 GVL。
4. 预览数据，确认 `Name`、`EnglishName`、`NodeType`、`DataType`、`NodeLanguage`、`NodeId`。
5. 设置 Namespace index、NodeId 前缀和输出路径。`ns=4` 只是仓库示例。
6. 需要完整点表时选择全部变量并展开结构体；只要公开 symbol 时保持默认范围。
7. 提取并保存到受控目录。导出后不要在同一文件上并行修改。

“下载程序块”不是变量导出步骤。在线下载属于非幂等设备操作，GUI 当前默认拒绝。

## 命令行等价操作

```powershell
python -m plc_sim ino structure `
  --project "C:\project\XUSE.project" `
  -o "C:\project\evidence\project-structure.json"
```

导出 GVL：

```powershell
python -m plc_sim ino extract `
  --project "C:\project\XUSE.project" `
  --out "C:\project\evidence\szlab_variables.csv" `
  --gvl "Application/GVL_XUSE" `
  --all `
  --name-mode comment `
  --ns-index 4 `
  --ns-prefix "uniab|"
```

| 参数 | 说明 |
| --- | --- |
| `--gvl` | 可重复。不确定路径时先跑 `structure` |
| `--all` | 导出全部变量；不带该参数时只导出带 symbol attribute 的变量 |
| `--name-mode comment` | 使用中文注释，必须确认注释稳定 |
| `--ns-index` / `--ns-prefix` | 必须来自工程或点表，不能为了让代理连上而猜测 |

`pipeline` 可以一次完成“提取 + 启动 Server”，但不会启动握手代理：

```powershell
python -m plc_sim ino pipeline `
  --project "C:\project\XUSE.project" `
  --out "C:\project\evidence\szlab_variables.csv" `
  --gvl "Application/GVL_XUSE" --all `
  --serve --host 127.0.0.1 --port 4855
```

## CSV 合同

```text
Name,EnglishName,NodeType,DataType,NodeLanguage,NodeId
工站初始化,Station_Initialize,VARIABLE,BOOLEAN,Chinese,ns=4;s=uniab|工站初始化
```

支持类型：`BOOLEAN`、`INT16`、`INT32`、`FLOAT`、`STRING`。

仓库内置两份可直接使用的表：

- `data/demo_variables.csv`：最小演示；
- `data/szlab_plc_0810.csv`：与 Uni-Lab-SZLab 官方部署图对齐的点表。

使用自己的表时，把绝对路径传给 Server 或写入 `PLCSIM_CSV`。Server 必须加载与真机或驱动一致的点表；握手代理按节点名读写，不单独加载 CSV。

## 符号与工程历史

打开 `.project` 后，PLC-Sim 会在运行数据目录的 `plc-history/` 下建立按工程隔离的内容寻址快照。保存、POU 修改、符号 pragma 修改和下载前都会留档。

“符号导出”可逐变量增删 `{attribute 'symbol' := 'readwrite'}`，并可立即编译验证。下载策略：

| 策略 | 行为 |
| --- | --- |
| `save_compile` | 只保存并编译，绝不登录 PLC |
| `online` | GUI 当前无条件关闭 |

旧的 `PLCSIM_ALLOW_ONLINE_DEPLOY` 开关和二次确认不构成部署授权。

## 常见问题

| 现象 | 检查重点 |
| --- | --- |
| GUI 打不开工程 | Windows、InoProShop、Node.js、MCP bundle、`.project` 是否为绝对路径 |
| GVL 没有变量 | GVL 路径、symbol attribute、是否加了 `--all`、中文注释是否为空 |
| 导出后代理找不到节点 | Server 是否加载同一份 CSV，NodeId 和 Namespace 是否被改过 |
