from __future__ import annotations

from ino_mcp.client import McpError
from ino_mcp.toolkit import DownloadStrategy, InoToolkit


class FakeMcp:
    def call_tool(self, name, args):
        if name == "save_project":
            return "saved"
        if name == "compile_project":
            return "Compiled 0 errors, 0 warnings"
        if name == "probe_api":
            raise McpError("online unavailable")
        raise AssertionError(name)


def test_online_probe_failure_is_never_reported_as_deployed() -> None:
    toolkit = InoToolkit(FakeMcp(), "demo.project")
    report = toolkit.download_program(DownloadStrategy.ONLINE_IRONPYTHON)
    assert report["compile"]["ok"] is True
    assert "error" in report
    assert "未确认成功" in report["error"]

