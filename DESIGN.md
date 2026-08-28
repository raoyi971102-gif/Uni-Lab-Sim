---
name: Modbus-Sim
description: 面向现场联调的高密度现代工业协议控制台
colors:
  console-navy: "#1c2a38"
  console-border: "#0e1924"
  console-mark: "#8497a9"
  console-muted: "#9fb0bf"
  console-hover: "#2b3d4e"
  console-state: "#263747"
  industrial-blue: "#1769d2"
  industrial-blue-hover: "#0e58b8"
  selection-blue: "#e8f1ff"
  selection-fill: "#dceaff"
  selection-border: "#a9c8f4"
  selection-text: "#0d4f9f"
  document-border-active: "#6f9fda"
  document-title-active: "#164f94"
  changed-value: "#0d5fb9"
  run-green: "#16823b"
  run-light: "#a7efb9"
  run-border: "#347e4a"
  run-dot: "#35bd60"
  run-dark: "#1d492a"
  counter-green: "#69d48a"
  counter-blue: "#79b6ff"
  warning-amber: "#a15c00"
  error-red: "#c83434"
  error-light: "#ffd0d0"
  error-border: "#9b4646"
  error-dark: "#522424"
  error-dot: "#ff6868"
  counter-red: "#ff7777"
  danger-border: "#e1aaaa"
  error-box-text: "#8c2020"
  error-box-border: "#e3a6a6"
  canvas-cool: "#e9edf2"
  canvas-document: "#dfe4ea"
  surface-white: "#ffffff"
  surface-subtle: "#f6f8fa"
  line: "#cfd6df"
  line-strong: "#aeb8c5"
  line-icon: "#929fac"
  line-hover: "#8c99a8"
  line-table: "#e0e5eb"
  line-table-row: "#e2e7ec"
  line-subtle: "#e2e6eb"
  icon-muted: "#8a99a8"
  state-neutral: "#8290a0"
  state-inactive: "#a5afba"
  table-heading: "#374353"
  dialog-border: "#7f8b98"
  toast: "#273746"
  text: "#17202a"
  text-muted: "#586474"
typography:
  title:
    fontFamily: "-apple-system, BlinkMacSystemFont, Segoe UI, PingFang SC, Microsoft YaHei, sans-serif"
    fontSize: "12px"
    fontWeight: 700
    lineHeight: 1.35
  body:
    fontFamily: "-apple-system, BlinkMacSystemFont, Segoe UI, PingFang SC, Microsoft YaHei, sans-serif"
    fontSize: "13px"
    fontWeight: 400
    lineHeight: 1.35
  label:
    fontFamily: "-apple-system, BlinkMacSystemFont, Segoe UI, PingFang SC, Microsoft YaHei, sans-serif"
    fontSize: "10px"
    fontWeight: 600
    lineHeight: 1.3
  data:
    fontFamily: "SFMono-Regular, Consolas, Liberation Mono, Menlo, monospace"
    fontSize: "11px"
    fontWeight: 400
    lineHeight: 1.25
  dialog-title:
    fontFamily: "-apple-system, BlinkMacSystemFont, Segoe UI, PingFang SC, Microsoft YaHei, sans-serif"
    fontSize: "14px"
    fontWeight: 700
    lineHeight: 1.35
  product:
    fontFamily: "-apple-system, BlinkMacSystemFont, Segoe UI, PingFang SC, Microsoft YaHei, sans-serif"
    fontSize: "15px"
    fontWeight: 700
    lineHeight: 1
  icon-small:
    fontFamily: "-apple-system, BlinkMacSystemFont, Segoe UI, sans-serif"
    fontSize: "15px"
    fontWeight: 400
    lineHeight: 1
  icon-medium:
    fontFamily: "-apple-system, BlinkMacSystemFont, Segoe UI, sans-serif"
    fontSize: "16px"
    fontWeight: 400
    lineHeight: 1
  icon-add:
    fontFamily: "-apple-system, BlinkMacSystemFont, Segoe UI, sans-serif"
    fontSize: "18px"
    fontWeight: 400
    lineHeight: 1
  icon-close:
    fontFamily: "-apple-system, BlinkMacSystemFont, Segoe UI, sans-serif"
    fontSize: "20px"
    fontWeight: 400
    lineHeight: 1
rounded:
  glyph: "2px"
  control: "3px"
  command: "4px"
  dialog: "5px"
  counter: "8px"
  round: "50%"
spacing:
  xs: "4px"
  sm: "6px"
  md: "8px"
  lg: "10px"
  xl: "13px"
components:
  button-primary:
    backgroundColor: "{colors.industrial-blue}"
    textColor: "{colors.surface-white}"
    typography: "{typography.body}"
    rounded: "{rounded.control}"
    padding: "0 10px"
    height: "32px"
  button-primary-hover:
    backgroundColor: "{colors.industrial-blue-hover}"
    textColor: "{colors.surface-white}"
  input:
    backgroundColor: "{colors.surface-white}"
    textColor: "{colors.text}"
    typography: "{typography.body}"
    rounded: "{rounded.control}"
    padding: "5px 7px"
    height: "30px"
  document-window:
    backgroundColor: "{colors.surface-white}"
    textColor: "{colors.text}"
    rounded: "0"
---

# Design System: Modbus-Sim

## Overview

**Creative North Star: “The Field Service Console”**

Modbus-Sim 是面向长时间现场联调的工程桌面。用户已确认以方案 B 的多文档工作台为主，并采用方案 C 的设备树：并排寄存器窗口承担主要工作，传输、从站层级、连接状态和真实报文始终围绕数据表存在。

它借鉴 Modbus Poll 一类成熟工具的信息直接性，但不复制其品牌或旧式窗口外壳。视觉保持冷静、紧凑、平面；边框承担层级，颜色只服务于选择和运行状态。

**Key Characteristics:**

- 多文档数据表优先，最多两个工作窗口并排。
- 左侧以“从站 → 数据区”组织设备树，选中项使用工业蓝。
- 连接检查器和通信流量常驻，运行状态不只依赖颜色。
- 停止时编辑模型，运行时只编辑可写区实时值。

## Colors

冷灰与白色构成绝大部分工作面，深海军蓝固定顶部应用身份，工业蓝只用于选择和主操作；绿、琥珀、红分别表达运行、警告和错误。

### Primary

- **Industrial Blue** (`#1769d2`)：主按钮、当前文档、设备树选中项和可交互强调。
- **Console Navy** (`#1c2a38`)：全局应用栏，稳定产品边界。

### Neutral

- **Canvas Cool** (`#e9edf2`)：文档桌面底层。
- **Surface White** (`#ffffff`)：数据表、字段与主要工作表面。
- **Surface Subtle** (`#f6f8fa`)：侧栏、检查器与辅助工具带。
- **Line / Strong Line** (`#cfd6df` / `#aeb8c5`)：默认分隔和关键面板边界。
- **Text / Muted Text** (`#17202a` / `#586474`)：正文和次级说明。

**The State Owns Color Rule.** 高饱和色只表示选择、运行、警告或错误，不给普通容器制造彩色装饰。

## Typography

**Body Font:** 系统 UI 字体栈，优先平台原生中文字体。

**Label/Mono Font:** `SFMono-Regular, Consolas, Liberation Mono, Menlo, monospace`。

系统字体保证 Windows、macOS 与 Linux 的熟悉感；等宽字体用于地址、值、端点、计数器、时间戳和十六进制报文，实时刷新时不得改变列宽。

### Hierarchy

- **Title**（700，12px，1.35）：面板标题、表头和重要结构标签。
- **Body**（400，13px，1.35）：控件与工作区正文。
- **Label**（600，10px，1.3）：字段名、元数据和状态说明。
- **Data**（400，10–12px，1.25）：地址、寄存器值、端点与报文字节。
- **Product / Dialog Title**（700，15px / 14px）：产品锁定标识和模态标题。
- **Icon Glyphs**（400，15 / 16 / 18 / 20px）：树删除、窗口关闭、添加和对话框关闭符号；这些字号不用于正文。

**The Numbers Do Not Jump Rule.** 所有持续刷新的数字使用等宽字体或 tabular numerals。

## Layout

桌面由 42px 应用栏、44px 命令栏、弹性工作区、226px 通信流量和 26px 状态栏组成。宽屏工作区固定为 248px 设备树、弹性双文档桌面、286px 连接检查器；文档之间用 6px 间距和单像素边界分开。

在 1220px 以下，连接检查器成为右侧可开关面板；在 900px 以下，中间只显示当前文档，表格保持最小可读宽度并允许局部滚动。产品是桌面工程工具，600px 是受支持的最小工作宽度，不通过压缩字号模拟手机界面。

## Elevation & Depth

常驻界面无阴影，通过表面色阶、表头底色和 1px 边框建立深度。只有对话框、窄屏连接抽屉和 toast 使用结构性阴影，明确表示临时浮层。

**The Flat Workbench Rule.** 工作台静止时保持平面；阴影只属于需要压住当前任务的临时层。

## Shapes

工作区和表格保持直角，树字形使用 2px，字段与按钮使用 3px，命令按钮使用 4px，对话框使用 5px，小型计数标签使用 8px。50% 圆形仅用于 7px 状态灯；不把普通操作做成药丸按钮。

## Components

### Buttons

- 主按钮高 32px，工业蓝底、白字、3–4px 圆角；hover 使用更深的 `#0e58b8`。
- 次级按钮为白底灰边框；危险停止操作使用浅红底、红字和明确文字。
- `:focus-visible` 使用 2px `#005fcc` 实线焦点，不能被 hover 样式覆盖。

### Inputs / Fields

- 字段高 30px，白底、`#aeb8c5` 边框和 3px 圆角，内部间距 5px 7px。
- 运行锁定时禁用连接字段，透明度降低并保留“运行时锁定”文字。

### Navigation

- 顶部菜单是深色应用栏内的紧凑文字命令。
- 设备树行高 29–30px；选中数据区同时改变文字、背景与边界语义。
- 文档标签和窗口标题共同表达当前文档，关闭操作始终靠近标题。

### Register Document

寄存器文档由标题、局部工具栏、sticky 表头、27px 数据行和页脚组成。地址、初值、实时值、格式和权限持续可见；变化值使用蓝色加粗，读写权限使用 `R/W` 或 `R` 文字而非单独依赖颜色。

### Traffic Band

通信流量固定在底部，24px 行高、等宽报文字节并支持 Tx/Rx/Error 与文本筛选。错误行同时使用红色文字和浅红底。

## Do's and Don'ts

### Do:

- **Do** 让当前传输、端点、从站、数据区与运行状态持续可见。
- **Do** 保持 B 的并排文档效率和 C 的设备树层级。
- **Do** 用边框、对齐和稳定行高组织密集数据。
- **Do** 明确区分模型初值、运行实时值、只读区与可写区。

### Don't:

- **Don't** 用欢迎页、KPI 卡片或大块留白挤占寄存器工作空间。
- **Don't** 复制 Modbus Poll 的商标、图标或专有窗口外观。
- **Don't** 用颜色作为状态和权限的唯一线索。
- **Don't** 把连接和寄存器配置拆成需要频繁往返的多页向导。
