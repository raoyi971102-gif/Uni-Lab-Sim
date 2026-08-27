// 工程、变量提取与 POU 编辑
"use strict";

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
    const payload = { strategy };
    if (strategy === "online") {
      const preflight = await get("/api/project/deploy/preflight");
      if (!preflight.online_allowed) throw new Error(
        preflight.warning || "GUI 在线下载已关闭，请通过 pTLC PlcProgramService 部署"
      );
      if (!confirm(preflight.warning + "\n\n工程 SHA256:\n" + preflight.project_sha256)) return;
      payload.confirm_online = true;
      payload.expected_project_sha256 = preflight.project_sha256;
    }
    const r = await post("/api/project/download", payload);
    alert("下载报告:\n" + JSON.stringify(r.report, null, 2));
  } catch (e) { alert(e.message); }
};

$("btnVersions").onclick = async () => {
  try {
    const r = await get("/api/project/versions");
    const lines = (r.items || []).map(item =>
      `${item.rev}  ${String(item.sha256).slice(0, 12)}  ${item.message || ""}` +
      `${item.deployed_at ? "  [已部署]" : ""}`
    );
    alert(lines.length ? lines.join("\n") : "当前工程还没有版本快照");
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
$("btnPouSymbols").onclick = async () => {
  const path = $("pouPath").value.trim();
  if (!path) return alert("请先选择 POU/GVL 路径");
  try {
    const catalog = await get("/api/project/symbols?path=" + encodeURIComponent(path));
    const summary = (catalog.symbols || []).map(item =>
      `${item.exported ? "✓" : "○"} ${item.name}: ${item.type}`
    ).join("\n");
    const name = prompt("当前符号:\n" + summary + "\n\n输入要切换的变量名");
    if (!name) return;
    const current = (catalog.symbols || []).find(item => item.name === name);
    if (!current) throw new Error("声明中没有变量: " + name);
    const enabled = confirm(
      `${name} 当前${current.exported ? "已" : "未"}导出。\n` +
      `确定将其设为${current.exported ? "不导出" : "读写导出"}吗？`
    ) ? !current.exported : current.exported;
    if (enabled === current.exported) return;
    const result = await post("/api/project/symbol", {
      path, name, enabled, compile: $("chkSetCompile").checked,
    });
    alert(JSON.stringify(result, null, 2));
    await readPouByPath(path);
  } catch (e) { alert(e.message); }
};
