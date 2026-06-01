#!/usr/bin/env bash
#
# Build release artifacts for GitHub Releases.
#
# Outputs:
#   dist/AMD-BC-250-Undervolt-Control-<version>.tar.gz
#   dist/BC-250-Control-Center-<version>-<arch>.AppImage, if appimagetool exists

set -eu

ROOT_DIR="$(readlink -f "$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)")"
VERSION="${VERSION:-}"

usage() {
  cat <<EOF
Usage:
  ./packaging/build-release.sh [--version VERSION]

Environment:
  VERSION=1.0.0 ./packaging/build-release.sh

Outputs:
  dist/AMD-BC-250-Undervolt-Control-<version>.tar.gz
  dist/BC-250-Control-Center.AppDir/
  dist/BC-250-Control-Center-<version>-<arch>.AppImage, if appimagetool exists
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --version)
      [ -n "${2:-}" ] || {
        echo "--version requires a value" >&2
        exit 2
      }
      VERSION="$2"
      shift 2
      ;;
    -h|--help|help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [ -z "$VERSION" ]; then
  if command -v git >/dev/null 2>&1 && git -C "$ROOT_DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    VERSION="$(git -C "$ROOT_DIR" describe --tags --always --dirty 2>/dev/null || true)"
  fi
fi

if [ -z "$VERSION" ]; then
  VERSION="$(date +%Y%m%d)"
fi

APP_NAME="AMD-BC-250-Undervolt-Control"
APPIMAGE_NAME="BC-250-Control-Center"
DIST_DIR="$ROOT_DIR/dist"
RELEASE_DIR="$DIST_DIR/${APP_NAME}-${VERSION}"
APPDIR="$DIST_DIR/${APPIMAGE_NAME}.AppDir"
ARCH="$(uname -m)"

rm -rf "$RELEASE_DIR" "$APPDIR"
mkdir -p "$RELEASE_DIR" "$DIST_DIR"

copy_common_files() {
  local target="$1"

  mkdir -p "$target/locales"
  cp "$ROOT_DIR/README.md" "$target/"
  cp "$ROOT_DIR/bc250-control.sh" "$target/"
  cp "$ROOT_DIR/bc250-control-helper.sh" "$target/"
  cp "$ROOT_DIR/bc250-control-gui.py" "$target/"
  cp "$ROOT_DIR/install-polkit-policy.sh" "$target/"
  cp "$ROOT_DIR/install-dependencies.sh" "$target/"
  cp "$ROOT_DIR/locales/"*.json "$target/locales/"

  chmod 0755 \
    "$target/bc250-control.sh" \
    "$target/bc250-control-helper.sh" \
    "$target/bc250-control-gui.py" \
    "$target/install-polkit-policy.sh" \
    "$target/install-dependencies.sh"
}

echo "Building release directory: $RELEASE_DIR"
copy_common_files "$RELEASE_DIR"

tar -C "$DIST_DIR" -czf "$DIST_DIR/${APP_NAME}-${VERSION}.tar.gz" "${APP_NAME}-${VERSION}"
echo "Created: $DIST_DIR/${APP_NAME}-${VERSION}.tar.gz"

echo "Building AppDir: $APPDIR"
mkdir -p \
  "$APPDIR/usr/bin" \
  "$APPDIR/usr/share/applications" \
  "$APPDIR/usr/share/icons/hicolor/scalable/apps"

copy_common_files "$APPDIR/usr/bin"
cp "$ROOT_DIR/packaging/appimage/AppRun" "$APPDIR/AppRun"
cp "$ROOT_DIR/packaging/bc250-control-center.desktop" "$APPDIR/"
cp "$ROOT_DIR/packaging/bc250-control-center.desktop" "$APPDIR/usr/share/applications/"
cp "$ROOT_DIR/packaging/bc250-control.svg" "$APPDIR/"
cp "$ROOT_DIR/packaging/bc250-control.svg" "$APPDIR/usr/share/icons/hicolor/scalable/apps/"
chmod 0755 "$APPDIR/AppRun"

cat > "$APPDIR/usr/bin/bc250-control-center" <<'EOF'
#!/usr/bin/env bash
exec "$(dirname "$0")/bc250-control-gui.py" "$@"
EOF
chmod 0755 "$APPDIR/usr/bin/bc250-control-center"

if command -v appimagetool >/dev/null 2>&1; then
  APPIMAGE_OUTPUT="$DIST_DIR/${APPIMAGE_NAME}-${VERSION}-${ARCH}.AppImage"
  ARCH="$ARCH" appimagetool "$APPDIR" "$APPIMAGE_OUTPUT"
  chmod 0755 "$APPIMAGE_OUTPUT"
  echo "Created: $APPIMAGE_OUTPUT"
else
  echo
  echo "appimagetool was not found, so only the AppDir was created."
  echo "Install appimagetool and rerun this script to create the AppImage:"
  echo "  VERSION=$VERSION ./packaging/build-release.sh"
fi

echo
echo "Release artifacts are in: $DIST_DIR"
