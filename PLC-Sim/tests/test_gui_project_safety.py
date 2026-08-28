from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException

from common import NodeDef
from gui import backend, server_routes


def test_online_download_is_fail_closed_without_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("PLCSIM_ALLOW_ONLINE_DEPLOY", raising=False)
    backend._STATE.toolkit = (
        object()
    )  # gate must reject before touching toolkit methods
    try:
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(
                backend.api_project_download(
                    backend.DownloadReq(strategy="online", confirm_online=True)
                )
            )
        assert exc_info.value.status_code == 403
    finally:
        backend._STATE.toolkit = None


def test_online_download_requires_explicit_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PLCSIM_ALLOW_ONLINE_DEPLOY", "true")
    backend._STATE.toolkit = object()
    try:
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(
                backend.api_project_download(
                    backend.DownloadReq(strategy="online", confirm_online=False)
                )
            )
        assert exc_info.value.status_code == 400
    finally:
        backend._STATE.toolkit = None


def test_online_download_cannot_bypass_ptlc_deploy_handshake(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证 GUI 不会绕过 pTLC 下载握手直接调用 CODESYS 在线下载。

    参数：``monkeypatch`` 用于开启旧环境开关，证明开关本身不再构成授权。
    返回：无；断言即使显式确认也以 501 关闭失败且不会触碰工具包。
    """

    monkeypatch.setenv("PLCSIM_ALLOW_ONLINE_DEPLOY", "true")
    backend._STATE.toolkit = object()
    try:
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(
                backend.api_project_download(
                    backend.DownloadReq(strategy="online", confirm_online=True)
                )
            )
        assert exc_info.value.status_code == 501
        assert "PlcProgramService" in str(exc_info.value.detail)
    finally:
        backend._STATE.toolkit = None


def test_gui_cannot_write_plc_owned_output_without_maintenance_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    node = NodeDef(
        "Rail_L2_State",
        "",
        "VARIABLE",
        "INT16",
        "ns=4;s=ptlc|Rail_L2_State",
        write_owner="plc",
    )
    monkeypatch.setattr(server_routes, "_require_running_server", lambda: None)
    backend._STATE.server_node_defs = [node]
    try:
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(
                backend.api_server_variable_write(
                    backend.ServerVariableWriteReq(node_id=node.node_id, value=10)
                )
            )
        assert exc_info.value.status_code == 409
        assert "只能由握手/行为代理写入" in str(exc_info.value.detail)
    finally:
        backend._STATE.server_node_defs = []
