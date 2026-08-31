"use strict";

const AREAS = {
  coils: { label: "线圈", code: "0x", glyph: "C", writable: true },
  discrete_inputs: { label: "离散输入", code: "1x", glyph: "DI", writable: false },
  holding_registers: { label: "保持寄存器", code: "4x", glyph: "HR", writable: true },
  input_registers: { label: "输入寄存器", code: "3x", glyph: "IR", writable: false },
};
const TRANSPORTS = {
  tcp: { label: "Modbus TCP", glyph: "IP" },
  "rtu-rs485": { label: "RTU over RS-485", glyph: "48" },
  "rtu-rs232": { label: "RTU over RS-232", glyph: "23" },
  ascii: { label: "Modbus ASCII", glyph: "A" },
};
const state = {
  config: null,
  runtime: { running: false },
  documents: [],
  activeDocument: null,
  rows: new Map(),
  expandedDevices: new Set(),
  selectedPoint: null,
  traffic: [],
  trafficSequence: 0,
  plcAddress: false,
  serialPorts: [],
  virtualSerial: null,
  toastTimer: null,
  refreshing: false,
  busy: false,
  editingUnit: null,
};

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const keyOf = (unitId, area) => `${unitId}:${area}`;

async function api(path, options = {}) {
  const request = { ...options, headers: { ...(options.headers || {}) } };
  if (request.body && typeof request.body !== "string" && !(request.body instanceof ArrayBuffer) && !(request.body instanceof Blob)) {
    request.headers["Content-Type"] = "application/json";
    request.body = JSON.stringify(request.body);
  }
  const response = await fetch(path, request);
  const type = response.headers.get("content-type") || "";
  const payload = type.includes("json") ? await response.json() : await response.text();
  if (!response.ok) {
    const detail = payload?.detail;
    const message = Array.isArray(detail)
      ? detail.map((item) => item.msg).join("；")
      : detail || payload || `请求失败（HTTP ${response.status}）`;
    throw new Error(message);
  }
  return payload;
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>'"]/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;",
  })[char]);
}

function toast(message, error = false) {
  const element = $("#toast");
  clearTimeout(state.toastTimer);
  element.textContent = message;
  element.classList.toggle("error", error);
  element.hidden = false;
  state.toastTimer = setTimeout(() => { element.hidden = true; }, 3200);
}

function setStatus(message) {
  $("#statusMessage").textContent = message;
}

async function runAction(action, successMessage) {
  if (state.busy) {
    toast("已有操作正在执行，请稍候", true);
    throw new Error("操作正在执行");
  }
  setBusy(true);
  try {
    setStatus("正在处理…");
    const result = await action();
    if (successMessage) toast(successMessage);
    return result;
  } catch (error) {
    toast(error.message, true);
    setStatus(error.message);
    throw error;
  } finally {
    setBusy(false);
  }
}

function setBusy(busy) {
  state.busy = busy;
  document.body.setAttribute("aria-busy", String(busy));
  if (busy) {
    $$(`button`).forEach((button) => {
      button.dataset.busyWasDisabled = String(button.disabled);
      button.disabled = true;
    });
  } else {
    $$(`button[data-busy-was-disabled]`).forEach((button) => {
      button.disabled = button.dataset.busyWasDisabled === "true";
      delete button.dataset.busyWasDisabled;
    });
    if (state.config) syncEditLocks();
  }
}

function syncEditLocks() {
  const locked = state.runtime.running;
  $$('[data-transport], [data-edit-unit], [data-delete-unit], #connectionForm input, #connectionForm select').forEach((element) => { element.disabled = locked; });
  $("#btnApplyConfig").disabled = locked;
  $("#btnImport").disabled = locked;
  $("#btnImportCsv").disabled = locked;
  $("#configLock").textContent = locked ? "运行时锁定" : "可编辑";
  renderVirtualSerial();
  renderRuntime();
}

function transportEndpoint(mode, spec) {
  if (mode === "tcp") return `tcp://${spec.host}:${spec.port}`;
  return `serial://${spec.device} · ${spec.baudrate} ${spec.bytesize}${spec.parity}${spec.stopbits}`;
}

function renderTransportList() {
  const active = state.config.active_transport;
  $("#transportList").innerHTML = Object.entries(TRANSPORTS).map(([mode, item]) => {
    const spec = state.config.transports[mode];
    const selected = mode === active;
    return `<button class="transport-option${selected ? " active" : ""}${selected && state.runtime.running ? " running" : ""}"
      type="button" data-transport="${mode}" ${state.runtime.running || state.busy ? "disabled" : ""}>
      <span class="transport-icon">${item.glyph}</span>
      <span class="transport-copy"><strong>${item.label}</strong><small>${escapeHtml(transportEndpoint(mode, spec))}</small></span>
      <i class="transport-state" aria-hidden="true"></i>
    </button>`;
  }).join("");
}

function renderDeviceTree() {
  const activeKey = state.activeDocument;
  $("#deviceTree").innerHTML = state.config.devices.map((device) => {
    const expanded = state.expandedDevices.has(device.unit_id);
    const areas = Object.entries(AREAS).map(([area, meta]) => {
      const count = device.areas[area].size;
      const active = activeKey === keyOf(device.unit_id, area);
      return `<button class="tree-area-row${active ? " active" : ""}"
        type="button" data-open-unit="${device.unit_id}" data-open-area="${area}" ${active ? 'aria-current="page"' : ""}>
        <span></span><span class="tree-glyph">${meta.glyph}</span><span>${meta.label}</span><span class="tree-count">${count}</span>
      </button>`;
    }).join("");
    return `<div class="tree-device">
      <div class="tree-device-row">
        <button class="tree-toggle-button" type="button" data-toggle-unit="${device.unit_id}" aria-label="展开或收起" aria-expanded="${expanded}">${expanded ? "▼" : "▶"}</button>
        <span class="tree-glyph">U</span><span class="tree-device-name" title="${escapeHtml(device.name)}">${escapeHtml(device.name)} <small>(${device.unit_id})</small></span>
        <span class="tree-actions"><button class="tree-edit" type="button" data-edit-unit="${device.unit_id}" title="编辑从站" ${state.runtime.running || state.busy ? "disabled" : ""}>✎</button>
        <button class="tree-delete" type="button" data-delete-unit="${device.unit_id}" title="删除从站" ${state.runtime.running || state.busy ? "disabled" : ""}>×</button></span>
      </div>
      <div class="tree-areas" ${expanded ? "" : "hidden"}>${areas}</div>
    </div>`;
  }).join("");
}

function renderConnectionForm() {
  const mode = state.config.active_transport;
  const spec = state.config.transports[mode];
  const disabled = state.runtime.running || state.busy ? "disabled" : "";
  if (mode === "tcp") {
    $("#connectionForm").innerHTML = `
      <label class="full"><span>监听地址</span><input name="host" value="${escapeHtml(spec.host)}" ${disabled} required></label>
      <label class="full"><span>TCP 端口</span><input name="port" type="number" min="1" max="65535" value="${spec.port}" ${disabled} required></label>`;
  } else {
    $("#connectionForm").innerHTML = `
      <label class="full"><span>串口设备</span><input name="device" list="serialPortOptions" value="${escapeHtml(spec.device)}" ${disabled} required></label>
      <label><span>波特率</span><select name="baudrate" ${disabled}>${options([1200, 2400, 4800, 9600, 19200, 38400, 57600, 115200], spec.baudrate)}</select></label>
      <label><span>数据位</span><select name="bytesize" ${disabled}>${options([7, 8], spec.bytesize)}</select></label>
      <label><span>校验位</span><select name="parity" ${disabled}>${options([["N", "None"], ["E", "Even"], ["O", "Odd"]], spec.parity)}</select></label>
      <label><span>停止位</span><select name="stopbits" ${disabled}>${options([1, 2], spec.stopbits)}</select></label>
      <label><span>超时（秒）</span><input name="timeout" type="number" min="0.01" step="0.1" value="${spec.timeout}" ${disabled} required></label>
      <label><span>重连延时（秒）</span><input name="reconnect_delay" type="number" min="0" step="0.1" value="${spec.reconnect_delay}" ${disabled} required></label>
      <label class="checkbox-field full"><input name="handle_local_echo" type="checkbox" ${spec.handle_local_echo ? "checked" : ""} ${disabled}><span>处理本地回显</span></label>`;
  }
  $("#btnApplyConfig").disabled = state.runtime.running || state.busy;
  $("#configLock").textContent = state.runtime.running ? "运行时锁定" : "可编辑";
}

function options(items, selected) {
  return items.map((item) => {
    const pair = Array.isArray(item) ? item : [item, item];
    return `<option value="${pair[0]}" ${String(pair[0]) === String(selected) ? "selected" : ""}>${pair[1]}</option>`;
  }).join("");
}

function readConnectionForm() {
  const form = new FormData($("#connectionForm"));
  const mode = state.config.active_transport;
  if (mode === "tcp") return { host: form.get("host").trim(), port: Number(form.get("port")) };
  return {
    device: form.get("device").trim(),
    baudrate: Number(form.get("baudrate")),
    bytesize: Number(form.get("bytesize")),
    parity: form.get("parity"),
    stopbits: Number(form.get("stopbits")),
    timeout: Number(form.get("timeout")),
    handle_local_echo: form.has("handle_local_echo"),
    reconnect_delay: Number(form.get("reconnect_delay")),
  };
}

async function applyConnection(showToast = true) {
  if (!$("#connectionForm").reportValidity()) throw new Error("请先补全连接设置");
  const payload = structuredClone(state.config);
  payload.transports[payload.active_transport] = readConnectionForm();
  const result = await api("/api/config", { method: "PUT", body: payload });
  state.config = result.config;
  renderAll();
  if (showToast) toast("连接设置已应用");
}

function initializeDocuments() {
  if (state.documents.length || !state.config.devices.length) return;
  const first = state.config.devices[0];
  const second = state.config.devices[1] || first;
  state.documents = [keyOf(first.unit_id, "holding_registers"), keyOf(second.unit_id, "coils")];
  state.activeDocument = state.documents[0];
  state.config.devices.forEach((device) => state.expandedDevices.add(device.unit_id));
}

async function openDocument(unitId, area) {
  const key = keyOf(unitId, area);
  if (!state.documents.includes(key)) {
    if (state.documents.length >= 2) {
      const replaceIndex = state.documents.findIndex((item) => item !== state.activeDocument);
      state.documents.splice(replaceIndex < 0 ? 0 : replaceIndex, 1, key);
    } else state.documents.push(key);
  }
  state.activeDocument = key;
  renderDeviceTree();
  await refreshDocuments();
}

function documentMeta(key) {
  const [unitText, area] = key.split(":");
  const unitId = Number(unitText);
  const device = state.config.devices.find((item) => item.unit_id === unitId);
  return { key, unitId, area, device, areaMeta: AREAS[area] };
}

async function refreshDocuments() {
  const valid = state.documents.filter((key) => {
    const meta = documentMeta(key);
    return meta.device && meta.areaMeta;
  });
  state.documents = valid;
  if (!valid.includes(state.activeDocument)) state.activeDocument = valid[0] || null;
  await Promise.all(valid.map(async (key) => {
    const meta = documentMeta(key);
    try {
      state.rows.set(key, await api(`/api/registers?unit_id=${meta.unitId}&area=${meta.area}`));
    } catch (error) {
      state.rows.set(key, { ...meta, rows: [], error: error.message });
    }
  }));
  renderDocuments();
  renderDeviceTree();
}

function renderDocuments() {
  $("#documentTabs").innerHTML = state.documents.map((key) => {
    const meta = documentMeta(key);
    return `<div class="document-tab${key === state.activeDocument ? " active" : ""}">
      <button type="button" data-activate-doc="${key}" ${key === state.activeDocument ? 'aria-current="page"' : ""}><span>${escapeHtml(meta.device.name)} · ${meta.areaMeta.label}</span></button>
      <button class="tab-close" type="button" data-close-doc="${key}" aria-label="关闭">×</button>
    </div>`;
  }).join("");
  if (!state.documents.length) {
    $("#documentGrid").innerHTML = `<div class="document-empty">从左侧设备树打开一个数据区</div>`;
    renderSelectedFacts();
    return;
  }
  $("#documentGrid").innerHTML = state.documents.map(renderDocumentWindow).join("");
  renderSelectedFacts();
}

function renderDocumentWindow(key) {
  const meta = documentMeta(key);
  const payload = state.rows.get(key);
  const rows = payload?.rows || [];
  const running = state.runtime.running;
  const rowHtml = rows.map((row) => {
    const activeValue = running ? row.live_value : row.initial_value;
    const changed = running && String(row.live_value) !== String(row.initial_value);
    return `<tr tabindex="0" data-edit-doc="${key}" data-edit-address="${row.address}">
      <td>${row.address + 1}</td><td class="data-value">${state.plcAddress ? row.plc_address : row.address}</td>
      <td title="${escapeHtml(row.alias)}">${escapeHtml(row.alias || "—")}</td>
      <td class="data-value initial-cell">${formatValue(row.initial_value, row.format)}</td>
      <td class="data-value live-cell${changed ? " value-changed" : ""}">${formatValue(activeValue, row.format)}</td>
      <td>${escapeHtml(row.format)}</td><td><span class="permission${row.writable ? "" : " readonly"}">${row.writable ? "R/W" : "R"}</span></td>
    </tr>`;
  }).join("");
  return `<article class="document-window${key === state.activeDocument ? " active" : ""}" data-doc-window="${key}">
    <header class="document-titlebar"><span class="tree-glyph">${meta.areaMeta.glyph}</span><h2>${escapeHtml(meta.device.name)} — ${meta.areaMeta.label}</h2>
      <span class="document-meta"><span>Unit ${meta.unitId}</span><span>FC ${meta.area === "coils" ? "01/05/15" : meta.area === "discrete_inputs" ? "02" : meta.area === "holding_registers" ? "03/06/16" : "04"}</span></span>
      <button type="button" data-close-doc="${key}" aria-label="关闭">×</button></header>
    <div class="document-toolbar"><button type="button" data-refresh-doc="${key}">刷新</button><button type="button" data-first-row="${key}">定位 0</button>
      <span class="range-label">${rows.length ? `0…${rows.length - 1}` : "无数据"}</span></div>
    <div class="register-table-wrap"><table class="register-table"><thead><tr><th>#</th><th>地址</th><th>别名</th><th>初值</th><th>${running ? "实时值" : "当前值"}</th><th>格式</th><th>权限</th></tr></thead>
      <tbody>${payload?.error ? `<tr class="empty-row"><td colspan="7">${escapeHtml(payload.error)}</td></tr>` : rowHtml}</tbody></table></div>
    <footer class="document-footer"><span>双击行编辑${running ? "实时值" : "定义与初值"}</span><span>${rows.length} 点</span></footer>
  </article>`;
}

function formatValue(value, format) {
  if (format === "bool") return value ? "ON" : "OFF";
  const number = Number(value);
  if (format === "hex") return `0x${(number & 0xFFFF).toString(16).toUpperCase().padStart(4, "0")}`;
  if (format === "binary") return (number & 0xFFFF).toString(2).padStart(16, "0");
  if (format === "int16") return String(number > 32767 ? number - 65536 : number);
  return String(value);
}

function renderSelectedFacts() {
  const target = $("#selectedDocumentFacts");
  if (!state.activeDocument) { target.innerHTML = "<div><dt>状态</dt><dd>未打开文档</dd></div>"; return; }
  const meta = documentMeta(state.activeDocument);
  const payload = state.rows.get(state.activeDocument);
  target.innerHTML = `<div><dt>从站</dt><dd>${escapeHtml(meta.device.name)} (${meta.unitId})</dd></div>
    <div><dt>数据区</dt><dd>${meta.areaMeta.label}</dd></div><div><dt>点数</dt><dd>${payload?.rows?.length || 0}</dd></div>
    <div><dt>访问</dt><dd>${meta.areaMeta.writable ? "读 / 写" : "只读"}</dd></div>`;
}

function openPointEditor(key, address) {
  const payload = state.rows.get(key);
  const row = payload?.rows.find((item) => item.address === Number(address));
  if (!row) return;
  const meta = documentMeta(key);
  const running = state.runtime.running;
  const canWriteLive = running && meta.areaMeta.writable;
  state.selectedPoint = { key, row };
  $("#pointDialogTitle").textContent = `${meta.device.name} · ${meta.areaMeta.label}`;
  $("#pointAddress").textContent = `${state.plcAddress ? row.plc_address : row.address}（协议偏移 ${row.address}）`;
  $("#pointAlias").value = row.alias;
  $("#pointDescription").value = row.description;
  $("#pointValue").value = running ? row.live_value : row.initial_value;
  $("#pointValueLabel").textContent = running ? "实时值" : "初值";
  const formats = meta.area === "coils" || meta.area === "discrete_inputs" ? [["bool", "布尔"]] : [["uint16", "无符号 16 位"], ["int16", "有符号 16 位"], ["hex", "十六进制"], ["binary", "二进制"]];
  $("#pointFormat").innerHTML = options(formats, row.format);
  [$("#pointAlias"), $("#pointFormat"), $("#pointDescription")].forEach((element) => { element.disabled = running; });
  $("#pointValue").disabled = running && !canWriteLive;
  $("#btnSavePoint").disabled = running && !canWriteLive;
  $("#pointDialogNote").textContent = running
    ? (canWriteLive ? "服务运行中：保存将立即写入仿真数据区；定义字段已锁定。" : "该数据区对 Modbus 客户端只读；请停止服务后修改初值。")
    : "服务停止时可编辑点位定义与下次启动使用的初值。";
  $("#pointDialog").showModal();
}

function parsePointValue(value, format) {
  if (format === "bool") {
    const normalized = value.trim().toLowerCase();
    if (["true", "1", "on"].includes(normalized)) return true;
    if (["false", "0", "off"].includes(normalized)) return false;
    throw new Error("布尔值请输入 true/false、on/off 或 1/0");
  }
  const parsed = Number(value);
  if (!Number.isInteger(parsed) || parsed < -32768 || parsed > 65535) throw new Error("寄存器值必须在 -32768..65535 范围内");
  return parsed;
}

async function savePoint(event) {
  event.preventDefault();
  const selected = state.selectedPoint;
  if (!selected) return;
  await runAction(async () => {
    const meta = documentMeta(selected.key);
    const value = parsePointValue($("#pointValue").value, $("#pointFormat").value);
    const path = state.runtime.running
      ? `/api/devices/${meta.unitId}/areas/${meta.area}/values/${selected.row.address}`
      : `/api/devices/${meta.unitId}/areas/${meta.area}/points/${selected.row.address}`;
    const body = state.runtime.running ? { value } : {
      value, alias: $("#pointAlias").value.trim(), description: $("#pointDescription").value.trim(), format: $("#pointFormat").value,
    };
    state.rows.set(selected.key, await api(path, { method: "PUT", body }));
    $("#pointDialog").close();
    renderDocuments();
  }, state.runtime.running ? "实时值已写入" : "点位定义已保存");
}

function formatUptime(seconds = 0) {
  const value = Math.floor(seconds);
  return [Math.floor(value / 3600), Math.floor(value / 60) % 60, value % 60].map((item) => String(item).padStart(2, "0")).join(":");
}

function renderRuntime() {
  const runtime = state.runtime;
  $("#stateBadge").className = `state-badge${runtime.running ? " running" : runtime.last_error ? " error" : ""}`;
  $("#stateBadge b").textContent = runtime.running ? "运行中" : runtime.last_error ? "启动失败" : "已停止";
  $("#inlineState").className = `inline-state${runtime.running ? " running" : ""}`;
  $("#inlineState").innerHTML = `<i></i>${runtime.running ? "运行中" : "已停止"}`;
  $("#footerState").textContent = runtime.running ? "服务运行中" : "就绪";
  $(".status-led").classList.toggle("running", runtime.running);
  $("#txCount").textContent = runtime.tx || 0; $("#rxCount").textContent = runtime.rx || 0; $("#errorCount").textContent = runtime.errors || 0;
  $("#uptimeValue").textContent = formatUptime(runtime.uptime_seconds);
  $("#endpointValue").textContent = runtime.endpoint || "—";
  $("#transportStatus").textContent = runtime.running ? "服务运行中" : "未运行";
  $("#factTransport").textContent = TRANSPORTS[runtime.transport]?.label || runtime.transport || "—";
  $("#factConnections").textContent = runtime.connections || 0; $("#factDevices").textContent = state.config?.devices.length || 0; $("#factErrors").textContent = runtime.errors || 0;
  $("#lastError").hidden = !runtime.last_error; $("#lastError").textContent = runtime.last_error || "";
  $("#btnStart").disabled = runtime.running || state.busy; $("#btnStop").disabled = !runtime.running || state.busy; $("#btnAddDevice").disabled = runtime.running || state.busy; $("#btnTreeAdd").disabled = runtime.running || state.busy;
  $("#btnImport").disabled = runtime.running || state.busy; $("#btnImportCsv").disabled = runtime.running || state.busy;
  $("#trafficLiveLabel").textContent = runtime.running ? "实时捕获已开启" : "等待服务启动";
  renderVirtualSerial();
}

function renderVirtualSerial() {
  const info = state.virtualSerial;
  if (!info) return;
  const isWindows = info.platform.startsWith("win");
  const pair = info.active_pair;
  $("#virtualBackend").textContent = info.backend === "pty" ? "系统 PTY" : info.backend === "com0com" ? "com0com" : "不可用";
  $("#virtualMessage").textContent = info.message;
  $("#virtualPortInputs").hidden = !isWindows || Boolean(pair);
  $("#virtualEndpoints").hidden = !pair;
  $("#virtualSimulatorPort").textContent = pair?.simulator_port || "—";
  $("#virtualClientPort").textContent = pair?.client_port || "—";
  $("#btnInstallVirtualDriver").hidden = !isWindows || info.driver_installed || !info.installer_available;
  $("#virtualDriverLink").hidden = !isWindows || info.driver_installed || info.installer_available;
  if (info.driver_url) $("#virtualDriverLink").href = info.driver_url;
  const locked = state.runtime.running || state.busy;
  $("#virtualPortA").disabled = locked || Boolean(pair);
  $("#virtualPortB").disabled = locked || Boolean(pair);
  $("#btnInstallVirtualDriver").disabled = locked || !info.can_install_driver;
  $("#btnCreateVirtual").disabled = locked || Boolean(pair) || !info.can_create;
  $("#btnRemoveVirtual").disabled = locked || !pair;
}

async function refreshSerialPorts() {
  const ports = await api("/api/serial-ports");
  state.serialPorts = ports.ports || [];
  $("#serialPortOptions").innerHTML = state.serialPorts.map((port) => `<option value="${escapeHtml(port.device)}">${escapeHtml(port.description || "")}</option>`).join("");
}

async function createVirtualSerial() {
  await runAction(async () => {
    const body = { port_a: $("#virtualPortA").value.trim(), port_b: $("#virtualPortB").value.trim() };
    state.virtualSerial = await api("/api/virtual-serial", { method: "POST", body });
    const pair = state.virtualSerial.active_pair;
    if (pair && state.config.active_transport !== "tcp") {
      const payload = structuredClone(state.config);
      payload.transports[payload.active_transport].device = pair.simulator_port;
      state.config = (await api("/api/config", { method: "PUT", body: payload })).config;
    }
    await refreshSerialPorts();
    renderAll();
  }, "虚拟串口对已创建");
}

async function installVirtualSerialDriver() {
  if (!window.confirm("安装 com0com 虚拟串口驱动？Windows 将弹出 UAC 授权窗口。")) return;
  await runAction(async () => {
    state.virtualSerial = await api("/api/virtual-serial/driver", { method: "POST" });
    await refreshSerialPorts();
    renderAll();
  }, "com0com 驱动已安装，可以创建虚拟串口对");
}

async function removeVirtualSerial() {
  const pair = state.virtualSerial?.active_pair;
  if (!pair || !window.confirm(`确认移除虚拟串口对 ${pair.simulator_port} ↔ ${pair.client_port}？`)) return;
  await runAction(async () => {
    state.virtualSerial = await api("/api/virtual-serial", { method: "DELETE" });
    await refreshSerialPorts();
    renderAll();
  }, "虚拟串口对已移除；原连接路径仍保留在配置中");
}

function renderTraffic() {
  const query = $("#trafficSearch").value.trim().toLowerCase();
  const visible = state.traffic.filter((entry) => {
    if (entry.error && !$("#showErrors").checked) return false;
    if (!entry.error && entry.direction === "Tx" && !$("#showTx").checked) return false;
    if (!entry.error && entry.direction === "Rx" && !$("#showRx").checked) return false;
    return !query || JSON.stringify(entry).toLowerCase().includes(query);
  });
  $("#trafficRows").innerHTML = visible.length ? visible.map((entry) => `<tr class="${entry.error ? "error" : ""}">
    <td>${escapeHtml(new Date(entry.timestamp).toLocaleTimeString("zh-CN", { hour12: false, fractionalSecondDigits: 3 }))}</td>
    <td class="direction-${entry.direction.toLowerCase()}">${entry.direction}</td><td>${escapeHtml(entry.transport)}</td><td>${entry.unit_id ?? "—"}</td>
    <td>${entry.function_code == null ? "—" : `${entry.function_name} (${entry.function_code})`}</td><td>${entry.address ?? "—"}</td><td>${entry.count ?? "—"}</td>
    <td title="${escapeHtml(entry.data_hex)}">${escapeHtml(entry.data_hex)}</td></tr>`).join("")
    : `<tr class="empty-row"><td colspan="8">${state.traffic.length ? "没有符合筛选条件的报文" : "服务启动后显示真实 Modbus 收发报文"}</td></tr>`;
  if ($("#trafficAutoScroll").checked) $("#trafficTableWrap").scrollTop = $("#trafficTableWrap").scrollHeight;
}

function renderAll() {
  renderTransportList(); renderDeviceTree(); renderConnectionForm(); renderRuntime(); renderVirtualSerial(); renderDocuments();
}

async function refreshLiveDocuments() {
  await Promise.all(state.documents.map(async (key) => {
    const meta = documentMeta(key);
    const payload = await api(`/api/registers?unit_id=${meta.unitId}&area=${meta.area}`);
    state.rows.set(key, payload);
    const windowElement = document.querySelector(`[data-doc-window="${key}"]`);
    if (!windowElement) return;
    payload.rows.forEach((row) => {
      const rowElement = windowElement.querySelector(`[data-edit-address="${row.address}"]`);
      const cell = rowElement?.querySelector(".live-cell");
      if (!cell) return;
      cell.textContent = formatValue(row.live_value, row.format);
      cell.classList.toggle("value-changed", String(row.live_value) !== String(row.initial_value));
    });
  }));
}

async function refreshRuntime() {
  if (state.refreshing || state.busy) return;
  state.refreshing = true;
  try {
    const wasRunning = state.runtime.running;
    state.runtime = await api("/api/state");
    if (state.runtime.traffic_sequence < state.trafficSequence) {
      state.traffic = []; state.trafficSequence = 0;
    }
    const traffic = await api(`/api/traffic?after=${state.trafficSequence}`);
    state.trafficSequence = traffic.sequence;
    if (traffic.entries.length) state.traffic = [...state.traffic, ...traffic.entries].slice(-500);
    if (wasRunning !== state.runtime.running) {
      renderAll(); await refreshDocuments();
    } else {
      renderRuntime();
      if (state.runtime.running) await refreshLiveDocuments();
    }
    renderTraffic();
    setStatus(state.runtime.running ? `${state.runtime.endpoint} · 正在仿真 ${state.config.devices.length} 个从站` : "配置就绪；启动服务后接受 Modbus 客户端连接");
  } catch (error) { setStatus(`状态刷新失败：${error.message}`); }
  finally { state.refreshing = false; }
}
async function startService() {
  await runAction(async () => {
    await applyConnection(false);
    state.runtime = await api("/api/start", { method: "POST", body: {} });
    state.traffic = []; state.trafficSequence = 0;
    renderAll(); await refreshDocuments();
  }, "Modbus 服务已启动");
}
async function stopService() {
  await runAction(async () => {
    state.runtime = await api("/api/stop", { method: "POST" });
    renderAll(); await refreshDocuments();
  }, "Modbus 服务已停止");
}
function nextUnitId() {
  for (let id = 1; id <= 247; id += 1) if (!state.config.devices.some((item) => item.unit_id === id)) return id;
  return 247;
}

function openDeviceEditor(unitId = null) {
  state.editingUnit = unitId;
  const device = unitId == null ? null : state.config.devices.find((item) => item.unit_id === unitId);
  $("#deviceDialogMode").textContent = device ? "编辑设备模型" : "设备模型";
  $("#deviceDialogTitle").textContent = device ? `${device.name}（Unit ${device.unit_id}）` : "添加 Modbus 从站";
  $("#btnSaveDevice").textContent = device ? "保存" : "添加";
  $("#newUnitId").value = device?.unit_id ?? nextUnitId();
  $("#newDeviceName").value = device?.name ?? "New Device";
  $("#sizeCoils").value = device?.areas.coils.size ?? 16;
  $("#sizeDiscreteInputs").value = device?.areas.discrete_inputs.size ?? 16;
  $("#sizeHoldingRegisters").value = device?.areas.holding_registers.size ?? 16;
  $("#sizeInputRegisters").value = device?.areas.input_registers.size ?? 16;
  $("#deviceDialog").showModal();
}
async function importYaml(file) {
  const text = await file.text();
  await runAction(async () => {
    const result = await api("/api/config/yaml", { method: "PUT", headers: { "Content-Type": "application/yaml" }, body: text });
    state.config = result.config; state.documents = []; state.rows.clear(); state.expandedDevices.clear(); initializeDocuments(); renderAll(); await refreshDocuments();
  }, `已导入 ${file.name}`);
}

async function importRegistersCsv(file) {
  if (!window.confirm("导入 CSV 将替换全部设备与寄存器地址表，连接设置会保留。是否继续？")) return;
  const data = await file.arrayBuffer();
  await runAction(async () => {
    const result = await api("/api/registers/csv", { method: "PUT", headers: { "Content-Type": "text/csv" }, body: data });
    state.config = result.config; state.documents = []; state.rows.clear(); state.expandedDevices.clear(); initializeDocuments(); renderAll(); await refreshDocuments();
  }, `已导入 ${file.name}；连接设置保持不变`);
}

async function boot() {
  try {
    const [version, config, runtime, ports, virtualSerial] = await Promise.all([api("/api/version"), api("/api/config"), api("/api/state"), api("/api/serial-ports"), api("/api/virtual-serial")]);
    $("#versionLabel").textContent = `v${version.version}`;
    state.config = config; state.runtime = runtime; state.serialPorts = ports.ports || []; state.virtualSerial = virtualSerial;
    $("#serialPortOptions").innerHTML = state.serialPorts.map((port) => `<option value="${escapeHtml(port.device)}">${escapeHtml(port.description || "")}</option>`).join("");
    initializeDocuments(); renderAll(); await refreshDocuments(); await refreshRuntime();
    window.setInterval(refreshRuntime, 1000);
  } catch (error) { toast(`载入失败：${error.message}`, true); setStatus(`载入失败：${error.message}`); }
}

document.addEventListener("click", async (event) => {
  const target = event.target.closest("button, [data-menu-action]");
  if (!target || state.busy) return;
  try {
    if (target.dataset.transport) {
      const result = await runAction(() => api(`/api/transport/${target.dataset.transport}`, { method: "POST" }), "传输方式已切换");
      state.config = result.config; renderAll();
    } else if (target.dataset.toggleUnit) {
      const id = Number(target.dataset.toggleUnit); state.expandedDevices.has(id) ? state.expandedDevices.delete(id) : state.expandedDevices.add(id); renderDeviceTree();
    } else if (target.dataset.openArea) await openDocument(Number(target.dataset.openUnit), target.dataset.openArea);
    else if (target.dataset.editUnit) openDeviceEditor(Number(target.dataset.editUnit));
    else if (target.dataset.deleteUnit) {
      const id = Number(target.dataset.deleteUnit);
      if (window.confirm(`确认删除从站 ${id}？`)) {
        const result = await runAction(() => api(`/api/devices/${id}`, { method: "DELETE" }), "从站已删除");
        state.config = result.config; state.documents = state.documents.filter((key) => !key.startsWith(`${id}:`)); initializeDocuments(); renderAll(); await refreshDocuments();
      }
    } else if (target.dataset.activateDoc) { state.activeDocument = target.dataset.activateDoc; renderDocuments(); renderDeviceTree(); }
    else if (target.dataset.closeDoc) { state.documents = state.documents.filter((key) => key !== target.dataset.closeDoc); state.rows.delete(target.dataset.closeDoc); state.activeDocument = state.documents[0] || null; renderDocuments(); renderDeviceTree(); }
    else if (target.dataset.refreshDoc) await runAction(refreshDocuments, "寄存器已刷新");
    else if (target.dataset.firstRow) document.querySelector(`[data-doc-window="${target.dataset.firstRow}"] .register-table-wrap`).scrollTop = 0;
    else if (target.id === "btnStart") await startService();
    else if (target.id === "btnStop") await stopService();
    else if (target.id === "btnApplyConfig") await runAction(() => applyConnection(false), "连接设置已应用");
    else if (["btnImport"].includes(target.id) || target.dataset.menuAction === "import") $("#yamlFile").click();
    else if (target.id === "btnImportCsv" || target.dataset.menuAction === "import-csv") $("#csvFile").click();
    else if (target.id === "btnInstallVirtualDriver") await installVirtualSerialDriver();
    else if (target.id === "btnCreateVirtual") await createVirtualSerial();
    else if (target.id === "btnRemoveVirtual") await removeVirtualSerial();
    else if (["btnAddDevice", "btnTreeAdd"].includes(target.id) || target.dataset.menuAction === "device") openDeviceEditor();
    else if (target.id === "btnClearTraffic") await runAction(async () => { await api("/api/traffic", { method: "DELETE" }); state.traffic = []; state.trafficSequence = 0; renderTraffic(); }, "报文记录已清空");
    else if (target.id === "btnToggleInspector" || target.dataset.menuAction === "connection") { const open = $("#inspector").classList.toggle("open"); $("#btnToggleInspector").setAttribute("aria-expanded", String(open)); }
    else if (target.dataset.menuAction === "traffic") $("#trafficSearch").focus();
    else if (target.id === "addressBaseToggle") { state.plcAddress = !state.plcAddress; target.setAttribute("aria-pressed", String(state.plcAddress)); target.textContent = state.plcAddress ? "显示协议偏移" : "显示 PLC 基址"; renderDocuments(); }
    else if (target.hasAttribute("data-close-dialog")) target.closest("dialog").close();
  } catch (_) { /* runAction already surfaced the error. */ }
});

$("#documentGrid").addEventListener("dblclick", (event) => {
  const row = event.target.closest("[data-edit-address]");
  if (row) openPointEditor(row.dataset.editDoc, row.dataset.editAddress);
});
$("#documentGrid").addEventListener("keydown", (event) => {
  const row = event.target.closest("[data-edit-address]");
  if (row && event.key === "Enter") openPointEditor(row.dataset.editDoc, row.dataset.editAddress);
});
$("#pointForm").addEventListener("submit", (event) => savePoint(event).catch(() => {}));
$("#deviceForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const body = { unit_id: Number($("#newUnitId").value), name: $("#newDeviceName").value.trim(), sizes: {
      coils: Number($("#sizeCoils").value), discrete_inputs: Number($("#sizeDiscreteInputs").value),
      holding_registers: Number($("#sizeHoldingRegisters").value), input_registers: Number($("#sizeInputRegisters").value),
    } };
    const oldUnit = state.editingUnit;
    const path = oldUnit == null ? "/api/devices" : `/api/devices/${oldUnit}`;
    const result = await runAction(() => api(path, { method: oldUnit == null ? "POST" : "PUT", body }), oldUnit == null ? "从站已添加" : "从站已更新");
    state.config = result.config;
    if (oldUnit != null && oldUnit !== body.unit_id) {
      const remap = (key) => key?.startsWith(`${oldUnit}:`) ? `${body.unit_id}:${key.split(":")[1]}` : key;
      state.documents = state.documents.map(remap); state.activeDocument = remap(state.activeDocument); state.expandedDevices.delete(oldUnit);
    }
    state.rows.clear(); state.expandedDevices.add(body.unit_id); $("#deviceDialog").close(); renderAll(); await refreshDocuments();
  } catch (_) { /* runAction already surfaced the error. */ }
});
$("#yamlFile").addEventListener("change", (event) => { const [file] = event.target.files; if (file) importYaml(file).catch(() => {}); event.target.value = ""; });
$("#csvFile").addEventListener("change", (event) => { const [file] = event.target.files; if (file) importRegistersCsv(file).catch(() => {}); event.target.value = ""; });
[$("#showTx"), $("#showRx"), $("#showErrors"), $("#trafficSearch")].forEach((element) => element.addEventListener("input", renderTraffic));

boot();
