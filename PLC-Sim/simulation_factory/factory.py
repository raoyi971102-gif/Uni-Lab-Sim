"""Orchestrator for the fourth PLC-Sim feature: device-package generation."""
from __future__ import annotations
import json
from pathlib import Path
from .table_package import inspect_table, build_package
from .models import merge_patch
from .os_package import generate_os_package
from .scenarios import validate_scenarios

class SimulationFactory:
    def inspect(self, source): return inspect_table(source)
    def infer(self, source, patch=None): return merge_patch(inspect_table(source), patch)
    def build(self, source, destination, patch=None):
        result = build_package(source, destination); spec = self.infer(source, patch); spec["package_id"] = result["manifest"]["package_id"]
        spec_path = Path(destination) / "simulation-spec.json"; spec_path.write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")
        os_root = generate_os_package(spec, Path(destination) / "os-package", self._plc_source()); report = validate_scenarios(spec)
        (Path(destination) / "scenario-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        result.update({"spec": str(spec_path), "os_package": str(os_root), "scenario_report": report}); return result
    def validate(self, spec_path): return validate_scenarios(json.loads(Path(spec_path).read_text(encoding="utf-8")))
    @staticmethod
    def _plc_source():
        candidate = Path(r"D:\DPLC\Uni-Lab-SZLab\szlab_poly_studio\devices\szlab_poly_plc\device.py")
        return candidate if candidate.is_file() else None
