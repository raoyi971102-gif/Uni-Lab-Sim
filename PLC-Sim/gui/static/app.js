// ==========================================================================
// PLC-Sim GUI 前端
// ==========================================================================
"use strict";

function dismissAppSplash() {
  const splash = document.getElementById("appSplash");
  if (!splash) return;
  splash.classList.add("is-hidden");
  window.setTimeout(() => splash.remove(), 260);
}

window.addEventListener("load", () => window.setTimeout(dismissAppSplash, 120), { once: true });

// 版本 marker —— F12 Console 里能看到. 如果你看到的是旧样式但这一行没打印,
// 说明你的浏览器根本没执行这份 app.js (纯缓存旧文件).
const GUI_BUILD = "2026-08-20_szlab-package-runtime";
console.log("%c[PLC-Sim] GUI build " + GUI_BUILD, "color:#0f766e;font-weight:bold");

const $ = (id) => document.getElementById(id);
const el = (sel, ctx = document) => ctx.querySelector(sel);
const els = (sel, ctx = document) => Array.from(ctx.querySelectorAll(sel));

// ---------------- 通用 API 客户端 ----------------
async function api(method, url, body, timeoutMs = 0) {
  const opts = { method, headers: {} };
  if (body !== undefined) {
    opts.headers["content-type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const controller = timeoutMs > 0 ? new AbortController() : null;
  const timeoutId = controller
    ? setTimeout(() => controller.abort(), timeoutMs)
    : null;
  if (controller) opts.signal = controller.signal;

  let resp;
  try {
    resp = await fetch(url, opts);
  } catch (netErr) {
    if (netErr.name === "AbortError") {
      throw new Error(`请求超时（${Math.round(timeoutMs / 1000)} 秒），请刷新状态后重试`);
    }
    // 典型: 后端崩了/断开、CORS、DNS
    throw new Error(
      "后端连接失败 (" + netErr.message + ")。请检查启动 GUI 的那个 cmd 窗口是否有 Python traceback；" +
      "有的话把整段贴给我。"
    );
  } finally {
    if (timeoutId !== null) clearTimeout(timeoutId);
  }
  const text = await resp.text();
  let data;
  try { data = text ? JSON.parse(text) : {}; }
  catch (_) { data = { ok: false, message: text }; }
  if (!resp.ok) throw new Error(data.detail || data.message || ("HTTP " + resp.status + ": " + text.slice(0, 500)));
  return data;
}

const get  = (u)      => api("GET", u);
const post = (u, b, timeoutMs = 0) => api("POST", u, b || {}, timeoutMs);

async function requireBackendCapability(name, label) {
  const version = await get("/api/version");
  if (!version.capabilities?.[name]) {
    throw new Error(
      `当前页面已包含${label}，但常驻 GUI 后端仍是旧版本。` +
      "请关闭启动 GUI 的终端/窗口，重新运行 start_gui.bat 或 plc-sim gui，再刷新页面。"
    );
  }
}

function showResult(node, ok, text) {
  if (node.classList.contains("inline-result")) {
    node.classList.toggle("success", ok);
    node.classList.toggle("error", !ok);
  } else {
    node.className = "result-box " + (ok ? "success" : "error");
  }
  node.textContent = text;
}

function setBusyDisabled(disabled) {
  for (const b of els("button")) {
    if (b.id === "btnClearLog") continue;    // 日志清空永远可点
    if (disabled && !b.hasAttribute("data-before-busy-disabled")) {
      b.setAttribute("data-before-busy-disabled", b.disabled ? "1" : "0");
      b.disabled = true;
    } else if (!disabled && b.hasAttribute("data-before-busy-disabled")) {
      b.disabled = b.getAttribute("data-before-busy-disabled") === "1";
      b.removeAttribute("data-before-busy-disabled");
    }
  }
}

// ---------------- 状态显示 ----------------
let lastServerRunning = false;
let currentAppState = null;

function formatConnectionDuration(connectedAt) {
  const seconds = Math.max(0, Math.floor(Date.now() / 1000 - Number(connectedAt || 0)));
  if (seconds < 60) return `${seconds} 秒`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)} 分 ${seconds % 60} 秒`;
  const hours = Math.floor(seconds / 3600);
  return `${hours} 小时 ${Math.floor((seconds % 3600) / 60)} 分`;
}

function renderServerConnections(server) {
  const info = server.connections || {};
  const tcpCount = Number(info.tcp_connection_count || 0);
  const sessionCount = Number(info.session_count || 0);
  $("tcpConnectionCount").textContent = `${tcpCount} 个 TCP 连接`;
  $("opcSessionCount").textContent = `${sessionCount} 个会话`;
  const status = $("connectionTelemetryStatus");
  const tbody = $("connectionTable").querySelector("tbody");

  status.className = "telemetry-status";
  if (!server.running) {
    status.textContent = "未运行";
    $("connectionMessage").textContent =
      "启动 OPC UA Server 后显示客户端 IP、源端口与 Session 状态。";
    tbody.innerHTML =
      '<tr><td colspan="4" class="empty-cell">当前没有客户端连接</td></tr>';
    return;
  }
  if (!info.available) {
    status.textContent = info.stale ? "数据已过期" : "等待上报";
    status.classList.add("waiting");
    $("connectionMessage").textContent =
      "服务器正在运行，等待连接遥测数据。外部托管模式需使用支持遥测的新版本 server.py。";
    tbody.innerHTML =
      '<tr><td colspan="4" class="empty-cell">暂未取得客户端连接数据</td></tr>';
    return;
  }

  status.textContent = "实时";
  status.classList.add("on");
  $("connectionMessage").textContent =
    "显示当前活动 TCP 连接；客户端源端口由操作系统临时分配，重连后可能变化。";
  const clients = Array.isArray(info.clients) ? info.clients : [];
  if (!clients.length) {
    tbody.innerHTML =
      '<tr><td colspan="4" class="empty-cell">Server 正在监听，当前没有客户端连接</td></tr>';
    return;
  }
  tbody.innerHTML = clients.map(client => {
    const sessionState = String(client.session_state || "None");
    const active = sessionState === "Activated";
    const sessionText = active
      ? "Session 已建立"
      : sessionState === "Created"
        ? "Session 待激活"
        : sessionState === "Closed"
          ? "Session 已关闭"
          : "仅 TCP 连接";
    return `<tr>` +
      `<td class="connection-client">${escapeHtml(client.host || "未知地址")}</td>` +
      `<td class="connection-port">${escapeHtml(client.port ?? "--")}</td>` +
      `<td><span class="session-state${active ? " active" : ""}">${sessionText}</span></td>` +
      `<td class="connection-duration">${formatConnectionDuration(client.connected_at)}</td>` +
      `</tr>`;
  }).join("");
}

function renderState(s) {
  const firstState = currentAppState === null;
  currentAppState = s;
  $("dotMcp").className = "status-dot " + (s.mcp_connected ? "on" : (s.busy === "opening" ? "busy" : ""));
  $("dotServer").className = "status-dot " + (s.server.running ? "on" : (s.server.stopping ? "busy" : ""));
  $("dotAgent").className = "status-dot " + (s.agent.running ? "on" : (s.agent.stopping ? "busy" : ""));
  const mcpSession = s.mcp_session || {};
  $("statusMcpText").textContent = s.mcp_connected
    ? (mcpSession.persistent
        ? `工程常驻${mcpSession.host_pid ? ` · PID ${mcpSession.host_pid}` : ""}`
        : "已连接")
    : "未连接";
  const runText = (p) =>
    p.stopping ? "停止中"
      : p.running ? (p.attached ? "运行中（外部托管）" : "运行中")
        : "已停止";
  $("statusServerText").textContent = runText(s.server);
  $("statusAgentText").textContent = runText(s.agent);

  const stateMessage = s.last_error || (s.busy ? `正在${s.busy}` : "");
  $("topBusy").textContent = stateMessage;
  $("topBusy").classList.toggle("hidden", !stateMessage);
  $("topBusy").classList.toggle("error", Boolean(s.last_error));
  $("pidServer").textContent = s.server.pid ? `PID ${s.server.pid}` : "PID --";
  $("pidAgent").textContent = s.agent.pid ? `PID ${s.agent.pid}` : "PID --";
  const runtime = s.agent.state || s.agent.ptlc_state || null;
  const runtimeNode = $("agentRuntimeSummary");
  if (runtimeNode) {
    if (s.agent.profile === "szlab" && runtime?.schema === "unilab.package_simulation/v1") {
      const active = Array.isArray(runtime.active_runs) ? runtime.active_runs.length : 0;
      const coverage = runtime.coverage?.counts || {};
      runtimeNode.textContent =
        `设备包会话 ${runtime.state || "running"} · 活动动作 ${active} · ` +
        `事件 ${Number(runtime.sequence || 0)} · 已建模 ${Number(coverage.modeled || 0)} · ` +
        `外部适配 ${Number(coverage.external || 0)}`;
      runtimeNode.classList.add("success");
      runtimeNode.classList.remove("error");
    } else if (s.agent.running) {
      runtimeNode.textContent = "代理已启动，等待首个运行状态快照";
      runtimeNode.classList.remove("success", "error");
    } else {
      runtimeNode.textContent = "尚未启动仿真会话";
      runtimeNode.classList.remove("success", "error");
    }
  }
  $("serverEndpoint").textContent = s.server.endpoint || "未启动";
  renderServerConnections(s.server);
  syncMonitorProfile(s.server.csv_id, s.server.running);
  if (s.project && document.activeElement !== $("projectPath")) $("projectPath").value = s.project;

  setBusyDisabled(!!s.busy);
  $("btnServerStart").disabled = !!s.busy || s.server.running || s.server.stopping;
  $("btnServerStop").disabled = !!s.busy || s.server.stopping || !s.server.running || s.server.attached;
  $("btnAgentStart").disabled = !!s.busy || s.agent.running || s.agent.stopping;
  $("btnAgentStop").disabled = !!s.busy || s.agent.stopping || !s.agent.running || s.agent.attached;
  setAgentFormDisabled(s.agent.running || s.agent.stopping);
  $("btnClose").disabled = !!s.busy || !s.project;
  updateVariableControls();

  if (s.server.running && !lastServerRunning) {
    loadServerVariables({ reset: true });
    refreshMonitoredVariables({ announce: false });
    scheduleMonitorRefresh();
  } else if (!s.server.running && lastServerRunning) {
    clearServerVariables("服务已停止。启动 OPC UA Server 后可继续选择变量。");
    markMonitorsOffline();
    scheduleMonitorRefresh();
  } else if (firstState && !s.server.running) {
    clearServerVariables("启动 OPC UA Server 后即可选择在线变量。");
    markMonitorsOffline();
  }
  lastServerRunning = s.server.running;
}

async function refreshState() {
  try { renderState(await get("/api/state")); }
  catch (e) { console.warn("refreshState:", e); }
}

// ---------------- 标签切换 ----------------
els(".tab").forEach(t => t.addEventListener("click", () => {
  els(".tab").forEach(x => {
    x.classList.remove("active");
    x.removeAttribute("aria-current");
  });
  els(".panel").forEach(x => x.classList.remove("active"));
  t.classList.add("active");
  t.setAttribute("aria-current", "page");
  $("tab-" + t.dataset.tab).classList.add("active");
  el(".workspace").scrollTop = 0;
  if (t.dataset.tab === "sim" && currentAppState?.server?.running) {
    loadServerVariables();
  }
}));
