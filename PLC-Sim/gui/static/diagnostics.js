// SSE 日志、版本诊断与日志面板
"use strict";

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
    const assetTimestamp = Math.max(
      0,
      ...Object.values(v.static_mtime || {}).filter(value => Number.isFinite(value))
    );
    const assetMtime = assetTimestamp ? new Date(assetTimestamp * 1000) : null;
    const assetTag = assetMtime
      ? `${String(assetMtime.getHours()).padStart(2,"0")}:${String(assetMtime.getMinutes()).padStart(2,"0")}`
      : "?";
    // 部署版走 CI 写入的 VERSION, 本地开发回落到手写的 GUI_BUILD
    const release = v.release && v.release !== "dev" ? v.release : GUI_BUILD + " (dev)";
    badge.textContent = `${release}  ·  backend ${hh}:${mm}:${ss} (pid ${v.backend_pid})  ·  frontend ${assetTag}`;
    badge.title = "点击查看完整 /api/version 响应";
    badge.onclick = () => alert(JSON.stringify(v, null, 2));
  } catch (e) {
    badge.textContent = "build ? (无法拉 /api/version: " + e.message + ")";
    badge.classList.add("error");
  }
})();

// ---------------- 日志窗口拖拽调高 ----------------
(function initLogResizer() {
  const resizer  = $("logResizer");
  const logbar   = $("logbar");
  const collapseBtn = $("btnLogCollapse");
  if (!resizer || !logbar) return;

  const MIN_H = 80, MAX_H = () => Math.max(200, window.innerHeight - 300);
  const LS_KEY = "plcsim.logHeight";
  const LS_COLLAPSED = "plcsim.logCollapsed";

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
