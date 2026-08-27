// OPC UA Server 与握手代理控制
"use strict";

// ---------------- Tab: Sim ----------------
function syncServerProfile() {
  const ptlc = $("simProfile").value === "ptlc";
  $("simNodeTableLabel").textContent = ptlc ? "PTLC 节点 YAML" : "变量 CSV";
  $("simCsvFile").closest("label").classList.toggle("hidden", ptlc);
  $("simOcc").closest("label").classList.toggle("hidden", ptlc);
  $("simCsv").value = ptlc
    ? "config/ptlc_nodes.yaml"
    : "data/szlab_plc_0810.csv";
}
$("simProfile").onchange = syncServerProfile;
syncServerProfile();

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
    if ($("simProfile").value === "ptlc") {
      await requireBackendCapability("ptlc_server_profile", "PTLC 节点模型");
    }
    const r = await post("/api/server/start", {
      csv: $("simCsv").value.trim() || null,
      profile: $("simProfile").value,
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
  const ptlc = $("agentProfile").value === "ptlc";
  $("ptlcAgentHint").classList.toggle("hidden", !ptlc);
  $("agentWorkflowField").classList.toggle("hidden", ptlc);
  if (ptlc) {
    for (const id of [
      "agentPositionField", "agentPumpField", "agentS09VolumeField",
      "agentS07BalanceField", "agentS09BalanceField",
      "agentS1HostField", "agentS1PortField",
    ]) $(id).classList.add("hidden");
    $("agentDelayField").classList.remove("hidden");
    return;
  }
  const workflow = $("agentWorkflow").value;
  $("agentS1HostField").classList.remove("hidden");
  $("agentS1PortField").classList.remove("hidden");
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
    "agentProfile",
    "agentWorkflow", "agentPosition", "agentPump", "agentDelayMs",
    "agentPollMs", "agentS09Volume", "agentS07Balance", "agentS09Balance",
    "agentTimeScale", "ptlcFaultStation", "ptlcFaultCode", "ptlcFaultOutcome",
    "ptlcSensorMode",
    "agentS1Host", "agentS1Port",
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
    const profile = $("agentProfile").value;
    if (profile === "ptlc") {
      await requireBackendCapability("ptlc_handshake_agent", "PTLC L2 代理");
    } else {
      await requireBackendCapability("szlab_package_runtime", "SZLab 设备包仿真");
    }
    const body = {
      profile,
      host: $("agentHost").value.trim() || "127.0.0.1",
      port: parseInt($("agentPort").value, 10) || 4855,
      config: $("agentCfg").value.trim() || null,
      poll_ms: readAgentNumber("agentPollMs", "轮询间隔", 5, 60000, true),
      time_scale: readAgentNumber("agentTimeScale", "仿真时间倍率", 0.01, 1000),
    };
    if (profile === "ptlc") {
      body.delay_ms = $("agentDelayMs").value.trim()
        ? readAgentNumber("agentDelayMs", "动作延时", 0, 3600000, true)
        : undefined;
      body.sensor_mode = $("ptlcSensorMode").value;
    } else {
      body.workflow = workflow;
      body.s1_host = $("agentS1Host").value.trim() || "127.0.0.1";
      body.s1_port = readAgentNumber("agentS1Port", "S1 HTTP 端口", 1, 65535, true);
    }
    if (profile === "szlab" &&
      workflow !== "szlab_photoshotting_workflow" &&
      $("agentDelayMs").value.trim()
    ) {
      body.delay_ms = readAgentNumber("agentDelayMs", "动作延时", 0, 3600000, true);
    }
    if (profile === "szlab" && SZLAB_S04_WORKFLOWS.has(workflow)) {
      body.position = readAgentNumber("agentPosition", "S04 位置", 1, 6, true);
    }
    if (profile === "szlab" && SZLAB_PUMP_WORKFLOWS.has(workflow)) {
      body.pump = readAgentNumber("agentPump", "储液泵", 1, 3, true);
    }
    if (profile === "szlab" && SZLAB_S09_WORKFLOWS.has(workflow)) {
      body.s09_remaining_volume_ml = readAgentNumber(
        "agentS09Volume", "S09 初始余量", 0.1, Number.MAX_SAFE_INTEGER
      );
      body.s09_balance_reading = readAgentNumber(
        "agentS09Balance", "S09 模拟天平读数",
        -Number.MAX_SAFE_INTEGER, Number.MAX_SAFE_INTEGER
      );
    }
    if (profile === "szlab" && SZLAB_S07_WORKFLOWS.has(workflow)) {
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
$("agentProfile").onchange = () => {
  const ptlc = $("agentProfile").value === "ptlc";
  $("agentCfg").value = ptlc
    ? "config/ptlc_handshake.yaml"
    : "config/szlab_handshake.yaml";
  syncSzlabAgentOptions();
};
syncSzlabAgentOptions();
$("btnAgentStop").onclick = () => stopManagedProcess(
  $("btnAgentStop"), "/api/agent/stop"
);
$("btnPtlcFault").onclick = async () => {
  try {
    const result = await post("/api/agent/ptlc/fault", {
      station: $("ptlcFaultStation").value,
      action_code: readAgentNumber(
        "ptlcFaultCode", "PTLC 动作码", -32768, 32767, true
      ),
      outcome: $("ptlcFaultOutcome").value,
    });
    alert("运行期故障已更新:\n" + JSON.stringify(result.faults, null, 2));
  } catch (e) { alert(e.message); }
};

const PTLC_SENSOR_SITES = [
  "collect_bottle", "staging_a", "staging_b",
  "sampling_tray_1", "sampling_tray_2",
  ...Array.from({ length: 12 }, (_, index) =>
    `rack_${String(index + 1).padStart(2, "0")}`),
];

function fillPtlcSiteSelect(id, includeExternal) {
  const values = includeExternal ? ["external", ...PTLC_SENSOR_SITES] : PTLC_SENSOR_SITES;
  $(id).replaceChildren(...values.map((value) => {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = value;
    return option;
  }));
}

function syncPtlcEventFields() {
  const siteSet = $("ptlcEventKind").value === "site_set";
  $("ptlcEventSourceField").classList.toggle("hidden", siteSet);
  $("ptlcEventTargetField").classList.toggle("hidden", siteSet);
  $("ptlcEventSiteField").classList.toggle("hidden", !siteSet);
  $("ptlcEventPresentField").classList.toggle("hidden", !siteSet);
}

fillPtlcSiteSelect("ptlcEventSource", true);
fillPtlcSiteSelect("ptlcEventTarget", true);
fillPtlcSiteSelect("ptlcEventSite", false);
$("ptlcEventTarget").value = "collect_bottle";
$("ptlcEventKind").onchange = syncPtlcEventFields;
syncPtlcEventFields();

$("btnPtlcWorldEvent").onclick = async () => {
  try {
    const kind = $("ptlcEventKind").value;
    const event = {
      event_id: `gui-${Date.now()}-${Math.random().toString(16).slice(2)}`,
      kind,
    };
    if (kind === "site_set") {
      event.site = $("ptlcEventSite").value;
      event.present = $("ptlcEventPresent").value === "true";
    } else {
      event.source = $("ptlcEventSource").value;
      event.target = $("ptlcEventTarget").value;
      if (event.source === event.target) {
        throw new Error("来源站点和目标站点不能相同");
      }
    }
    const result = await post("/api/agent/ptlc/world", { events: [event] });
    alert("传感器事件已提交:\n" + JSON.stringify(result.world.events.at(-1), null, 2));
  } catch (e) { alert(e.message); }
};
