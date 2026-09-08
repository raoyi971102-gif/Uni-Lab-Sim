"use strict";

const $ = (id) => document.getElementById(id);
const state = {
  bootstrap: null,
  polling: null,
  artifact: null,
  running: false,
  activeMode: "simulator",
  endpointValues: {},
};

const MODE_DETAILS = {
  simulator: {
    external: false,
    onSite: false,
    material: false,
    hint: "内置 SZLab 九设备 L1 仿真 · 约 25 秒",
    evidenceLevel: "L1 协议仿真证据",
  },
  soft_plc: {
    external: true,
    onSite: false,
    material: false,
    hint: "供应商 L2 软 PLC · 必须绑定候选包",
    evidenceLevel: "L2 供应商软 PLC 证据",
    safetyTitle: "已确认软 PLC 进入受控测试模式",
    safetyDescription: "候选程序已部署，测试身份、变量权限和动作范围已经确认。",
  },
  bench: {
    external: true,
    onSite: true,
    material: false,
    hint: "L3 真机台架 · 将向真实机构派发动作",
    evidenceLevel: "L3 真机台架证据",
    safetyTitle: "已确认 L3 台架全部安全前置",
    safetyDescription: "PLC 验收模式、急停、门禁、控制权、运动区域和机构安全初态均已现场确认。",
  },
  fat_sat: {
    external: true,
    onSite: true,
    material: true,
    hint: "L4 FAT/SAT · 完整设备与指定物料",
    evidenceLevel: "L4 FAT/SAT 现场证据",
    safetyTitle: "已确认 L4 FAT/SAT 全部安全前置",
    safetyDescription: "PLC 验收模式、急停、门禁、控制权、指定物料、运动区域和机构安全初态均已现场确认。",
  },
};

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
  const details = MODE_DETAILS[mode] || MODE_DETAILS.simulator;
  if (state.activeMode !== mode) $("safeMode").checked = false;
  state.activeMode = mode;
  $("simulatorConfig").classList.toggle("hidden", details.external);
  $("externalPlcConfig").classList.toggle("hidden", !details.external);
  $("onSiteEvidence").classList.toggle("hidden", !details.onSite);
  $("materialConfig").classList.toggle("hidden", !details.material);
  if (details.external) {
    $("endpoint").value = state.endpointValues[mode] || "";
    $("safeModeTitle").textContent = details.safetyTitle;
    $("safeModeDescription").textContent = details.safetyDescription;
  }
  $("runButtonHint").textContent = details.hint;
  hideError();
}

function validateRunInputs(mode, details) {
  if (!details.external) return;
  let endpoint;
  try {
    endpoint = new URL($("endpoint").value.trim());
  } catch {
    throw new Error("请输入有效的 opc.tcp:// Endpoint");
  }
  if (endpoint.protocol !== "opc.tcp:" || !endpoint.hostname || !endpoint.port) {
    throw new Error("OPC UA Endpoint 必须包含 opc.tcp://、主机和端口");
  }
  if (!$("namespaceUri").value.trim()) throw new Error("请输入 OPC UA Namespace URI");
  if (details.onSite && !$("supervisor").value.trim()) throw new Error("请输入现场监护/见证人");
  if (details.onSite && !$("testLocation").value.trim()) throw new Error("请输入台架/现场位置");
  if (details.material && !$("materialReference").value.trim()) throw new Error("请输入物料或批次标识");
  if (!$("safeMode").checked) throw new Error(`请先完成并确认 ${mode === "soft_plc" ? "L2" : mode === "bench" ? "L3" : "L4"} 安全前置`);
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
  document.querySelectorAll(
    'input[name="acceptanceMode"], #endpoint, #namespaceUri, #artifactFile, #safeMode, #supervisor, #testLocation, #materialReference',
  )
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
  $("evidenceLevel").textContent = report?.evidence_level
    || (running ? MODE_DETAILS[snapshot.mode]?.evidenceLevel || snapshot.mode : "--");
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
  const details = MODE_DETAILS[mode];
  try {
    validateRunInputs(mode, details);
    setRunning(true);
    let artifact = null;
    if (details.external) artifact = await uploadArtifact();
    const snapshot = await api("/api/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        mode,
        endpoint: details.external ? $("endpoint").value.trim() : null,
        namespace_uri: details.external ? $("namespaceUri").value.trim() : null,
        confirm_safe_test_mode: details.external ? $("safeMode").checked : false,
        artifact_id: artifact?.artifact_id || null,
        supervisor: details.onSite ? $("supervisor").value.trim() : null,
        test_location: details.onSite ? $("testLocation").value.trim() : null,
        material_reference: details.material ? $("materialReference").value.trim() : null,
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
      if (MODE_DETAILS[selectedMode()].external) $("safeMode").checked = false;
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
    state.endpointValues = { ...payload.environment_endpoints };
    $("namespaceUri").value = payload.namespace_uri;
    $("protocolSummary").textContent = `${payload.project_id} · protocol ${payload.protocol_version} · ${payload.node_count} nodes`;
    $("dataDirectory").textContent = payload.data_dir;
    $("baselineStatus").classList.add(payload.l0_status.toLowerCase());
    $("baselineStatus").querySelector("span:last-child").textContent = `L0 ${payload.l0_status} · ${payload.node_count} 点位`;
    renderCoverage(payload.coverage_gaps);
    renderHistory(payload.history);
    hideHistoryError();
    setMode(selectedMode());
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
$("endpoint").addEventListener("input", () => {
  state.endpointValues[state.activeMode] = $("endpoint").value;
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
