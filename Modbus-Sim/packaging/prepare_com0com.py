"""Download, verify, and stage the pinned com0com redistribution payload."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import tempfile
import urllib.request
import zipfile
from pathlib import Path

VERSION = "3.0.0.0"
SIGNED_ARCHIVE_URL = (
    "https://sourceforge.net/projects/com0com/files/com0com/3.0.0.0/"
    "com0com-3.0.0.0-i386-and-x64-signed.zip/download"
)
SOURCE_ARCHIVE_URL = (
    "https://sourceforge.net/projects/com0com/files/com0com/3.0.0.0/"
    "com0com-3.0.0.0.zip/download"
)
SIGNED_ARCHIVE_SHA256 = (
    "6e5d4359865277430d4ae88c73fb7e648a0ed8e81aea5002478179cfcb0bb0e1"
)
SOURCE_ARCHIVE_SHA256 = (
    "6751e911f73980b23cc878a456eb99d1dc6d0603c11c1d3ced109abe1c556380"
)
INSTALLER_NAME = "Setup_com0com_v3.0.0.0_W7_x64_signed.exe"
INSTALLER_SHA256 = "26486b28604b49a9008c54feb11b9ece0008a8287ee5caf0bcf2a62f4317128f"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify(path: Path, expected: str, label: str) -> None:
    actual = sha256(path)
    if actual != expected.lower():
        raise ValueError(f"{label} SHA-256 mismatch: expected {expected}, got {actual}")


def _download(url: str, destination: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "Modbus-Sim build"})
    with (
        urllib.request.urlopen(request, timeout=120) as response,
        destination.open("wb") as target,
    ):
        shutil.copyfileobj(response, target)


def _member_by_basename(archive: zipfile.ZipFile, basename: str) -> str:
    matches = [
        name
        for name in archive.namelist()
        if Path(name).name.lower() == basename.lower()
    ]
    if len(matches) != 1:
        raise ValueError(
            f"Expected exactly one {basename!r} in archive, found {len(matches)}"
        )
    return matches[0]


def prepare(
    output_dir: Path,
    *,
    signed_archive: Path | None = None,
    source_archive: Path | None = None,
    signed_sha256: str = SIGNED_ARCHIVE_SHA256,
    source_sha256: str = SOURCE_ARCHIVE_SHA256,
    installer_sha256: str = INSTALLER_SHA256,
) -> Path:
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="modbus-sim-com0com-") as temporary:
        temporary_dir = Path(temporary)
        signed = signed_archive or temporary_dir / "com0com-signed.zip"
        source = source_archive or temporary_dir / "com0com-source.zip"
        if signed_archive is None:
            _download(SIGNED_ARCHIVE_URL, signed)
        if source_archive is None:
            _download(SOURCE_ARCHIVE_URL, source)

        verify(signed, signed_sha256, "com0com signed binary archive")
        verify(source, source_sha256, "com0com source archive")
        with zipfile.ZipFile(signed) as archive:
            installer_bytes = archive.read(_member_by_basename(archive, INSTALLER_NAME))
        installer_path = output_dir / INSTALLER_NAME
        installer_path.write_bytes(installer_bytes)
        verify(installer_path, installer_sha256, "com0com x64 installer")

        staged_source = output_dir / f"com0com-{VERSION}-source.zip"
        shutil.copyfile(source, staged_source)
        with zipfile.ZipFile(source) as archive:
            for original, staged in (
                ("license.txt", "LICENSE.txt"),
                ("ReadMe.txt", "README.txt"),
            ):
                (output_dir / staged).write_bytes(
                    archive.read(_member_by_basename(archive, original))
                )

    manifest = {
        "component": "com0com",
        "version": VERSION,
        "license": "GPL-2.0-only",
        "official_project": "https://sourceforge.net/projects/com0com/",
        "signed_archive": {"url": SIGNED_ARCHIVE_URL, "sha256": signed_sha256},
        "source_archive": {"url": SOURCE_ARCHIVE_URL, "sha256": source_sha256},
        "installer": {"filename": INSTALLER_NAME, "sha256": installer_sha256},
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return output_dir


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output", type=Path, default=Path(__file__).parent / "vendor" / "com0com"
    )
    parser.add_argument("--signed-archive", type=Path)
    parser.add_argument("--source-archive", type=Path)
    parser.add_argument("--signed-sha256", default=SIGNED_ARCHIVE_SHA256)
    parser.add_argument("--source-sha256", default=SOURCE_ARCHIVE_SHA256)
    parser.add_argument("--installer-sha256", default=INSTALLER_SHA256)
    args = parser.parse_args()
    prepared = prepare(
        args.output,
        signed_archive=args.signed_archive,
        source_archive=args.source_archive,
        signed_sha256=args.signed_sha256,
        source_sha256=args.source_sha256,
        installer_sha256=args.installer_sha256,
    )
    print(f"Prepared verified com0com {VERSION} payload at {prepared}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
