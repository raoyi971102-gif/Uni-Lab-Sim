"""Static scenario validation for generated package review."""
from __future__ import annotations
from typing import Any

def validate_scenarios(spec: dict[str, Any]) -> dict[str, Any]:
    actions = list(spec.get("actions", [])); scenarios=[]; blockers=[]
    for action in actions:
        name = str(action.get("action_id") or action.get("name") or "unknown")
        required = ["happy_path", "guard_failure", "reset_cancel", "timeout_fault"]
        for kind in required:
            present = bool(action.get("scenarios", {}).get(kind))
            scenarios.append({"action": name, "scenario": kind, "status": "pass" if present else "blocked"})
            if not present: blockers.append({"action": name, "scenario": kind, "reason": "缺少可验证步骤或证据"})
    return {"schema": "unilab.scenario_report/v1", "action_count": len(actions), "scenario_count": len(scenarios), "passed": sum(x["status"] == "pass" for x in scenarios), "blocked": blockers, "ready": not blockers and bool(actions)}
