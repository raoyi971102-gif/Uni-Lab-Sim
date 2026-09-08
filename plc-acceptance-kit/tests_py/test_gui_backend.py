from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from fastapi import HTTPException
from plc_acceptance import gui_backend
from plc_acceptance.run_manager import AcceptanceRunManager


class BinaryRequest:
    """为流式候选包接口提供最小异步请求体。"""

    def __init__(self, payload: bytes):
        """保存待上传的候选包字节。

        参数：``payload`` 是不可变候选包内容。
        返回：无；初始化当前对象。
        """

        self.payload = payload

    async def stream(self) -> AsyncIterator[bytes]:
        """产生一段候选包请求体。

        参数：无。
        返回：异步产生候选包字节。
        """

        yield self.payload


def test_gui_bootstrap_exposes_the_szlab_acceptance_baseline() -> None:
    """GUI 首页必须展示真实 SZLab 点表、协议和显式覆盖缺口。

    参数：无。
    返回：无；断言首页配置来自版本化验收包。
    """

    payload = gui_backend.bootstrap()

    assert payload["project_id"] == "szlab-poly-studio-plc"
    assert payload["node_count"] == 1591
    assert payload["l0_status"] == "PASSED"
    assert {item["requirement"] for item in payload["coverage_gaps"]} >= {
        "R6",
        "R12",
        "HS-C-002",
        "HS-D-001",
    }
    assert set(payload["environment_endpoints"]) == {
        "soft_plc",
        "bench",
        "fat_sat",
    }


def test_gui_exposes_independent_l3_and_l4_real_device_modes() -> None:
    """GUI 必须把 L3 台架和 L4 FAT/SAT 作为独立真机路径呈现。"""

    html = gui_backend.index()

    assert 'value="bench"' in html
    assert 'value="fat_sat"' in html
    assert "监护/见证人" in html
    assert "物料或批次标识" in html


def test_gui_streams_and_hashes_an_immutable_plc_artifact(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """候选包上传必须保留内容哈希并拒绝依赖浏览器本地路径。

    参数：``tmp_path`` 是隔离存储；``monkeypatch`` 替换 GUI 候选包目录。
    返回：无；断言二进制内容、规范化名称和 SHA-256 身份。
    """

    artifact_root = tmp_path / "artifacts"
    monkeypatch.setattr(gui_backend, "ARTIFACT_DIR", artifact_root)
    payload = asyncio.run(
        gui_backend.upload_artifact(
            BinaryRequest(b"immutable-plc-candidate"),  # type: ignore[arg-type]
            filename="SZLab PLC candidate.zip",
        )
    )

    assert payload["name"] == "SZLab-PLC-candidate.zip"
    assert len(payload["sha256"]) == 64
    assert (artifact_root / payload["artifact_id"]).read_bytes() == (
        b"immutable-plc-candidate"
    )


def test_gui_blocks_soft_plc_without_version_and_safety_evidence() -> None:
    """软 PLC 模式不得在缺少候选包或安全确认时开始运行。

    参数：无。
    返回：无；断言 API 在后台线程启动前返回可恢复错误。
    """

    request = gui_backend.RunRequest(
        mode="soft_plc",
        endpoint="opc.tcp://127.0.0.1:4840/",
        namespace_uri="urn:xuse:sim",
        confirm_safe_test_mode=False,
    )

    with pytest.raises(HTTPException, match="候选包"):
        gui_backend.start_run(request)


@pytest.mark.parametrize(
    "endpoint",
    ["opc.tcp://", "opc.tcp://192.168.1.10", "http://192.168.1.10:4840"],
)
def test_gui_rejects_incomplete_or_non_opcua_endpoints(endpoint: str) -> None:
    """外部模式必须在上传候选包前拒绝缺主机、端口或协议的地址。"""

    request = gui_backend.RunRequest(
        mode="soft_plc",
        endpoint=endpoint,
        namespace_uri="urn:xuse:sim",
    )

    with pytest.raises(HTTPException, match="Endpoint"):
        gui_backend.start_run(request)


@pytest.mark.parametrize("mode", ["bench", "fat_sat"])
def test_gui_blocks_real_plc_without_on_site_evidence(mode: str) -> None:
    """真机运行缺少监护人或现场位置时必须在后台任务启动前阻塞。"""

    request = gui_backend.RunRequest(
        mode=mode,
        endpoint="opc.tcp://192.168.1.10:4840/",
        namespace_uri="urn:xuse:sim",
        confirm_safe_test_mode=True,
        artifact_id="candidate.zip",
    )

    with pytest.raises(HTTPException, match="监护/见证人"):
        gui_backend.start_run(request)


def test_gui_passes_fat_sat_evidence_to_the_run_manager(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """L4 请求必须把现场、见证和物料身份原样交给统一运行器。"""

    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir()
    (artifact_root / "candidate.zip").write_bytes(b"candidate")
    captured: dict[str, object] = {}

    class FakeManager:
        def start(self, **kwargs):  # type: ignore[no-untyped-def]
            captured.update(kwargs)
            return {"state": "RUNNING"}

    monkeypatch.setattr(gui_backend, "ARTIFACT_DIR", artifact_root)
    monkeypatch.setattr(gui_backend, "RUN_MANAGER", FakeManager())

    response = gui_backend.start_run(
        gui_backend.RunRequest(
            mode="fat_sat",
            endpoint="opc.tcp://192.168.1.20:4840/",
            namespace_uri="urn:szlab:plc",
            confirm_safe_test_mode=True,
            artifact_id="candidate.zip",
            supervisor="供应商张工",
            test_location="SZLab FAT 现场",
            material_reference="批次 B-20260903",
        )
    )

    assert response == {"state": "RUNNING"}
    assert captured["mode"] == "fat_sat"
    assert captured["namespace_uri"] == "urn:szlab:plc"
    assert captured["evidence_metadata"] == {
        "supervisor": "供应商张工",
        "test_location": "SZLab FAT 现场",
        "material_reference": "批次 B-20260903",
    }


def test_report_directory_rejects_path_traversal(tmp_path: Path) -> None:
    """报告下载接口只能读取报告根目录中的稳定运行 ID。

    参数：``tmp_path`` 是隔离报告根目录。
    返回：无；断言路径穿越不会解析为文件系统路径。
    """

    manager = AcceptanceRunManager(output_root=tmp_path)

    try:
        manager.report_dir("../outside")
    except FileNotFoundError:
        pass
    else:
        raise AssertionError("路径穿越必须被拒绝")
