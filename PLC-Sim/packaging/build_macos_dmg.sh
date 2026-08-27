#!/usr/bin/env bash
set -euo pipefail

VERSION="${1:?usage: build_macos_dmg.sh VERSION ARCH}"
ARCH="${2:?usage: build_macos_dmg.sh VERSION ARCH}"
APP_PATH="dist/PLC-Sim.app"
OUTPUT_DIR="artifacts"

if [[ ! -d "$APP_PATH" ]]; then
  echo "Missing PyInstaller application: $APP_PATH" >&2
  exit 1
fi

mkdir -p "$OUTPUT_DIR"
STAGING_DIR="$(mktemp -d "${RUNNER_TEMP:-/tmp}/plc-sim-dmg.XXXXXX")"

codesign --force --deep --sign - "$APP_PATH"
cp -R "$APP_PATH" "$STAGING_DIR/PLC-Sim.app"
ln -s /Applications "$STAGING_DIR/Applications"

hdiutil create \
  -volname "PLC-Sim" \
  -srcfolder "$STAGING_DIR" \
  -ov \
  -format UDZO \
  "$OUTPUT_DIR/PLC-Sim-macOS-${ARCH}-v${VERSION}.dmg"
