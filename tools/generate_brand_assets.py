#!/usr/bin/env python3
"""Generate deterministic Uni-Lab-Sim brand assets from the source PNG.

The source logo is intentionally kept untouched.  ImageMagick is used only
for transparent cropping/resizing and the ICNS container is assembled from
the resulting PNG representations, so every platform receives the same mark.
"""

from __future__ import annotations

import argparse
import os
import shutil
import struct
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "assets" / "brand" / "uni-lab-sim-logo.png"


def image_dimensions(path: Path) -> tuple[int, int]:
    with path.open("rb") as handle:
        if handle.read(8) != b"\x89PNG\r\n\x1a\n":
            raise ValueError(f"not a PNG: {path}")
        if struct.unpack(">I", handle.read(4))[0] != 13 or handle.read(4) != b"IHDR":
            raise ValueError(f"PNG has no IHDR: {path}")
        width, height = struct.unpack(">II", handle.read(8))
    return width, height


def convert(executable: str, *arguments: str) -> None:
    command = [executable, *arguments]
    subprocess.run(command, check=True)


def write_icns(icon_png: Path, destination: Path, convert_executable: str) -> None:
    entries: list[tuple[bytes, bytes]] = []
    for type_code, size in ((b"ic07", 128), (b"ic08", 256), (b"ic09", 512), (b"ic10", 1024)):
        png_path = destination.with_suffix(f".{size}.png")
        convert(
            convert_executable,
            str(icon_png),
            "-resize",
            f"{size}x{size}",
            "PNG32:" + str(png_path),
        )
        payload = png_path.read_bytes()
        entries.append((type_code, payload))
        png_path.unlink()

    body = bytearray()
    for type_code, payload in entries:
        body += type_code
        body += struct.pack(">I", len(payload) + 8)
        body += payload
    destination.write_bytes(b"icns" + struct.pack(">I", len(body) + 8) + body)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=SOURCE)
    args = parser.parse_args()
    source = args.source.resolve()
    if not source.is_file():
        raise SystemExit(f"missing source logo: {source}")

    convert_executable = shutil.which("magick")
    if convert_executable is None and os.name != "nt":
        # ``convert`` is ImageMagick on POSIX; on Windows that name belongs
        # to the built-in filesystem conversion utility, so never use it as a
        # fallback there.
        convert_executable = shutil.which("convert")
    if convert_executable is None:
        raise SystemExit("ImageMagick (magick or convert) is required to regenerate brand assets")

    root_brand = ROOT / "assets" / "brand"
    plc_static = ROOT / "PLC-Sim" / "gui" / "static"
    modbus_static = ROOT / "Modbus-Sim" / "gui" / "static"
    plc_packaging = ROOT / "PLC-Sim" / "packaging" / "assets"
    modbus_packaging = ROOT / "Modbus-Sim" / "packaging" / "assets"
    for directory in (root_brand, plc_static, modbus_static, plc_packaging, modbus_packaging):
        directory.mkdir(parents=True, exist_ok=True)

    width, height = image_dimensions(source)
    # The supplied lockup has a transparent gap between the illustration and
    # wordmark.  Crop only the illustration for app/file icons; keep the full
    # source for README and splash artwork.
    illustration_height = round(height * 0.686)
    icon = root_brand / "uni-lab-sim-icon.png"
    convert(
        convert_executable,
        str(source),
        "-crop",
        f"{width}x{illustration_height}+0+0",
        "+repage",
        "-trim",
        "+repage",
        "-background",
        "none",
        "-gravity",
        "center",
        "-extent",
        "1024x1024",
        "-resize",
        "896x896",
        "-gravity",
        "center",
        "-extent",
        "1024x1024",
        "PNG32:" + str(icon),
    )

    logo_targets = (plc_static / "uni-lab-sim-logo.png", modbus_static / "uni-lab-sim-logo.png")
    for target in logo_targets:
        convert(convert_executable, str(source), "-resize", "640x640", "PNG32:" + str(target))

    # Web assets are deliberately small and stable so they do not delay GUI
    # startup or create layout shifts while the browser loads the shell.
    for static_directory in (plc_static, modbus_static):
        convert(convert_executable, str(icon), "-resize", "192x192", "PNG32:" + str(static_directory / "uni-lab-sim-icon.png"))
        convert(convert_executable, str(icon), "-resize", "32x32", "PNG32:" + str(static_directory / "favicon.png"))
        convert(convert_executable, str(icon), "-resize", "180x180", "PNG32:" + str(static_directory / "apple-touch-icon.png"))

    # Native package icon representations.
    convert(convert_executable, str(icon), "-resize", "512x512", "PNG32:" + str(plc_packaging / "uni-lab-sim.png"))
    shutil.copyfile(plc_packaging / "uni-lab-sim.png", modbus_packaging / "uni-lab-sim.png")
    for packaging_directory in (plc_packaging, modbus_packaging):
        convert(
            convert_executable,
            str(icon),
            "-define",
            "icon:auto-resize=256,128,64,48,32,16",
            str(packaging_directory / "uni-lab-sim.ico"),
        )
        write_icns(icon, packaging_directory / "uni-lab-sim.icns", convert_executable)

    for static_directory in (plc_static, modbus_static):
        shutil.copyfile(plc_packaging / "uni-lab-sim.ico", static_directory / "favicon.ico")

    print(f"generated deterministic brand assets from {source}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
