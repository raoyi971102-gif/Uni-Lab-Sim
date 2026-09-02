"use strict";

const $ = (id) => document.getElementById(id);
const state = { bootstrap: null, polling: null, artifact: null, running: false };

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

async function api(path, options = {}) {
  const response = await fetch(path, options);
  const payload = response.headers.get("content-type")?.includes("application/json")
    ? await response.json()
    : null;
  if (!response.ok) throw new Error(payload?.detail || `请求失败 (${response.status})`);
  return payload;
}

function selectedMode() {
  return document.querySelector('input[name="acceptanceMode"]:checked').value;
}

function formatTime(value) {
  if (!value) return "--";
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", second: "2-digit",
  }).format(new Date(value));
}

function statusClass(status) {
  return String(status || "neutral").toLowerCase();
}

function setMode(mode) {
  const soft = mode === "soft_plc";
  $("simulatorConfig").classList.toggle("hidden", soft);
  $("softPlcConfig").classList.toggle("hidden", !soft);
  $("runButtonHint").textContent = soft
    ? "供应商 L2 软 PLC · 必须绑定候选包"
    : "内置 SZLab L1 仿真 · 约 15 秒";
  hideError();
}

function showError(message) {
  $("runError").textContent = message;
  $("runError").classList.remove("hidden");
}

function hideError() {
  $("runError").textContent = "";
  $("runError").classList.add("hidden");
}

function showBootstrapError(message) {
  $("bootstrapError").textContent = message;
  $("bootstrapRecovery").classList.remove("hidden");
}

function hideBootstrapError() {
  $("bootstrapError").textContent = "";
  $("bootstrapRecovery").classList.add("hidden");
}

function showHistoryError(message) {
  $("historyError").textContent = message;
  $("historyRecovery").classList.remove("hidden");
}

function hideHistoryError() {
  $("historyError").textContent = "";
  $("historyRecovery").classList.add("hidden");
}

function setRunning(running) {
  state.running = running;
  $("runButton").disabled = running;
  document.querySelectorAll('input[name="acceptanceMode"], #endpoint, #artifactFile, #safeMode')
    .forEach((control) => { control.disabled = running; });
  $("runButton").querySelector("span").textContent = running ? "正在执行完整门禁" : "运行完整验收";
}

function renderCaseTable(cases) {
  if (!cases?.length) {
    $("caseTable").innerHTML = '<tr><td colspan="6" class="empty-cell">等待首次验收</td></tr>';
    return;
  }
  $("caseTable").innerHTML = cases.map((item) => `
    <tr>
      <td><strong>${escapeHtml(item.case_id)}</strong><br><span class="muted-copy">${escapeHtml(item.name)}</span></td>
      <td>${escapeHtml(item.safety_level)}</td>
      <td>${escapeHtml(item.iteration)}</td>
      <td>${Number(item.duration_ms || 0).toFixed(1)} ms</td>
      <td><span class="case-status ${statusClass(item.status)}">${escapeHtml(item.status)}</span></td>
      <td>${escapeHtml(item.message || "--")}</td>
    </tr>`).join("");
}

function renderRun(snapshot) {
  const runState = snapshot.state || "IDLE";
  const report = snapshot.report;
  const running = runState === "RUNNING";
  setRunning(running);
  $("runMessage").textContent = snapshot.error ? `${snapshot.message}：${snapshot.error}` : snapshot.message;
  $("runStatus").textContent = runState === "IDLE" ? "READY" : runState;
  $("runStatus").className = `status-badge ${runState === "IDLE" ? "neutral" : statusClass(runState)}`;
  $("progressTrack").className = "progress-track";
  if (running) $("progressTrack").classList.add("running");
  else if (runState === "PASSED") $("progressTrack").classList.add("complete");
  else if (["FAILED", "BLOCKED", "ABORTED"].includes(runState)) $("progressTrack").classList.add("failed");

  $("runId").textContent = report?.run_id || snapshot.request_id || "--";
  $("evidenceLevel").textContent = report?.evidence_level || (running ? snapshot.mode : "--");
  $("elapsedTime").textContent = running
    ? `${Number(snapshot.elapsed_seconds || 0).toFixed(1)} s`
    : report ? `${((new Date(report.ended_at) - new Date(report.started_at)) / 1000).toFixed(1)} s` : "--";
  const counts = report?.case_summary || {};
  $("caseCounts").textContent = Object.entries(counts).map(([key, value]) => `${key} ${value}`).join(" · ") || "--";
  renderCaseTable(report?.cases);

  for (const [id, suffix] of [["openReport", "report"], ["downloadReport", "download"]]) {
    const link = $(id);
    if (report?.run_id) {
      link.href = `/api/reports/${encodeURIComponent(report.run_id)}/${suffix}`;
      link.classList.remove("disabled");
      link.removeAttribute("aria-disabled");
      if (suffix === "report") link.target = "_blank";
    } else {
      link.removeAttribute("href");
      link.classList.add("disabled");
      link.setAttribute("aria-disabled", "true");
    }
  }
}

function renderCoverage(items) {
  if (!items.length) {
    $("coverageList").innerHTML = '<p class="empty-state">当前覆盖矩阵没有待补证据</p>';
    return;
  }
  $("coverageList").innerHTML = items.map((item) => `
    <div class="coverage-item">
      <div class="coverage-code">
        <strong>${escapeHtml(item.requirement)}</strong>
        <span class="coverage-state">${escapeHtml(item.status)}</span>
      </div>
      <p>${escapeHtml(item.evidence)}</p>
    </div>`).join("");
}

function renderHistory(reports) {
  if (!reports?.length) {
    $("historyTable").innerHTML = '<tr><td colspan="5" class="empty-cell">暂无历史报告</td></tr>';
    return;
  }
  $("historyTable").innerHTML = reports.map((report) => `
    <tr>
      <td>${escapeHtml(formatTime(report.started_at))}</td>
      <td>${escapeHtml(report.environment_id)}</td>
      <td>${escapeHtml(report.run_id)}</td>
      <td><span class="case-status ${statusClass(report.status)}">${escapeHtml(report.status)}</span></td>
      <td><a class="history-link" href="/api/reports/${encodeURIComponent(report.run_id)}/report" target="_blank">查看</a></td>
    </tr>`).join("");
}

async function uploadArtifact() {
  const file = $("artifactFile").files[0];
  if (!file) throw new Error("请选择不可变 PLC 候选包");
  if (state.artifact?.source === file) return state.artifact;
  $("runButtonHint").textContent = "正在读取并绑定 PLC 候选包";
  const uploaded = await api(`/api/artifacts?filename=${encodeURIComponent(file.name)}`, {
    method: "POST",
    headers: { "Content-Type": "application/octet-stream" },
    body: file,
  });
  state.artifact = { ...uploaded, source: file };
  return state.artifact;
}

async function startRun() {
  hideError();
  const mode = selectedMode();
  setRunning(true);
  try {
    let artifact = null;
    if (mode === "soft_plc") artifact = await uploadArtifact();
    const snapshot = await api("/api/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        mode,
        endpoint: mode === "soft_plc" ? $("endpoint").value.trim() : null,
        confirm_safe_test_mode: mode === "soft_plc" ? $("safeMode").checked : false,
        artifact_id: artifact?.artifact_id || null,
      }),
    });
    renderRun(snapshot);
    pollRun();
  } catch (error) {
    setRunning(false);
    setMode(mode);
    showError(error.message);
  }
}

async function pollRun() {
  clearTimeout(state.polling);
  try {
    const snapshot = await api("/api/run");
    renderRun(snapshot);
    if (snapshot.state === "RUNNING") {
      state.polling = setTimeout(pollRun, 800);
    } else {
      await refreshHistory().catch(() => {});
      setMode(selectedMode());
    }
  } catch (error) {
    showError(error.message);
    setRunning(false);
  }
}

async function refreshHistory() {
  try {
    const payload = await api("/api/history");
    renderHistory(payload.reports);
    hideHistoryError();
  } catch (error) {
    showHistoryError(error.message);
    throw error;
  }
}

async function initialize() {
  hideBootstrapError();
  $("baselineStatus").classList.remove("passed", "failed");
  $("baselineStatus").querySelector("span:last-child").textContent = "L0 基线检查中";
  try {
    const payload = await api("/api/bootstrap");
    state.bootstrap = payload;
    $("packageVersion").textContent = `验收包 ${payload.version}`;
    $("plcSimVersion").textContent = `PLC-Sim ${payload.plc_sim_version}`;
    $("endpoint").value = payload.soft_plc_endpoint;
    $("protocolSummary").textContent = `${payload.project_id} · protocol ${payload.protocol_version} · ${payload.node_count} nodes`;
    $("dataDirectory").textContent = payload.data_dir;
    $("baselineStatus").classList.add(payload.l0_status.toLowerCase());
    $("baselineStatus").querySelector("span:last-child").textContent = `L0 ${payload.l0_status} · ${payload.node_count} 点位`;
    renderCoverage(payload.coverage_gaps);
    renderHistory(payload.history);
    hideHistoryError();
    renderRun(await api("/api/run"));
  } catch (error) {
    $("baselineStatus").classList.add("failed");
    $("baselineStatus").querySelector("span:last-child").textContent = "L0 配置读取失败";
    showBootstrapError(error.message);
  }
}

document.querySelectorAll('input[name="acceptanceMode"]').forEach((input) => {
  input.addEventListener("change", () => setMode(input.value));
});
$("artifactFile").addEventListener("change", () => {
  state.artifact = null;
  $("artifactName").textContent = $("artifactFile").files[0]?.name || "尚未选择文件";
});
$("runButton").addEventListener("click", startRun);
$("refreshHistory").addEventListener("click", () => refreshHistory().catch(() => {}));
$("retryHistory").addEventListener("click", () => refreshHistory().catch(() => {}));
$("retryBootstrap").addEventListener("click", initialize);
initialize();
