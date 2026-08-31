import hashlib
import importlib.util
import json
import zipfile
from pathlib import Path

import pytest

PACKAGING_DIR = Path(__file__).resolve().parents[1] / "packaging"


def load_prepare_module():
    spec = importlib.util.spec_from_file_location(
        "prepare_com0com", PACKAGING_DIR / "prepare_com0com.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_prepare_com0com_stages_verified_binary_source_and_license(tmp_path):
    module = load_prepare_module()
    installer = b"MZ fake signed com0com installer"
    signed = tmp_path / "signed.zip"
    source = tmp_path / "source.zip"
    with zipfile.ZipFile(signed, "w") as archive:
        archive.writestr(module.INSTALLER_NAME, installer)
        archive.writestr("unused-x86.exe", b"x86")
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("com0com/license.txt", "GPL version 2 fixture")
        archive.writestr("com0com/ReadMe.txt", "source fixture")
        archive.writestr("com0com/sys/com0com.c", "/* fixture */")

    output = module.prepare(
        tmp_path / "staged",
        signed_archive=signed,
        source_archive=source,
        signed_sha256=digest(signed),
        source_sha256=digest(source),
        installer_sha256=hashlib.sha256(installer).hexdigest(),
    )

    assert (output / module.INSTALLER_NAME).read_bytes() == installer
    assert (output / "com0com-3.0.0.0-source.zip").read_bytes() == source.read_bytes()
    assert "GPL version 2" in (output / "LICENSE.txt").read_text(encoding="utf-8")
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["license"] == "GPL-2.0-only"
    assert manifest["installer"]["sha256"] == hashlib.sha256(installer).hexdigest()


def test_prepare_com0com_rejects_archive_hash_mismatch(tmp_path):
    module = load_prepare_module()
    signed = tmp_path / "signed.zip"
    source = tmp_path / "source.zip"
    signed.write_bytes(b"not trusted")
    source.write_bytes(b"not trusted")
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        module.prepare(
            tmp_path / "staged",
            signed_archive=signed,
            source_archive=source,
            signed_sha256="0" * 64,
            source_sha256="0" * 64,
        )


def test_windows_installer_keeps_driver_optional_and_uses_uac():
    definition = (PACKAGING_DIR / "windows-installer.iss").read_text(encoding="utf-8")
    assert 'Name: "com0com"' in definition
    assert "Flags: unchecked" in definition
    assert 'Verb: "runas"' in definition
    assert 'Parameters: "/S"' in definition
    assert "PrivilegesRequired=lowest" in definition
    assert "vendor\\com0com\\*" in definition
    assert "THIRD_PARTY_NOTICES.md" in definition
