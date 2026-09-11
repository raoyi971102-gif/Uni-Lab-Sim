"use strict";
(() => {
  const $ = id => document.getElementById(id);
  const show = (ok, text) => { const n = $("factoryResult"); n.className = `result-box ${ok ? "success" : "error"}`; n.textContent = text; };
  const render = data => {
    const s = data.evidence && !Array.isArray(data.evidence.nodes) ? data.evidence : data;
    const nodes = Array.isArray(s.nodes) ? s.nodes : [];
    $("factorySummary").textContent = `变量 ${Array.isArray(s.nodes) ? nodes.length : (s.nodes ?? 0)} · 不支持 ${s.unsupported ?? 0} · 重复 ${s.duplicates ?? 0}`;
    const groups = {};
    nodes.forEach(n => (groups[n.device_id || "device"] ||= {name:n.device_group || n.device_path || n.device_id, actions:0, states:0})[n.role === "action" ? "actions" : "states"]++);
    const box = $("factoryDevices"); box.innerHTML = Object.values(groups).map(g => `<div class="object-row"><strong>${g.name}</strong><span>动作 ${g.actions} · 状态 ${g.states}</span></div>`).join("") || "暂无设备";
  };
  async function post(url, body) { const r = await fetch(url, {method:"POST", headers:{"content-type":"application/json"}, body:JSON.stringify(body)}); const d = await r.json(); if (!r.ok) throw new Error(d.detail || "请求失败"); return d; }
  async function ensureUpload() {
    const file = $("factoryFile")?.files?.[0];
    if (!file) throw new Error("请先选择 CSV 文件");
    const bytes = new Uint8Array(await file.arrayBuffer());
    let binary = ""; for (const b of bytes) binary += String.fromCharCode(b);
    const d = await post("/api/csv/upload", {filename:file.name, content_b64:btoa(binary)});
    $("factorySource").value = d.path; $("factoryUploaded").textContent = `已上传：${file.name}（${d.count} 个变量）`; return d.path;
  }
  $("btnFactoryInspect")?.addEventListener("click", async () => { try { const source=await ensureUpload(); const d=await post("/api/factory/inspect", {source}); render(d); show(true, "变量提取完成，可继续生成设备包"); } catch(e) { show(false, e.message); } });
  $("btnFactoryBuild")?.addEventListener("click", async () => { try { const source=await ensureUpload(); const d=await post("/api/factory/build", {source, output:$("factoryOutput").value || null}); render(d); show(true, `设备包已生成：${d.package}；场景阻断 ${d.scenario_report?.blocked?.length || 0}`); } catch(e) { show(false, e.message); } });
})();
