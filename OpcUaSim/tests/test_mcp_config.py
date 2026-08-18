from __future__ import annotations

import json
from pathlib import Path

from ino_mcp import config


def _write_mcp_config(path: Path, *, bundle: Path, codesys: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "codesys_local": {
                        "command": "node",
                        "args": [
                            str(bundle),
                            "--codesys-path",
                            str(codesys),
                        ],
                    }
                }
            }
        ),
        encoding="utf-8",
    )


def test_stale_user_bundle_falls_back_to_repository_bundle(
    monkeypatch, tmp_path: Path
) -> None:
    project_root = tmp_path / "OpcUaSim"
    local_bundle = project_root / "vendor" / "inoproshop-mcp" / "bundle.min.js"
    local_bundle.parent.mkdir(parents=True)
    local_bundle.write_text("// bundled", encoding="utf-8")
    local_launcher = local_bundle.with_name("persistent-launcher.js")
    local_launcher.write_text("// persistent", encoding="utf-8")

    config_file = tmp_path / "mcp.json"
    installed_codesys = tmp_path / "InoProShop.exe"
    installed_codesys.touch()
    _write_mcp_config(
        config_file,
        bundle=tmp_path / "deleted-checkout" / "bundle.min.js",
        codesys=installed_codesys,
    )

    monkeypatch.setattr(config, "PROJECT_ROOT", project_root)
    monkeypatch.setattr(config, "_mcp_config_files", lambda: (config_file,))
    for name in (
        "OPCUASIM_MCP_BUNDLE",
        "OPCUASIM_INOPROSHOP_EXE",
        "OPCUASIM_INOPROSHOP_PROFILE",
        "OPCUASIM_MCP_WORKSPACE",
        "OPCUASIM_NODE",
    ):
        monkeypatch.delenv(name, raising=False)

    resolved = config.resolve_mcp_config()

    assert resolved["bundle_js"] == str(local_launcher.resolve())
    assert resolved["codesys_path"] == str(installed_codesys.resolve())


def test_valid_user_bundle_still_overrides_repository_bundle(
    monkeypatch, tmp_path: Path
) -> None:
    project_root = tmp_path / "OpcUaSim"
    local_bundle = project_root / "vendor" / "inoproshop-mcp" / "bundle.min.js"
    local_bundle.parent.mkdir(parents=True)
    local_bundle.write_text("// bundled", encoding="utf-8")
    local_bundle.with_name("persistent-launcher.js").write_text(
        "// persistent", encoding="utf-8"
    )
    configured_bundle = tmp_path / "custom" / "bundle.min.js"
    configured_bundle.parent.mkdir()
    configured_bundle.write_text("// configured", encoding="utf-8")
    installed_codesys = tmp_path / "InoProShop.exe"
    installed_codesys.touch()
    config_file = tmp_path / "mcp.json"
    _write_mcp_config(
        config_file,
        bundle=configured_bundle,
        codesys=installed_codesys,
    )

    monkeypatch.setattr(config, "PROJECT_ROOT", project_root)
    monkeypatch.setattr(config, "_mcp_config_files", lambda: (config_file,))
    monkeypatch.delenv("OPCUASIM_MCP_BUNDLE", raising=False)
    monkeypatch.delenv("OPCUASIM_INOPROSHOP_EXE", raising=False)

    resolved = config.resolve_mcp_config()

    assert resolved["bundle_js"] == str(configured_bundle.resolve())
