"""Small data-driven handshake runtime used by generated packages.

It intentionally accepts only the generated ``handshakes.yaml`` contract and
keeps all devices in one process/session.  Physical behavior remains a later
reviewed DSL concern.
"""
from __future__ import annotations
from pathlib import Path
from typing import Any
try:
    from .common import load_yaml
except ImportError:
    from common import load_yaml

class GenericPackageRuntime:
    def __init__(self, package_path: str | Path):
        root=Path(package_path); self.package_path=root; self.config=load_yaml(str(root/"handshakes.yaml")); self.channels={c["channel_id"]:c for c in self.config.get("channels", [])}; self.state={k:{"phase":"idle","last_error":None} for k in self.channels}
    def start(self, channel_id: str, parameters: dict[str, Any] | None = None) -> dict[str, Any]:
        if channel_id not in self.channels: return {"accepted":False,"error":"UNKNOWN_CHANNEL"}
        current=self.state[channel_id]
        if current["phase"] == "running": return {"accepted":False,"error":"BUSY"}
        current.update({"phase":"running","parameters":dict(parameters or {})}); return {"accepted":True,"phase":"running"}
    def complete(self, channel_id: str, error: str | None = None) -> dict[str, Any]:
        if channel_id not in self.channels: return {"completed":False,"error":"UNKNOWN_CHANNEL"}
        self.state[channel_id].update({"phase":"error" if error else "completed","last_error":error}); return {"completed":not bool(error),"error":error}
    def reset(self, channel_id: str) -> dict[str, Any]:
        if channel_id not in self.channels: return {"reset":False,"error":"UNKNOWN_CHANNEL"}
        self.state[channel_id]={"phase":"idle","last_error":None}; return {"reset":True}
    def snapshot(self) -> dict[str, Any]: return {"package":str(self.package_path),"channels":self.state}
