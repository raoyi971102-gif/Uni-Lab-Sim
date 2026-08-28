// 在线变量浏览、监控与维护写入
"use strict";

// ---------------- 在线变量 ----------------
const variablePage = {
  offset: 0,
  limit: 100,
  total: 0,
  query: "",
  items: [],
  loading: false,
};
const MONITOR_STORAGE_KEY = "plcsim.monitored-variables.v2";
const LEGACY_MONITOR_STORAGE_KEY = "plcsim.monitored-variables.v1";
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
      array_len: Math.max(0, Number(item.array_len || 0)),
    }));
}

function hydrateStoredMonitors(items) {
  return sanitizeStoredMonitors(items).map(item => ({
    ...item,
    value: null,
    draft: null,
    drafts: {},
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

function arrayLength(item) {
  return Math.max(0, Number(item?.array_len || 0));
}

function elementValue(item, elementIndex) {
  if (elementIndex === null) return item.value;
  return Array.isArray(item.value) ? item.value[elementIndex] : undefined;
}

function variableRows(items) {
  return items.flatMap((item, itemIndex) => {
    const length = arrayLength(item);
    if (!length) return [{ item, itemIndex, elementIndex: null }];
    return Array.from({ length }, (_, elementIndex) => ({
      item, itemIndex, elementIndex,
    }));
  });
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

  tbody.innerHTML = variableRows(variablePage.items).map(({ item, itemIndex, elementIndex }) => {
    const monitored = isMonitored(item.node_id);
    const selected = selectedVariables.has(item.node_id);
    const suffix = elementIndex === null ? "" : `[${elementIndex + 1}]`;
    const currentValue = elementValue(item, elementIndex);
    const arrayHint = elementIndex === null ? "" :
      `<small>数组元素 ${elementIndex + 1} / ${arrayLength(item)}</small>`;
    return `<tr data-index="${itemIndex}" data-element-index="${elementIndex ?? ""}">` +
      `<td class="select-cell"><input class="var-select" type="checkbox" ` +
      `aria-label="选择 ${escapeHtml(item.name + suffix)}" ${selected ? "checked" : ""} ` +
      `${monitored ? "disabled" : ""}></td>` +
      `<td class="variable-name"><strong>${escapeHtml(item.name + suffix)}</strong>` +
      `${arrayHint || `<small>${escapeHtml(item.english_name || "")}</small>`}</td>` +
      `<td><span class="type-pill">${escapeHtml(item.data_type)}</span></td>` +
      `<td><span class="read-value">${escapeHtml(formatVariableValue(currentValue))}</span></td>` +
      `<td class="node-id">${escapeHtml(item.node_id + suffix)}</td>` +
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
  renderServerVariables();
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

function monitorDraft(item, elementIndex) {
  if (elementIndex === null) return item.draft === null ? item.value : item.draft;
  item.drafts ||= {};
  return Object.prototype.hasOwnProperty.call(item.drafts, elementIndex)
    ? item.drafts[elementIndex]
    : elementValue(item, elementIndex);
}

function monitorEditor(item, elementIndex) {
  const draft = monitorDraft(item, elementIndex);
  if (item.data_type === "BOOLEAN") {
    const current = draft === true || String(draft).toLowerCase() === "true";
    return `<select class="value-editor monitor-editor" aria-label="${escapeHtml(item.name)} 新值">` +
      `<option value="true"${current ? " selected" : ""}>true</option>` +
      `<option value="false"${!current ? " selected" : ""}>false</option></select>`;
  }
  const isNumeric = ["BYTE", "INT16", "INT32", "FLOAT", "DOUBLE"].includes(item.data_type);
  const step = ["FLOAT", "DOUBLE"].includes(item.data_type) ? "any" : "1";
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

  tbody.innerHTML = variableRows(monitoredVariables).map(({ item, itemIndex, elementIndex }) => {
    const suffix = elementIndex === null ? "" : `[${elementIndex + 1}]`;
    const arrayHint = elementIndex === null ? (item.english_name || "") :
      `数组元素 ${elementIndex + 1} / ${arrayLength(item)}`;
    return `<tr data-index="${itemIndex}" data-element-index="${elementIndex ?? ""}">` +
    `<td class="variable-name"><strong>${escapeHtml(item.name + suffix)}</strong>` +
    `<small>${escapeHtml(arrayHint)}</small></td>` +
    `<td><span class="type-pill">${escapeHtml(item.data_type)}</span>` +
    `${item.write_owner === "plc" ? '<small>PLC 输出</small>' : ''}</td>` +
    `<td><span class="monitor-current">--</span></td>` +
    `<td>${monitorEditor(item, elementIndex)}</td>` +
    `<td class="node-id">${escapeHtml(item.node_id + suffix)}</td>` +
    `<td class="monitor-actions">` +
    `<button class="btn small monitor-write">写入</button>` +
    `<button class="text-button monitor-remove">移除</button>` +
    `</td></tr>`;
  }).join("");
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
    const elementIndex = row.dataset.elementIndex === ""
      ? null
      : Number(row.dataset.elementIndex);
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
      current.textContent = formatVariableValue(elementValue(item, elementIndex));
    }
    const ownershipBlocked = item.write_owner === "plc" &&
      !$("monitorMaintenanceOverride").checked;
    editor.disabled = !running || item.missing || item.offline || ownershipBlocked;
    writeButton.disabled = monitorState.loading || !running || item.missing ||
      item.offline || ownershipBlocked;
    writeButton.title = ownershipBlocked ? "PLC 输出由握手/行为代理独占；维护写入可临时解锁" : "";
    const draft = monitorDraft(item, elementIndex);
    const currentValue = elementValue(item, elementIndex);
    const hasDraft = elementIndex === null
      ? item.draft !== null
      : Object.prototype.hasOwnProperty.call(item.drafts || {}, elementIndex);
    if (!hasDraft && document.activeElement !== editor) {
      editor.value = formatVariableValue(currentValue) === "--"
        ? (item.data_type === "BOOLEAN" ? "false" : "")
        : formatVariableValue(currentValue);
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
    let shapeChanged = false;
    monitoredVariables.forEach(item => {
      const fresh = values.get(item.node_id);
      item.offline = false;
      item.missing = missing.has(item.node_id);
      if (fresh) {
        item.name = fresh.name;
        item.english_name = fresh.english_name || "";
        item.data_type = fresh.data_type;
        const nextArrayLen = Math.max(0, Number(fresh.array_len || 0));
        if (arrayLength(item) !== nextArrayLen) shapeChanged = true;
        item.array_len = nextArrayLen;
        item.write_owner = fresh.write_owner || "shared";
        item.writable = fresh.writable !== false;
        item.value = fresh.value;
      }
    });
    persistMonitors();

    variablePage.items.forEach(item => {
      const fresh = values.get(item.node_id);
      if (fresh) item.value = fresh.value;
    });
    if (shapeChanged) renderMonitoredVariables();
    else updateMonitorRows();
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
$("monitorMaintenanceOverride").onchange = updateMonitorRows;
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
  if (!item) return;
  const elementIndex = row.dataset.elementIndex === ""
    ? null
    : Number(row.dataset.elementIndex);
  if (elementIndex === null) item.draft = editor.value;
  else {
    item.drafts ||= {};
    item.drafts[elementIndex] = editor.value;
  }
});

$("monitorVarsTable").addEventListener("click", async event => {
  const row = event.target.closest("tr[data-index]");
  if (!row) return;
  const index = Number(row.dataset.index);
  const item = monitoredVariables[index];
  if (!item) return;
  const elementIndex = row.dataset.elementIndex === ""
    ? null
    : Number(row.dataset.elementIndex);

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
      index: elementIndex,
      maintenance_override: $("monitorMaintenanceOverride").checked,
    }, 7000);
    item.value = response.value;
    if (elementIndex === null) item.draft = null;
    else if (item.drafts) delete item.drafts[elementIndex];
    item.offline = false;
    item.missing = false;
    variablePage.items.forEach(pageItem => {
      if (pageItem.node_id === item.node_id) pageItem.value = response.value;
    });
    updateMonitorRows();
    renderServerVariables();
    monitorMessage(
      `${item.name}${elementIndex === null ? "" : `[${elementIndex + 1}]`} 已写入，` +
      `服务器回读值为 ${formatVariableValue(
        elementIndex === null ? response.value : response.element_value
      )}。`,
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
