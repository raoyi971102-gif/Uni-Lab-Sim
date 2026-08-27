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

APP_DIR="${PLCSIM_DIST_DIR:-dist/PLC-Sim}"
APP_BINARY="$APP_DIR/PLC-Sim"
OUTPUT_DIR="${PLCSIM_ARTIFACT_DIR:-artifacts}"
BUNDLE_NAME="PLC-Sim-Linux-${ARCH}-v${VERSION}"
DEBIAN_ARCH="amd64"

if [[ ! -x "$APP_BINARY" ]]; then
  echo "Missing PyInstaller application: $APP_BINARY" >&2
  exit 1
fi
command -v dpkg-deb >/dev/null 2>&1 || {
  echo "dpkg-deb is required to build the Linux installer" >&2
  exit 1
}

mkdir -p "$OUTPUT_DIR"
STAGING_DIR="$(mktemp -d "${RUNNER_TEMP:-/tmp}/plc-sim-linux.XXXXXX")"
trap 'rm -rf "$STAGING_DIR"' EXIT

# 便携包保留 PyInstaller onedir 布局，解压后无需 root 权限即可运行。
PORTABLE_ROOT="$STAGING_DIR/$BUNDLE_NAME"
mkdir -p "$PORTABLE_ROOT"
cp -a "$APP_DIR/." "$PORTABLE_ROOT/"
cat > "$PORTABLE_ROOT/README.txt" <<EOF
PLC-Sim ${VERSION} Linux ${ARCH}

无需预装 Python。解压后运行：
  ./${BUNDLE_NAME}/PLC-Sim

显式启动 Web GUI：
  ./${BUNDLE_NAME}/PLC-Sim gui

运行状态写入 XDG_DATA_HOME/plc-sim；未设置时使用 ~/.local/share/plc-sim。
EOF
tar -C "$STAGING_DIR" -czf "$OUTPUT_DIR/${BUNDLE_NAME}.tar.gz" "$BUNDLE_NAME"

# DEB 把冻结应用安装到 /opt，并提供命令行和桌面入口。
DEB_ROOT="$STAGING_DIR/deb-root"
install -d \
  "$DEB_ROOT/DEBIAN" \
  "$DEB_ROOT/opt/PLC-Sim" \
  "$DEB_ROOT/usr/bin" \
  "$DEB_ROOT/usr/share/applications"
cp -a "$APP_DIR/." "$DEB_ROOT/opt/PLC-Sim/"
ln -s /opt/PLC-Sim/PLC-Sim "$DEB_ROOT/usr/bin/plc-sim"

INSTALLED_SIZE="$(du -sk "$DEB_ROOT/opt/PLC-Sim" | cut -f1)"
cat > "$DEB_ROOT/DEBIAN/control" <<EOF
Package: unilab-plc-sim
Version: ${VERSION}
Section: science
Priority: optional
Architecture: ${DEBIAN_ARCH}
Installed-Size: ${INSTALLED_SIZE}
Depends: libc6 (>= 2.35)
Maintainer: Uni-Lab <raoyi971102@gmail.com>
Description: CSV-driven OPC UA simulator and PLC handshake GUI
 PLC-Sim bundles its Python runtime, Web GUI, OPC UA server, and handshake agents.
EOF

cat > "$DEB_ROOT/usr/share/applications/plc-sim.desktop" <<'EOF'
[Desktop Entry]
Type=Application
Name=PLC-Sim
Comment=OPC UA simulator and PLC handshake GUI
Exec=/opt/PLC-Sim/PLC-Sim gui
Terminal=false
Categories=Development;Science;
EOF

dpkg-deb --root-owner-group --build \
  "$DEB_ROOT" \
  "$OUTPUT_DIR/${BUNDLE_NAME}.deb"
