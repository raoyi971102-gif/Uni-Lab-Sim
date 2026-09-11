"""Versioned, JSON-serialisable models used by the device-package feature."""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import Any

@dataclass
class SimulationSpecPatch:
    """The only shape an AI adapter is allowed to return."""
    evidence_ids: list[str] = field(default_factory=list)
    devices: list[dict[str, Any]] = field(default_factory=list)
    channels: list[dict[str, Any]] = field(default_factory=list)
    actions: list[dict[str, Any]] = field(default_factory=list)
    review_questions: list[dict[str, Any]] = field(default_factory=list)
    assumptions: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "SimulationSpecPatch":
        allowed = {"evidence_ids", "devices", "channels", "actions", "review_questions", "assumptions"}
        unknown = set(value) - allowed
        if unknown:
            raise ValueError(f"SimulationSpecPatch 含未知字段: {sorted(unknown)}")
        return cls(**{key: value.get(key, []) for key in allowed})

def merge_patch(evidence: dict[str, Any], patch: SimulationSpecPatch | dict[str, Any] | None = None) -> dict[str, Any]:
    """Merge reviewed data without allowing invented evidence references."""
    result = {"schema": "unilab.plc_simulation_spec/v1", "evidence": evidence, "devices": [], "channels": [], "actions": [], "review_questions": [], "assumptions": []}
    if patch is None:
        return result
    item = patch if isinstance(patch, SimulationSpecPatch) else SimulationSpecPatch.from_dict(patch)
    valid = {n["evidence_id"] for n in evidence.get("nodes", [])}
    invalid = set(item.evidence_ids) - valid
    if invalid:
        raise ValueError(f"补丁引用不存在的 evidence_id: {sorted(invalid)}")
    for key in ("devices", "channels", "actions", "review_questions", "assumptions"):
        result[key].extend(getattr(item, key))
    return result
