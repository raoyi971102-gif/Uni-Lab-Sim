// ==========================================================================
// OpcUaSim GUI 前端
// ==========================================================================
"use strict";

// 版本 marker —— F12 Console 里能看到. 如果你看到的是旧样式但这一行没打印,
// 说明你的浏览器根本没执行这份 app.js (纯缓存旧文件).
const GUI_BUILD = "2026-08-02_parallel-robot-lock";
console.log("%c[OpcUaSim] GUI build " + GUI_BUILD, "color:#3ecf8e;font-weight:bold");

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

function showResult(node, ok, text) {
  if (node.classList.contains("inline-result")) {
    node.style.color = ok ? "#86efac" : "#fda4af";
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
  $("topBusy").style.color = s.last_error ? "#fda4af" : "#fcd34d";
  $("pidServer").textContent = s.server.pid ? `PID ${s.server.pid}` : "PID --";
  $("pidAgent").textContent = s.agent.pid ? `PID ${s.agent.pid}` : "PID --";
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
  els(".tab").forEach(x => x.classList.remove("active"));
  els(".panel").forEach(x => x.classList.remove("active"));
  t.classList.add("active");
  $("tab-" + t.dataset.tab).classList.add("active");
  el(".workspace").scrollTop = 0;
  if (t.dataset.tab === "sim" && currentAppState?.server?.running) {
    loadServerVariables();
  }
}));

// ---------------- 项目栏 ----------------
$("btnOpen").onclick = async () => {
  const path = $("projectPath").value.trim();
  if (!path) return alert("请填 .project 路径");
  try {
    await post("/api/project/open", { path });
    await refreshState();
    // 打开成功后, 后台悄悄预热 (拉所有 POU/GVL/DUT 的声明+实现塞满缓存)
    // 不 await —— fire-and-forget, 让用户可以立刻操作其它 tab
    warmProjectInBackground();
  } catch (e) { alert("打开失败: " + e.message); }
};

let _warmInflight = false;
async function warmProjectInBackground() {
  if (_warmInflight) return;
  _warmInflight = true;
  try {
    // 用 fetch 而不是 alert-弹错的 post, 因为这是后台任务
    const t0 = Date.now();
    const r = await fetch("/api/project/warm", { method: "POST" });
    if (!r.ok) throw new Error("HTTP " + r.status);
    const data = await r.json();
    const dt = ((Date.now() - t0) / 1000).toFixed(1);
    console.log(`[warm] ${dt}s, ${data.warmed} 对象 (POU=${data.kinds.POU}, GVL=${data.kinds.GVL}, DUT=${data.kinds.DUT})`);
    // 项目预热完 → editables 已经缓存, 自动加载列表
    try {
      const er = await get("/api/project/editables");
      _editables = er.items || [];
      renderEditables();
    } catch (_) { /* editables tab 可能还没渲染, 忽略 */ }
  } catch (e) {
    console.warn("[warm] 后台预热失败: " + e.message);
  } finally {
    _warmInflight = false;
  }
}
$("btnClose").onclick = async () => { await post("/api/project/close"); await refreshState(); };
$("btnSave").onclick    = async () => { try { const r = await post("/api/project/save");    alert(r.message || "已保存"); } catch(e) { alert(e.message); } };
$("btnCompile").onclick = async () => {
  try {
    const r = await post("/api/project/compile");
    alert((r.ok ? "✅ " : "❌ ") + r.summary + (r.raw ? ("\n\n" + r.raw) : ""));
  } catch (e) { alert(e.message); }
};
$("btnDownload").onclick = async () => {
  const strategy = $("dlStrategy").value;
  try {
    const r = await post("/api/project/download", { strategy });
    alert("下载报告:\n" + JSON.stringify(r.report, null, 2));
  } catch (e) { alert(e.message); }
};

// 拖入 .project → 自动填路径
$("projectPath").addEventListener("dragover", e => e.preventDefault());
$("projectPath").addEventListener("drop", e => {
  e.preventDefault();
  const f = e.dataTransfer.files?.[0];
  if (f && f.path) $("projectPath").value = f.path;
  else if (f) $("projectPath").value = f.name;
});

// ---------------- Tab: Extract ----------------
$("btnDiscover").onclick = async () => {
  try {
    const r = await get("/api/project/gvls");
    const list = $("gvlList");
    if (!r.gvls || !r.gvls.length) {
      list.classList.add("empty-box");
      list.innerHTML = "<i>未发现 GVL；可手动填写对象路径，或在“编辑程序块”中查看工程结构。</i>";
      return;
    }
    list.classList.remove("empty-box");
    list.innerHTML = renderGvlTree(r.gvls);
    bindGvlSelectionControls();
  } catch (e) { alert(e.message); }
};

function renderGvlTree(paths) {
  const groups = new Map();
  for (const fullPath of paths) {
    const parts = String(fullPath).split("/").filter(Boolean);
    const name = parts.pop() || String(fullPath);
    const parent = parts.join("/") || "工程根目录";
    if (!groups.has(parent)) groups.set(parent, []);
    groups.get(parent).push({ fullPath: String(fullPath), name });
  }

  return [...groups.entries()].map(([parent, items]) => `
    <section class="gvl-tree-group">
      <div class="gvl-folder-row" title="${escapeHtml(parent)}">
        <span class="gvl-folder-icon" aria-hidden="true"></span>
        <span class="gvl-folder-path">${escapeHtml(parent)}</span>
        <span class="gvl-count">${items.length}</span>
      </div>
      <div class="gvl-tree-children">
        ${items.map(({ fullPath, name }) => `
          <label class="gvl-tree-item" title="${escapeHtml(fullPath)}">
            <input type="checkbox" class="gvlChk" value="${escapeHtml(fullPath)}" checked/>
            <span class="gvl-leaf-icon" aria-hidden="true"></span>
            <span class="gvl-item-text">
              <strong>${escapeHtml(name)}</strong>
              <small>${escapeHtml(fullPath)}</small>
            </span>
          </label>
        `).join("")}
      </div>
    </section>
  `).join("");
}

function syncGvlSelectAll() {
  const boxes = els(".gvlChk");
  const selectAll = $("chkSelectAllGvls");
  const checked = boxes.filter(box => box.checked).length;
  selectAll.disabled = boxes.length === 0;
  selectAll.checked = boxes.length > 0 && checked === boxes.length;
  selectAll.indeterminate = checked > 0 && checked < boxes.length;
}

function bindGvlSelectionControls() {
  els(".gvlChk").forEach(box => { box.onchange = syncGvlSelectAll; });
  syncGvlSelectAll();
}

$("chkSelectAllGvls").onchange = (event) => {
  els(".gvlChk").forEach(box => { box.checked = event.target.checked; });
  syncGvlSelectAll();
};

function collectGvls() {
  const chosen = els(".gvlChk").filter(c => c.checked).map(c => c.value);
  const manual = $("gvlManual").value.split(/\r?\n/).map(x => x.trim()).filter(Boolean);
  const set = new Set([...chosen, ...manual]);
  return Array.from(set);
}

function buildExtractReq(previewOnly) {
  const gvls = collectGvls();
  return {
    gvls: gvls.length ? gvls : null,
    // This controls variable filtering only. GVL selection is represented by
    // `gvls` above and must never be coupled to the select-all checkbox.
    include_all: $("chkIncludeUnmarked").checked,
    expand_structs: $("chkExpandStructs").checked,
    ns_index: parseInt($("numNs").value, 10) || 4,
    ns_prefix: $("txtNsPrefix").value || "uniab|",
    node_language: "Chinese",
    out_path: $("txtCsvOut").value.trim() || null,
    preview_only: !!previewOnly,
  };
}

async function runExtract(previewOnly) {
  const req = buildExtractReq(previewOnly);
  if (els(".gvlChk").length && !req.gvls) {
    showResult($("extractResult"), false, "请至少选择一个 GVL，或填写手动对象路径");
    return;
  }
  try {
    const r = await post("/api/project/extract", req);
    showResult($("extractResult"), r.ok,
      `${previewOnly ? "预览" : "已写"} ${r.count} 行` +
      (r.out_path ? `\n→ ${r.out_path}` : "") +
      (r.truncated ? "\n（表格仅显示前 500 行）" : ""));
    renderTable(r.rows || []);
    // 如果不是 preview，把 CSV 路径自动带到仿真页
    if (!previewOnly && r.out_path) $("simCsv").value = r.out_path;
  } catch (e) { showResult($("extractResult"), false, e.message); }
}
$("btnPreview").onclick = () => runExtract(true);
$("btnExtract").onclick = () => runExtract(false);

function renderTable(rows) {
  const t = $("previewTable");
  if (!rows.length) { t.innerHTML = ""; return; }
  const headers = Object.keys(rows[0]);
  t.innerHTML =
    "<thead><tr>" + headers.map(h => `<th>${h}</th>`).join("") + "</tr></thead>" +
    "<tbody>" + rows.map(r =>
      "<tr>" + headers.map(h => `<td>${escapeHtml(r[h] ?? "")}</td>`).join("") + "</tr>"
    ).join("") + "</tbody>";
}
function escapeHtml(s) { return String(s).replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c])); }

// ---------------- Tab: Edit POU ----------------
$("btnStructure").onclick = async () => {
  try {
    const r = await get("/api/project/structure");
    $("structureBox").textContent = r.text || "(空)";
  } catch (e) { alert(e.message); }
};

// 项目里发现的所有 POU/GVL/DUT 列表
let _editables = [];      // [{name, path, kind, has_impl, lang}]
let _selectedPath = null;
const KIND_ORDER = ["POU", "GVL", "DUT", "OTHER"];

function renderEditables() {
  const box = $("editablesList");
  if (!_editables.length) {
    box.classList.add("empty-box");
    box.innerHTML = "<div class='empty'>没找到可编辑对象</div>";
    return;
  }
  const kw = ($("editablesFilter").value || "").toLowerCase().trim();
  const kindOn = {
    POU: $("chkKindPOU").checked,
    GVL: $("chkKindGVL").checked,
    DUT: $("chkKindDUT").checked,
    OTHER: false,        // OTHER 默认不显示 (folders/tasks 等)
  };

  const grouped = {};
  for (const it of _editables) {
    if (!kindOn[it.kind]) continue;
    if (kw && !it.name.toLowerCase().includes(kw) && !it.path.toLowerCase().includes(kw)) continue;
    (grouped[it.kind] ||= []).push(it);
  }

  const html = [];
  for (const k of KIND_ORDER) {
    const items = grouped[k];
    if (!items || !items.length) continue;
    const folders = new Map();
    for (const it of items) {
      const parts = String(it.path).split("/").filter(Boolean);
      parts.pop();
      const parent = parts.join("/") || "工程根目录";
      if (!folders.has(parent)) folders.set(parent, []);
      folders.get(parent).push(it);
    }

    html.push(`<section class="object-kind-section">`);
    html.push(`<div class="object-group"><span>${k}</span><b>${items.length}</b></div>`);
    for (const [parent, folderItems] of folders) {
      const relParent = parent.startsWith("Application/")
        ? parent.slice("Application/".length)
        : parent;
      html.push(
        `<div class="object-folder-row" title="${escapeHtml(parent)}">` +
          `<span class="gvl-folder-icon" aria-hidden="true"></span>` +
          `<span class="object-folder-path">${escapeHtml(relParent)}</span>` +
          `<span class="gvl-count">${folderItems.length}</span>` +
        `</div>` +
        `<div class="object-tree-children">`
      );
      for (const it of folderItems) {
        const cls = (it.path === _selectedPath) ? "object-item active" : "object-item";
        html.push(
          `<div class="${cls}" data-path="${escapeHtml(it.path)}" data-kind="${it.kind}" title="${escapeHtml(it.path)}">` +
            `<span class="gvl-leaf-icon" aria-hidden="true"></span>` +
            `<span class="object-item-text">` +
              `<strong>${escapeHtml(it.name)}</strong>` +
              `<small>${escapeHtml(it.path)}</small>` +
            `</span>` +
            `<span class="kind-badge">${it.kind}</span>` +
          `</div>`
        );
      }
      html.push(`</div>`);
    }
    html.push(`</section>`);
  }
  if (!html.length) {
    box.classList.add("empty-box");
    box.innerHTML = "<div class='empty'>没有符合筛选条件的对象</div>";
    return;
  }
  box.classList.remove("empty-box");
  box.innerHTML = html.join("");
  els("#editablesList .object-item").forEach(node => {
    node.onclick = () => {
      _selectedPath = node.dataset.path;
      $("pouPath").value = _selectedPath;
      $("pouKindBadge").className = "kind-badge";
      $("pouKindBadge").textContent = node.dataset.kind;
      renderEditables();     // 高亮
      readPouByPath(_selectedPath);
    };
  });
}

async function loadEditables(force) {
  const btn = force ? $("btnRefreshEditables") : $("btnDiscoverEditables");
  const box = $("editablesList");
  box.classList.add("empty-box");
  box.innerHTML = "<div class='empty'>扫描中… (首次约 20s)</div>";
  btn.disabled = true;
  try {
    const url = "/api/project/editables" + (force ? "?refresh=true" : "");
    const r = await get(url);
    _editables = r.items || [];
    renderEditables();
  } catch (e) {
    box.classList.add("empty-box");
    box.innerHTML = `<div class='empty'>失败: ${escapeHtml(e.message)}</div>`;
  } finally {
    btn.disabled = false;
  }
}
$("btnDiscoverEditables").onclick = () => loadEditables(false);
$("btnRefreshEditables").onclick  = () => loadEditables(true);
$("editablesFilter").oninput = renderEditables;
$("chkKindPOU").onchange = renderEditables;
$("chkKindGVL").onchange = renderEditables;
$("chkKindDUT").onchange = renderEditables;

async function readPouByPath(p) {
  try {
    const r = await get("/api/pou?path=" + encodeURIComponent(p));
    $("pouDecl").value = r.declaration || "";
    $("pouImpl").value = r.implementation || "";
  } catch (e) { alert(e.message); }
}
$("btnGetPou").onclick = async () => {
  const p = $("pouPath").value.trim();
  if (!p) return alert("请填 POU 路径");
  _selectedPath = p;
  renderEditables();
  await readPouByPath(p);
};
$("btnSetPou").onclick = async () => {
  const path = $("pouPath").value.trim();
  if (!path) return alert("请填 POU 路径");
  const body = {
    path,
    declaration: $("pouDecl").value,
    implementation: $("pouImpl").value,
    save: $("chkSetSave").checked,
    compile: $("chkSetCompile").checked,
  };
  try {
    const r = await post("/api/pou", body);
    showResult($("setPouResult"), r.ok, JSON.stringify(r, null, 2));
  } catch (e) { showResult($("setPouResult"), false, e.message); }
};

// ---------------- Tab: Sim ----------------
// 远程部署时浏览器所在机器和服务器不是同一台, 填不出服务器路径 —— 上传后回填
$("simCsvFile").onchange = async (e) => {
  const input = e.target;
  const file = input.files?.[0];
  if (!file) return;
  input.disabled = true;
  try {
    const dataUrl = await new Promise((resolve, reject) => {
      const fr = new FileReader();
      fr.onload = () => resolve(String(fr.result));
      fr.onerror = () => reject(new Error("读取本地文件失败"));
      fr.readAsDataURL(file);      // 保留原始字节, 让后端去嗅探编码
    });
    const r = await post("/api/csv/upload", {
      filename: file.name,
      content_b64: dataUrl.slice(dataUrl.indexOf(",") + 1),
    }, 60000);
    $("simCsv").value = r.path;
    alert(`上传成功，识别到 ${r.count} 个变量节点：\n${r.path}`);
  } catch (err) {
    alert("上传失败: " + err.message);
  } finally {
    input.disabled = false;
    input.value = "";
  }
};

$("btnServerStart").onclick = async () => {
  try {
    const r = await post("/api/server/start", {
      csv: $("simCsv").value.trim() || null,
      host: $("simHost").value.trim() || "0.0.0.0",
      port: parseInt($("simPort").value, 10) || 4855,
      ns_index: parseInt($("simNs").value, 10) || 4,
      ns_uri: $("simNsUri").value.trim() || "urn:xuse:sim",
      occupancy_true: $("simOcc").checked,
    }, 10000);
    console.log("server started pid=", r.pid);
    await refreshState();
    await loadServerVariables({ reset: true });
  } catch (e) { alert(e.message); }
};
async function stopManagedProcess(button, url) {
  const originalText = button.textContent;
  button.disabled = true;
  button.textContent = "停止中…";
  try {
    const result = await post(url, {}, 7000);
    if (!result.ok) throw new Error(result.message || "停止失败");
    await refreshState();
  } catch (e) {
    alert(e.message);
    await refreshState();
  } finally {
    button.textContent = originalText;
  }
}

$("btnServerStop").onclick = () => stopManagedProcess(
  $("btnServerStop"), "/api/server/stop"
);

const SZLAB_S04_WORKFLOWS = new Set([
  "all",
  "szlab_magnetic_stirring_workflow",
  "szlab_robot_action_workflow",
  "s04_robot_stirring_workflow",
  "s_z_lab_单样品全流程_物料感知",
  "s_z_lab_单样品原子流程_无_s07_扫码",
  "s_z_lab_双任务单样品原子流程_无_s07_扫码",
]);
const SZLAB_PUMP_WORKFLOWS = new Set([
  "all",
  "s06_robot_workflow",
  "szlab_stack_s05_s06_workflow",
  "szlab_mixer_workflow",
  "szlab_mixer_pump_production",
  "szlab_material_s06_workflow",
  "s_z_lab_单样品全流程_物料感知",
  "s_z_lab_单样品原子流程_无_s07_扫码",
  "s_z_lab_双任务单样品原子流程_无_s07_扫码",
]);
const SZLAB_S07_WORKFLOWS = new Set([
  "all",
  "szlab_s07_solid_addition_workflow",
  "s07_粉桶与烧杯搬运后固体称量",
  "s_z_lab_单样品全流程_物料感知",
  "s_z_lab_单样品原子流程_无_s07_扫码",
  "s_z_lab_双任务单样品原子流程_无_s07_扫码",
]);
const SZLAB_S09_WORKFLOWS = new Set([
  "all",
  "s09_移液调试",
  "s_z_lab_单样品全流程_物料感知",
  "s_z_lab_单样品原子流程_无_s07_扫码",
  "s_z_lab_双任务单样品原子流程_无_s07_扫码",
]);

function syncSzlabAgentOptions() {
  const workflow = $("agentWorkflow").value;
  $("agentPositionField").classList.toggle(
    "hidden", !SZLAB_S04_WORKFLOWS.has(workflow)
  );
  $("agentPumpField").classList.toggle(
    "hidden", !SZLAB_PUMP_WORKFLOWS.has(workflow)
  );
  $("agentS09VolumeField").classList.toggle(
    "hidden", !SZLAB_S09_WORKFLOWS.has(workflow)
  );
  $("agentS07BalanceField").classList.toggle(
    "hidden", !SZLAB_S07_WORKFLOWS.has(workflow)
  );
  $("agentS09BalanceField").classList.toggle(
    "hidden", !SZLAB_S09_WORKFLOWS.has(workflow)
  );
  $("agentDelayField").classList.toggle(
    "hidden", workflow === "szlab_photoshotting_workflow"
  );
}

function setAgentFormDisabled(disabled) {
  for (const id of [
    "agentHost", "agentPort", "agentCfg",
    "agentWorkflow", "agentPosition", "agentPump", "agentDelayMs",
    "agentPollMs", "agentS09Volume", "agentS07Balance", "agentS09Balance",
  ]) {
    $(id).disabled = disabled;
  }
}

function readAgentNumber(id, label, min, max, integer = false) {
  const raw = $(id).value.trim();
  const value = Number(raw);
  if (!raw || !Number.isFinite(value) || value < min || value > max) {
    throw new Error(`${label}必须在 ${min} 到 ${max} 之间`);
  }
  if (integer && !Number.isInteger(value)) {
    throw new Error(`${label}必须是整数`);
  }
  return value;
}

$("btnAgentStart").onclick = async () => {
  try {
    const workflow = $("agentWorkflow").value;
    const body = {
      profile: "szlab",
      host: $("agentHost").value.trim() || "127.0.0.1",
      port: parseInt($("agentPort").value, 10) || 4855,
      config: $("agentCfg").value.trim() || null,
      workflow,
      poll_ms: readAgentNumber("agentPollMs", "轮询间隔", 5, 60000, true),
    };
    if (
      workflow !== "szlab_photoshotting_workflow" &&
      $("agentDelayMs").value.trim()
    ) {
      body.delay_ms = readAgentNumber("agentDelayMs", "动作延时", 0, 3600000, true);
    }
    if (SZLAB_S04_WORKFLOWS.has(workflow)) {
      body.position = readAgentNumber("agentPosition", "S04 位置", 1, 6, true);
    }
    if (SZLAB_PUMP_WORKFLOWS.has(workflow)) {
      body.pump = readAgentNumber("agentPump", "储液泵", 1, 3, true);
    }
    if (SZLAB_S09_WORKFLOWS.has(workflow)) {
      body.s09_remaining_volume_ml = readAgentNumber(
        "agentS09Volume", "S09 初始余量", 0.1, Number.MAX_SAFE_INTEGER
      );
      body.s09_balance_reading = readAgentNumber(
        "agentS09Balance", "S09 模拟天平读数",
        -Number.MAX_SAFE_INTEGER, Number.MAX_SAFE_INTEGER
      );
    }
    if (SZLAB_S07_WORKFLOWS.has(workflow)) {
      body.s07_balance_reading = readAgentNumber(
        "agentS07Balance", "S07 模拟天平读数",
        -Number.MAX_SAFE_INTEGER, Number.MAX_SAFE_INTEGER
      );
    }
    const r = await post("/api/agent/start", body, 10000);
    console.log("agent started pid=", r.pid, "options=", r.options || {});
    await refreshState();
  } catch (e) { alert(e.message); }
};
$("agentWorkflow").onchange = syncSzlabAgentOptions;
syncSzlabAgentOptions();
$("btnAgentStop").onclick = () => stopManagedProcess(
  $("btnAgentStop"), "/api/agent/stop"
);

// ---------------- 在线变量 ----------------
const variablePage = {
  offset: 0,
  limit: 100,
  total: 0,
  query: "",
  items: [],
  loading: false,
};
const MONITOR_STORAGE_KEY = "opcuasim.monitored-variables.v2";
const LEGACY_MONITOR_STORAGE_KEY = "opcuasim.monitored-variables.v1";
const selectedVariables = new Map();
const monitorStore = loadMonitorStore();
let activeMonitorCsvId = monitorStore.last_csv_id || null;
let monitoredVariables = hydrateStoredMonitors(
  activeMonitorCsvId && monitorStore.profiles[activeMonitorCsvId]
    ? monitorStore.profiles[activeMonitorCsvId].items
    : monitorStore.legacy_items
);
const monitorState = { loading: false };
let variableSearchTimer = null;
let monitorRefreshTimer = null;

function variableMessage(text, kind = "neutral") {
  const node = $("serverVarMessage");
  node.className = `result-box ${kind}`;
  node.textContent = text;
}

function monitorMessage(text, kind = "neutral") {
  const node = $("monitorVarMessage");
  node.className = `result-box ${kind}`;
  node.textContent = text;
}

function sanitizeStoredMonitors(items) {
  if (!Array.isArray(items)) return [];
  return items
    .filter(item => item && item.node_id && item.name && item.data_type)
    .slice(0, 200)
    .map(item => ({
      name: String(item.name),
      english_name: String(item.english_name || ""),
      data_type: String(item.data_type),
      node_id: String(item.node_id),
    }));
}

function hydrateStoredMonitors(items) {
  return sanitizeStoredMonitors(items).map(item => ({
    ...item,
    value: null,
    draft: null,
    missing: false,
    offline: true,
  }));
}

function loadMonitorStore() {
  const empty = {
    version: 2,
    last_csv_id: null,
    profiles: {},
    legacy_items: [],
  };
  try {
    const raw = JSON.parse(localStorage.getItem(MONITOR_STORAGE_KEY) || "null");
    if (raw && raw.version === 2 && raw.profiles && typeof raw.profiles === "object") {
      for (const [csvId, profile] of Object.entries(raw.profiles)) {
        if (!/^[a-f0-9]{64}$/.test(csvId) || !profile) continue;
        empty.profiles[csvId] = {
          updated_at: Number(profile.updated_at || 0),
          items: sanitizeStoredMonitors(profile.items),
        };
      }
      if (raw.last_csv_id && empty.profiles[raw.last_csv_id]) {
        empty.last_csv_id = raw.last_csv_id;
      }
      return empty;
    }
  } catch (_) {
    // v2 损坏时继续尝试迁移旧版监控列表。
  }
  try {
    empty.legacy_items = sanitizeStoredMonitors(
      JSON.parse(localStorage.getItem(LEGACY_MONITOR_STORAGE_KEY) || "[]")
    );
  } catch (_) {
    empty.legacy_items = [];
  }
  return empty;
}

function writeMonitorStore() {
  try {
    const profileIds = Object.keys(monitorStore.profiles);
    if (profileIds.length > 25) {
      profileIds
        .filter(csvId => csvId !== activeMonitorCsvId)
        .sort((a, b) =>
          Number(monitorStore.profiles[a].updated_at || 0) -
          Number(monitorStore.profiles[b].updated_at || 0)
        )
        .slice(0, profileIds.length - 25)
        .forEach(csvId => { delete monitorStore.profiles[csvId]; });
    }
    localStorage.setItem(MONITOR_STORAGE_KEY, JSON.stringify({
      version: 2,
      last_csv_id: monitorStore.last_csv_id,
      profiles: monitorStore.profiles,
    }));
    localStorage.removeItem(LEGACY_MONITOR_STORAGE_KEY);
  } catch (e) {
    console.warn("无法保存监控栏:", e);
  }
}

function persistMonitors() {
  const items = sanitizeStoredMonitors(monitoredVariables);
  if (!activeMonitorCsvId) {
    monitorStore.legacy_items = items;
    try {
      localStorage.setItem(LEGACY_MONITOR_STORAGE_KEY, JSON.stringify(items));
    } catch (e) {
      console.warn("无法保存待归属的监控栏:", e);
    }
    return;
  }
  monitorStore.profiles[activeMonitorCsvId] = {
    updated_at: Date.now(),
    items,
  };
  monitorStore.last_csv_id = activeMonitorCsvId;
  monitorStore.legacy_items = [];
  writeMonitorStore();
}

function updateMonitorCsvBadge(running = !!currentAppState?.server?.running) {
  const badge = $("monitorCsvBadge");
  if (!activeMonitorCsvId) {
    badge.textContent = "等待变量表";
    badge.title = "Server 启动并识别变量表后，监控列表将按 CSV 保存";
    return;
  }
  const shortId = activeMonitorCsvId.slice(0, 10);
  badge.textContent = `${running ? "当前" : "已保存"} · ${shortId}`;
  badge.title = `变量表指纹：${activeMonitorCsvId}`;
}

function syncMonitorProfile(csvId, running) {
  if (!running || !csvId) {
    updateMonitorCsvBadge(false);
    return;
  }
  if (csvId === activeMonitorCsvId) {
    updateMonitorCsvBadge(true);
    return;
  }

  const legacyItems = activeMonitorCsvId
    ? []
    : sanitizeStoredMonitors(monitoredVariables.length
      ? monitoredVariables
      : monitorStore.legacy_items);
  if (activeMonitorCsvId) persistMonitors();
  activeMonitorCsvId = csvId;
  const profile = monitorStore.profiles[csvId];
  const restoredItems = profile?.items || legacyItems;
  monitoredVariables = hydrateStoredMonitors(restoredItems);
  selectedVariables.clear();
  monitorStore.profiles[csvId] = {
    updated_at: Date.now(),
    items: sanitizeStoredMonitors(monitoredVariables),
  };
  monitorStore.last_csv_id = csvId;
  monitorStore.legacy_items = [];
  writeMonitorStore();
  renderMonitoredVariables();
  renderServerVariables();
  scheduleMonitorRefresh();
  updateMonitorCsvBadge(true);
  monitorMessage(
    monitoredVariables.length
      ? `已切换到当前变量表，并恢复 ${monitoredVariables.length} 个监控变量。`
      : "已切换到当前变量表；这是首次使用，尚未保存监控变量。",
    monitoredVariables.length ? "success" : "neutral"
  );
}

function isMonitored(nodeId) {
  return monitoredVariables.some(item => item.node_id === nodeId);
}

function formatVariableValue(value) {
  if (value === null || value === undefined) return "--";
  if (typeof value === "object") {
    try { return JSON.stringify(value); }
    catch (_) { return String(value); }
  }
  return String(value);
}

function updateVariableControls() {
  const running = !!currentAppState?.server?.running;
  const selectable = variablePage.items.filter(item => !isMonitored(item.node_id));
  const selectedOnPage = selectable.filter(item => selectedVariables.has(item.node_id));
  const selectPage = $("selectPageVars");
  selectPage.disabled = variablePage.loading || !running || selectable.length === 0;
  selectPage.checked = selectable.length > 0 && selectedOnPage.length === selectable.length;
  selectPage.indeterminate =
    selectedOnPage.length > 0 && selectedOnPage.length < selectable.length;

  $("selectedVarCount").textContent = `已选 ${selectedVariables.size} 个`;
  $("btnAddSelectedVars").disabled =
    variablePage.loading || !running || selectedVariables.size === 0;
  $("btnRefreshVars").disabled = variablePage.loading || !running;
  $("btnVarsPrev").disabled =
    variablePage.loading || !running || variablePage.offset <= 0;
  $("btnVarsNext").disabled =
    variablePage.loading || !running ||
    variablePage.offset + variablePage.limit >= variablePage.total;

  $("btnRefreshMonitor").disabled =
    monitorState.loading || !running || monitoredVariables.length === 0;
  $("btnClearMonitor").disabled = monitoredVariables.length === 0;
  $("monitorRefreshInterval").disabled = !$("monitorAutoRefresh").checked;
}

function clearServerVariables(message) {
  variablePage.offset = 0;
  variablePage.total = 0;
  variablePage.items = [];
  selectedVariables.clear();
  $("serverVarCount").textContent = "0 个变量";
  $("serverVarsTable").querySelector("tbody").innerHTML =
    `<tr><td colspan="6" class="empty-cell">${escapeHtml(message || "暂无在线变量")}</td></tr>`;
  $("serverVarPageInfo").textContent = "第 0 / 0 页";
  variableMessage(message || "暂无在线变量");
  updateVariableControls();
}

function renderServerVariables() {
  const tbody = $("serverVarsTable").querySelector("tbody");
  const totalPages = variablePage.total ? Math.ceil(variablePage.total / variablePage.limit) : 0;
  const currentPage = totalPages ? Math.floor(variablePage.offset / variablePage.limit) + 1 : 0;
  $("serverVarCount").textContent = `${variablePage.total} 个变量`;
  $("serverVarPageInfo").textContent = `第 ${currentPage} / ${totalPages} 页`;

  if (!variablePage.items.length) {
    const text = variablePage.query ? "没有匹配的变量" : "当前 CSV 中没有可显示的变量";
    tbody.innerHTML = `<tr><td colspan="6" class="empty-cell">${text}</td></tr>`;
    updateVariableControls();
    return;
  }

  tbody.innerHTML = variablePage.items.map((item, index) => {
    const monitored = isMonitored(item.node_id);
    const selected = selectedVariables.has(item.node_id);
    return `<tr data-index="${index}">` +
      `<td class="select-cell"><input class="var-select" type="checkbox" ` +
      `aria-label="选择 ${escapeHtml(item.name)}" ${selected ? "checked" : ""} ` +
      `${monitored ? "disabled" : ""}></td>` +
      `<td class="variable-name"><strong>${escapeHtml(item.name)}</strong>` +
      `<small>${escapeHtml(item.english_name || "")}</small></td>` +
      `<td><span class="type-pill">${escapeHtml(item.data_type)}</span></td>` +
      `<td><span class="read-value">${escapeHtml(formatVariableValue(item.value))}</span></td>` +
      `<td class="node-id">${escapeHtml(item.node_id)}</td>` +
      `<td>${monitored ? '<span class="monitor-status active">已监控</span>' :
        '<span class="monitor-status">未监控</span>'}</td>` +
      `</tr>`;
  }).join("");
  updateVariableControls();
}

async function loadServerVariables({ reset = false } = {}) {
  if (variablePage.loading) return;
  if (!currentAppState?.server?.running) {
    clearServerVariables("启动 OPC UA Server 后即可选择在线变量。");
    return;
  }
  if (reset) variablePage.offset = 0;
  variablePage.limit = parseInt($("serverVarPageSize").value, 10) || 100;
  variablePage.query = $("serverVarSearch").value.trim();
  variablePage.loading = true;
  $("btnRefreshVars").disabled = true;
  variableMessage("正在读取服务器变量…");
  try {
    const params = new URLSearchParams({
      query: variablePage.query,
      offset: String(variablePage.offset),
      limit: String(variablePage.limit),
    });
    const r = await get("/api/server/variables?" + params.toString());
    variablePage.total = r.total || 0;
    variablePage.offset = r.offset || 0;
    variablePage.items = r.items || [];
    renderServerVariables();
    variableMessage(
      variablePage.total
        ? `已从 ${currentAppState.server.endpoint || "当前服务器"} 读取 ${variablePage.items.length} 个变量。`
        : (variablePage.query ? "没有匹配的变量。" : "当前服务器没有变量。"),
      variablePage.total ? "success" : "neutral"
    );
  } catch (e) {
    variableMessage(e.message, "error");
    variablePage.items = [];
    renderServerVariables();
  } finally {
    variablePage.loading = false;
    renderServerVariables();
  }
}

$("btnRefreshVars").onclick = () => loadServerVariables();
$("serverVarSearch").oninput = () => {
  clearTimeout(variableSearchTimer);
  variableSearchTimer = setTimeout(() => loadServerVariables({ reset: true }), 300);
};
$("serverVarPageSize").onchange = () => loadServerVariables({ reset: true });
$("btnVarsPrev").onclick = () => {
  variablePage.offset = Math.max(0, variablePage.offset - variablePage.limit);
  loadServerVariables();
};
$("btnVarsNext").onclick = () => {
  if (variablePage.offset + variablePage.limit < variablePage.total) {
    variablePage.offset += variablePage.limit;
    loadServerVariables();
  }
};

$("serverVarsTable").addEventListener("change", event => {
  const checkbox = event.target.closest(".var-select");
  if (!checkbox) return;
  const row = checkbox.closest("tr");
  const item = variablePage.items[Number(row.dataset.index)];
  if (!item) return;
  if (checkbox.checked) selectedVariables.set(item.node_id, item);
  else selectedVariables.delete(item.node_id);
  updateVariableControls();
});

$("selectPageVars").onchange = event => {
  variablePage.items.forEach(item => {
    if (isMonitored(item.node_id)) return;
    if (event.target.checked) selectedVariables.set(item.node_id, item);
    else selectedVariables.delete(item.node_id);
  });
  renderServerVariables();
};

$("btnAddSelectedVars").onclick = () => {
  let added = 0;
  for (const item of selectedVariables.values()) {
    if (isMonitored(item.node_id) || monitoredVariables.length >= 200) continue;
    monitoredVariables.push({
      ...item,
      draft: null,
      missing: false,
      offline: false,
    });
    added += 1;
  }
  selectedVariables.clear();
  persistMonitors();
  renderMonitoredVariables();
  renderServerVariables();
  scheduleMonitorRefresh();
  monitorMessage(
    added
      ? `已添加 ${added} 个变量。监控栏将按设置的周期读取当前值。`
      : "没有可添加的变量，或监控栏已达到 200 个上限。",
    added ? "success" : "neutral"
  );
  if (added) {
    document.querySelector(".monitor-card").scrollIntoView({ behavior: "smooth", block: "start" });
  }
};

function monitorEditor(item) {
  const draft = item.draft === null ? item.value : item.draft;
  if (item.data_type === "BOOLEAN") {
    const current = draft === true || String(draft).toLowerCase() === "true";
    return `<select class="value-editor monitor-editor" aria-label="${escapeHtml(item.name)} 新值">` +
      `<option value="true"${current ? " selected" : ""}>true</option>` +
      `<option value="false"${!current ? " selected" : ""}>false</option></select>`;
  }
  const isNumeric = ["INT16", "INT32", "FLOAT"].includes(item.data_type);
  const step = item.data_type === "FLOAT" ? "any" : "1";
  const value = draft === null || draft === undefined ? "" : draft;
  return `<input class="value-editor monitor-editor" ` +
    `${isNumeric ? `type="number" step="${step}"` : 'type="text"'} ` +
    `value="${escapeHtml(value)}" aria-label="${escapeHtml(item.name)} 新值">`;
}

function renderMonitoredVariables() {
  const tbody = $("monitorVarsTable").querySelector("tbody");
  $("monitorVarCount").textContent = `${monitoredVariables.length} 个监控`;
  if (!monitoredVariables.length) {
    tbody.innerHTML =
      '<tr><td colspan="6" class="empty-cell">尚未添加监控变量</td></tr>';
    updateVariableControls();
    return;
  }

  tbody.innerHTML = monitoredVariables.map((item, index) =>
    `<tr data-index="${index}">` +
    `<td class="variable-name"><strong>${escapeHtml(item.name)}</strong>` +
    `<small>${escapeHtml(item.english_name || "")}</small></td>` +
    `<td><span class="type-pill">${escapeHtml(item.data_type)}</span></td>` +
    `<td><span class="monitor-current">--</span></td>` +
    `<td>${monitorEditor(item)}</td>` +
    `<td class="node-id">${escapeHtml(item.node_id)}</td>` +
    `<td class="monitor-actions">` +
    `<button class="btn small monitor-write">写入</button>` +
    `<button class="text-button monitor-remove">移除</button>` +
    `</td></tr>`
  ).join("");
  updateMonitorRows();
  if (currentAppState === null) {
    monitorMessage(`已恢复 ${monitoredVariables.length} 个监控变量，服务连接后将自动读取。`);
  }
}

function updateMonitorRows() {
  const running = !!currentAppState?.server?.running;
  els("tbody tr[data-index]", $("monitorVarsTable")).forEach(row => {
    const item = monitoredVariables[Number(row.dataset.index)];
    if (!item) return;
    const current = el(".monitor-current", row);
    const editor = el(".monitor-editor", row);
    const writeButton = el(".monitor-write", row);

    current.className = "monitor-current";
    if (item.offline) {
      current.textContent = "服务未运行";
      current.classList.add("offline");
    } else if (item.missing) {
      current.textContent = "节点不存在";
      current.classList.add("missing");
    } else {
      current.textContent = formatVariableValue(item.value);
    }
    editor.disabled = !running || item.missing || item.offline;
    writeButton.disabled = monitorState.loading || !running || item.missing || item.offline;
    if (item.draft === null && document.activeElement !== editor) {
      editor.value = formatVariableValue(item.value) === "--"
        ? (item.data_type === "BOOLEAN" ? "false" : "")
        : formatVariableValue(item.value);
    }
  });
  updateVariableControls();
}

function markMonitorsOffline() {
  monitoredVariables.forEach(item => { item.offline = true; });
  updateMonitorRows();
  if (monitoredVariables.length) {
    monitorMessage("服务未运行，监控变量已保留，服务恢复后会继续读取。");
  }
}

async function refreshMonitoredVariables({ announce = true } = {}) {
  if (monitorState.loading || !monitoredVariables.length) return;
  if (!currentAppState?.server?.running) {
    markMonitorsOffline();
    return;
  }
  if (document.hidden && !announce) return;

  monitorState.loading = true;
  updateMonitorRows();
  if (announce) monitorMessage("正在读取监控变量…");
  try {
    const response = await post("/api/server/variables/read", {
      node_ids: monitoredVariables.map(item => item.node_id),
    }, 7000);
    const values = new Map(response.items.map(item => [item.node_id, item]));
    const missing = new Set(response.missing || []);
    monitoredVariables.forEach(item => {
      const fresh = values.get(item.node_id);
      item.offline = false;
      item.missing = missing.has(item.node_id);
      if (fresh) {
        item.name = fresh.name;
        item.english_name = fresh.english_name || "";
        item.data_type = fresh.data_type;
        item.value = fresh.value;
      }
    });
    persistMonitors();

    variablePage.items.forEach(item => {
      const fresh = values.get(item.node_id);
      if (fresh) item.value = fresh.value;
    });
    updateMonitorRows();
    renderServerVariables();
    const missingCount = missing.size;
    if (announce || missingCount) {
      monitorMessage(
        `已读取 ${response.items.length} 个监控变量` +
        `${missingCount ? `，${missingCount} 个节点不在当前变量表中` : ""}。`,
        missingCount ? "neutral" : "success"
      );
    }
  } catch (e) {
    monitorMessage(`读取监控变量失败：${e.message}`, "error");
  } finally {
    monitorState.loading = false;
    updateMonitorRows();
  }
}

function scheduleMonitorRefresh() {
  if (monitorRefreshTimer !== null) {
    clearInterval(monitorRefreshTimer);
    monitorRefreshTimer = null;
  }
  updateVariableControls();
  if (
    !$("monitorAutoRefresh").checked ||
    !currentAppState?.server?.running ||
    !monitoredVariables.length
  ) return;
  const interval = parseInt($("monitorRefreshInterval").value, 10) || 2000;
  monitorRefreshTimer = setInterval(
    () => refreshMonitoredVariables({ announce: false }),
    interval
  );
}

$("btnRefreshMonitor").onclick = () => refreshMonitoredVariables();
$("monitorAutoRefresh").onchange = scheduleMonitorRefresh;
$("monitorRefreshInterval").onchange = scheduleMonitorRefresh;
$("btnClearMonitor").onclick = () => {
  monitoredVariables = [];
  selectedVariables.clear();
  persistMonitors();
  renderMonitoredVariables();
  renderServerVariables();
  scheduleMonitorRefresh();
  monitorMessage("监控栏已清空，可从全部变量中重新添加。");
};

$("monitorVarsTable").addEventListener("input", event => {
  const editor = event.target.closest(".monitor-editor");
  if (!editor) return;
  const row = editor.closest("tr");
  const item = monitoredVariables[Number(row.dataset.index)];
  if (item) item.draft = editor.value;
});

$("monitorVarsTable").addEventListener("click", async event => {
  const row = event.target.closest("tr[data-index]");
  if (!row) return;
  const index = Number(row.dataset.index);
  const item = monitoredVariables[index];
  if (!item) return;

  if (event.target.closest(".monitor-remove")) {
    monitoredVariables.splice(index, 1);
    selectedVariables.delete(item.node_id);
    persistMonitors();
    renderMonitoredVariables();
    renderServerVariables();
    scheduleMonitorRefresh();
    monitorMessage(`${item.name} 已移出监控栏。`);
    return;
  }

  const button = event.target.closest(".monitor-write");
  if (!button) return;
  const editor = el(".monitor-editor", row);
  const oldText = button.textContent;
  button.disabled = true;
  button.textContent = "写入中";
  try {
    const response = await post("/api/server/variable", {
      node_id: item.node_id,
      value: editor.value,
    }, 7000);
    item.value = response.value;
    item.draft = null;
    item.offline = false;
    item.missing = false;
    variablePage.items.forEach(pageItem => {
      if (pageItem.node_id === item.node_id) pageItem.value = response.value;
    });
    updateMonitorRows();
    renderServerVariables();
    monitorMessage(
      `${item.name} 已写入，服务器回读值为 ${formatVariableValue(response.value)}。`,
      "success"
    );
  } catch (e) {
    monitorMessage(`${item.name} 写入失败：${e.message}`, "error");
  } finally {
    button.textContent = oldText;
    updateMonitorRows();
  }
});

renderMonitoredVariables();
updateMonitorCsvBadge(false);

// ---------------- 日志 SSE ----------------
const logBox = $("logBox");
$("btnClearLog").onclick = () => { logBox.innerHTML = ""; };

function levelValue(l) { return { DEBUG: 10, INFO: 20, WARNING: 30, ERROR: 40, CRITICAL: 50 }[l] || 0; }

function appendLog(entry) {
  const min = parseInt($("logLevel").value, 10);
  if (levelValue(entry.level) < min) return;
  const filter = $("logFilter").value.trim().toLowerCase();
  if (filter && !entry.msg.toLowerCase().includes(filter) && !entry.source.toLowerCase().includes(filter)) return;
  const t = new Date(entry.ts * 1000);
  const ts = t.toTimeString().slice(0, 8) + "." + String(t.getMilliseconds()).padStart(3, "0");
  const line = document.createElement("div");
  line.className = "log-line " + entry.level.toLowerCase();
  line.innerHTML =
    `<span class="time">${ts}</span>` +
    `<span class="level">${escapeHtml(entry.level)}</span>` +
    `<span><b>${escapeHtml(entry.source)}</b> · ${escapeHtml(entry.msg)}</span>`;
  logBox.appendChild(line);
  while (logBox.children.length > 2000) logBox.removeChild(logBox.firstChild);
  if ($("autoScroll").checked) logBox.scrollTop = logBox.scrollHeight;
}

function connectSse() {
  const es = new EventSource("/api/logs/stream");
  es.addEventListener("log",   ev => { try { appendLog(JSON.parse(ev.data)); } catch(_){} });
  es.addEventListener("state", ev => { try { renderState(JSON.parse(ev.data)); } catch(_){} });
  es.onopen  = () => {
    $("sseState").textContent = "已连接";
    $("sseState").classList.add("on");
  };
  es.onerror = () => {
    $("sseState").textContent = "已断开，正在重连";
    $("sseState").classList.remove("on");
    es.close();
    setTimeout(connectSse, 5000);
  };
}

connectSse();
refreshState();
setInterval(refreshState, 2000);

// 顶栏 build badge —— 拉后端 /api/version 显示后端启动时间, 帮助判断是不是老 backend
(async function updateBuildBadge() {
  const badge = $("buildBadge");
  if (!badge) return;
  try {
    const r = await fetch("/api/version");
    if (!r.ok) throw new Error("HTTP " + r.status);
    const v = await r.json();
    const started = new Date(v.backend_started * 1000);
    const hh = String(started.getHours()).padStart(2, "0");
    const mm = String(started.getMinutes()).padStart(2, "0");
    const ss = String(started.getSeconds()).padStart(2, "0");
    const jsMtime = v.static_mtime["app.js"] ? new Date(v.static_mtime["app.js"] * 1000) : null;
    const jsTag = jsMtime ? `${String(jsMtime.getHours()).padStart(2,"0")}:${String(jsMtime.getMinutes()).padStart(2,"0")}` : "?";
    // 部署版走 CI 写入的 VERSION, 本地开发回落到手写的 GUI_BUILD
    const release = v.release && v.release !== "dev" ? v.release : GUI_BUILD + " (dev)";
    badge.textContent = `${release}  ·  backend ${hh}:${mm}:${ss} (pid ${v.backend_pid})  ·  app.js ${jsTag}`;
    badge.title = "点击查看完整 /api/version 响应";
    badge.onclick = () => alert(JSON.stringify(v, null, 2));
  } catch (e) {
    badge.textContent = "build ? (无法拉 /api/version: " + e.message + ")";
    badge.style.color = "#e0665b";
  }
})();

// ---------------- 日志窗口拖拽调高 ----------------
(function initLogResizer() {
  const resizer  = $("logResizer");
  const logbar   = $("logbar");
  const collapseBtn = $("btnLogCollapse");
  if (!resizer || !logbar) return;

  const MIN_H = 80, MAX_H = () => Math.max(200, window.innerHeight - 300);
  const LS_KEY = "opcuasim.logHeight";
  const LS_COLLAPSED = "opcuasim.logCollapsed";

  function applyHeight(h) {
    h = Math.max(MIN_H, Math.min(MAX_H(), h));
    document.documentElement.style.setProperty("--log-h", h + "px");
  }
  // 恢复保存的高度
  const saved = parseInt(localStorage.getItem(LS_KEY), 10);
  if (saved && saved > MIN_H) applyHeight(saved);

  // 拖拽
  let dragging = false, startY = 0, startH = 0;
  resizer.addEventListener("mousedown", (e) => {
    if (logbar.classList.contains("collapsed")) return;
    dragging = true; startY = e.clientY;
    startH = logbar.getBoundingClientRect().height;
    resizer.classList.add("dragging");
    document.body.style.userSelect = "none";
    document.body.style.cursor = "ns-resize";
    e.preventDefault();
  });
  window.addEventListener("mousemove", (e) => {
    if (!dragging) return;
    // 往上拖 = 变高 (startY - clientY 是正)
    applyHeight(startH + (startY - e.clientY));
  });
  window.addEventListener("mouseup", () => {
    if (!dragging) return;
    dragging = false;
    resizer.classList.remove("dragging");
    document.body.style.userSelect = "";
    document.body.style.cursor = "";
    const h = logbar.getBoundingClientRect().height;
    localStorage.setItem(LS_KEY, Math.round(h));
  });

  // 折叠按钮
  function setCollapsed(c) {
    logbar.classList.toggle("collapsed", c);
    resizer.classList.toggle("collapsed", c);
    collapseBtn.textContent = c ? "展开" : "收起";
    if (!c) {
      const h = parseInt(localStorage.getItem(LS_KEY), 10) || 240;
      applyHeight(h);
    }
    localStorage.setItem(LS_COLLAPSED, c ? "1" : "0");
  }
  if (collapseBtn) {
    collapseBtn.onclick = () => setCollapsed(!logbar.classList.contains("collapsed"));
    if (localStorage.getItem(LS_COLLAPSED) === "1") setCollapsed(true);
  }

  // 窗口大小改变时防止 log 挤出可视区
  window.addEventListener("resize", () => {
    const cur = logbar.getBoundingClientRect().height;
    if (cur > MAX_H()) applyHeight(MAX_H());
  });
})();
