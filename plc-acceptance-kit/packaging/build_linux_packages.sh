#!/usr/bin/env bash
set -euo pipefail

VERSION="${1:?usage: build_linux_packages.sh VERSION ARCH}"
ARCH="${2:-x64}"

if [[ ! "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+([.+~][0-9A-Za-z.+~-]+)?$ ]]; then
  echo "Unsupported release version: $VERSION" >&2
  exit 1
fi
if [[ "$ARCH" != "x64" ]]; then
  echo "Unsupported Linux architecture: $ARCH" >&2
  exit 1
fi

APP_DIR="${PLC_ACCEPTANCE_DIST_DIR:-dist/SZLab-PLC-Acceptance}"
APP_BINARY="$APP_DIR/SZLab-PLC-Acceptance"
OUTPUT_DIR="${PLC_ACCEPTANCE_ARTIFACT_DIR:-artifacts}"
BUNDLE_NAME="SZLab-PLC-Acceptance-Linux-${ARCH}-v${VERSION}"

if [[ ! -x "$APP_BINARY" ]]; then
  echo "Missing PyInstaller application: $APP_BINARY" >&2
  exit 1
fi
command -v dpkg-deb >/dev/null 2>&1 || {
  echo "dpkg-deb is required to build the Linux installer" >&2
  exit 1
}

mkdir -p "$OUTPUT_DIR"
STAGING_DIR="$(mktemp -d "${RUNNER_TEMP:-/tmp}/szlab-plc-acceptance.XXXXXX")"
trap 'rm -rf "$STAGING_DIR"' EXIT

PORTABLE_ROOT="$STAGING_DIR/$BUNDLE_NAME"
mkdir -p "$PORTABLE_ROOT"
cp -a "$APP_DIR/." "$PORTABLE_ROOT/"
cat > "$PORTABLE_ROOT/README.txt" <<EOF
SZLab PLC 自动验收 ${VERSION} Linux ${ARCH}

无需预装 Python。解压后双击或运行：
  ./${BUNDLE_NAME}/SZLab-PLC-Acceptance

程序自动打开本地 GUI，报告保存在 XDG_DATA_HOME/szlab-plc-acceptance；
未设置 XDG_DATA_HOME 时使用 ~/.local/share/szlab-plc-acceptance。
EOF
tar -C "$STAGING_DIR" -czf "$OUTPUT_DIR/${BUNDLE_NAME}.tar.gz" "$BUNDLE_NAME"

DEB_ROOT="$STAGING_DIR/deb-root"
install -d \
  "$DEB_ROOT/DEBIAN" \
  "$DEB_ROOT/opt/SZLab-PLC-Acceptance" \
  "$DEB_ROOT/usr/bin" \
  "$DEB_ROOT/usr/share/applications"
cp -a "$APP_DIR/." "$DEB_ROOT/opt/SZLab-PLC-Acceptance/"
ln -s /opt/SZLab-PLC-Acceptance/SZLab-PLC-Acceptance "$DEB_ROOT/usr/bin/szlab-plc-acceptance"

INSTALLED_SIZE="$(du -sk "$DEB_ROOT/opt/SZLab-PLC-Acceptance" | cut -f1)"
cat > "$DEB_ROOT/DEBIAN/control" <<EOF
Package: szlab-plc-acceptance
Version: ${VERSION}
Section: science
Priority: optional
Architecture: amd64
Installed-Size: ${INSTALLED_SIZE}
Depends: libc6 (>= 2.35)
Maintainer: Uni-Lab <raoyi971102@gmail.com>
Description: SZLab PLC protocol and handshake acceptance GUI
 Bundles Python, PLC-Sim, OPC UA acceptance cases, reports, and a local Web GUI.
EOF

cat > "$DEB_ROOT/usr/share/applications/szlab-plc-acceptance.desktop" <<'EOF'
[Desktop Entry]
Type=Application
Name=SZLab PLC 自动验收
Comment=运行 SZLab PLC 协议、握手与复位验收
Exec=/opt/SZLab-PLC-Acceptance/SZLab-PLC-Acceptance
Terminal=false
Categories=Development;Science;
EOF

dpkg-deb --root-owner-group --build \
  "$DEB_ROOT" \
  "$OUTPUT_DIR/${BUNDLE_NAME}.deb"
