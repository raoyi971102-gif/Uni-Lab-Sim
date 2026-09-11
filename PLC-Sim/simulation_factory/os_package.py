"""Generate an editable Uni-Lab-OS package skeleton from a simulation spec."""
from __future__ import annotations
import re
from pathlib import Path
from typing import Any

def _id(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z_]+", "_", value).strip("_").lower() or "generated_device"

def generate_os_package(spec: dict[str, Any], destination: str | Path, plc_source: str | Path | None = None) -> Path:
    root = Path(destination).expanduser().resolve(); root.mkdir(parents=True, exist_ok=True)
    package_id = _id(str(spec.get("package_id") or spec.get("devices", [{}])[0].get("device_id", "generated_device")))
    src = root / package_id; src.mkdir(exist_ok=True)
    (root / "pyproject.toml").write_text(f'''[build-system]\nrequires = ["setuptools>=68", "wheel"]\nbuild-backend = "setuptools.build_meta"\n\n[project]\nname = "{package_id}"\nversion = "0.1.0"\nrequires-python = ">=3.11"\ndependencies = ["unilabos>=0.11.3"]\n\n[tool.setuptools.packages.find]\ninclude = ["{package_id}*"]\n''', encoding="utf-8")
    (src / "__init__.py").write_text("", encoding="utf-8")
    (src / f"{package_id}.py").write_text(f'''from typing import Any, Dict\nfrom unilabos.registry.decorators import device, action, topic_config\n\n@device(id="{package_id}", category=["generated"], description="PLC-Sim generated device package", displayname="{package_id}")\nclass {''.join(part.title() for part in package_id.split('_'))}:\n    """业务设备层；所有 OPC UA 读写必须经由同包 plc.py 的 PLC driver。"""\n    def __init__(self, device_id: str | None = None, config: Dict[str, Any] | None = None, plc: Any = None, **kwargs):\n        self.device_id = device_id or "{package_id}"\n        self.config = config or {{}}\n        self.plc = plc\n        self.data: Dict[str, Any] = {{"status": "idle", "device_ready": False}}\n\n    @action(description="复位")\n    def reset(self) -> Dict[str, Any]:\n        if self.plc and hasattr(self.plc, "reset"): return self.plc.reset()\n        self.data["status"] = "idle"\n        return {{"success": True}}\n\n    @property\n    @topic_config()\n    def status(self) -> str:\n        if self.plc and hasattr(self.plc, "get_node_value"): return self.plc.get_node_value("Status") or ""\n        return self.data.get("status", "idle")\n''', encoding="utf-8")
    source = Path(plc_source).expanduser() if plc_source else None
    if source and source.is_file():
        (src / "plc.py").write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
        driver_note = "plc.py copied from the SZLab PLC communication driver. Review its device id and node CSV before publishing."
    else:
        (src / "plc.py").write_text('''"""Generated OPC UA communication driver compatible with SZLab PLC.py."""\nfrom typing import Any, Dict\nfrom unilabos.registry.decorators import device, action\nfrom base_opcua_client import OpcUaClientWithSubscription\n\n@device(id="PACKAGE_ID_plc", category=["generated"], description="Generated PLC communication driver", displayname="Generated PLC")\nclass GeneratedPLC(OpcUaClientWithSubscription):\n    def __init__(self, url: str, csv_path: str | None = None, **kwargs):\n        super().__init__(url=url, **kwargs)\n        if csv_path: self.load_nodes_from_csv(csv_path)\n\n    @action(description="复位")\n    def reset(self) -> Dict[str, Any]:\n        return {"success": True}\n'''.replace("PACKAGE_ID", package_id), encoding="utf-8")
        driver_note = "Generated fallback driver; configure the SZLab PLC.py source to use the production communication driver."
    (root / "PLC_DRIVER.md").write_text(driver_note + "\n", encoding="utf-8")
    (src / "nodes.json").write_text(__import__("json").dumps(spec.get("evidence", {}).get("nodes", []), ensure_ascii=False, indent=2), encoding="utf-8")
    return root
