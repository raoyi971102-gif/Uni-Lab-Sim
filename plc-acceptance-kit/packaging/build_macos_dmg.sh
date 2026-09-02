#!/usr/bin/env bash
set -euo pipefail

VERSION="${1:?usage: build_macos_dmg.sh VERSION ARCH}"
ARCH="${2:?usage: build_macos_dmg.sh VERSION ARCH}"
APP_PATH="dist/SZLab-PLC-Acceptance.app"
OUTPUT_DIR="artifacts"

if [[ ! -d "$APP_PATH" ]]; then
  echo "Missing PyInstaller application: $APP_PATH" >&2
  exit 1
fi

mkdir -p "$OUTPUT_DIR"
STAGING_DIR="$(mktemp -d "${RUNNER_TEMP:-/tmp}/szlab-plc-acceptance-dmg.XXXXXX")"
trap 'rm -rf "$STAGING_DIR"' EXIT

codesign --force --deep --sign - "$APP_PATH"
cp -R "$APP_PATH" "$STAGING_DIR/SZLab PLC Acceptance.app"
ln -s /Applications "$STAGING_DIR/Applications"

hdiutil create \
  -volname "SZLab PLC Acceptance" \
  -srcfolder "$STAGING_DIR" \
  -ov \
  -format UDZO \
  "$OUTPUT_DIR/SZLab-PLC-Acceptance-macOS-${ARCH}-v${VERSION}.dmg"
