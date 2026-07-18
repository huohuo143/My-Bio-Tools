#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_NAME="My Bio Tools"
SWIFT_PRODUCT="BioToolsApp"
DIST_DIR="${DIST_DIR_OVERRIDE:-$ROOT_DIR/dist}"
APP_BUNDLE="$DIST_DIR/$APP_NAME.app"
BACKEND_DIR="${BACKEND_DIR_OVERRIDE:-$ROOT_DIR/build/backend/BioToolsBackend}"
ENCRYPTED_OMICS="$ROOT_DIR/app_source/data/lab_omics/wulab_omics_v1.sqlite.zlib.aesctr"

if [[ ! -r "$ENCRYPTED_OMICS" ]]; then
  echo "缺少加密多组学数据库：$ENCRYPTED_OMICS" >&2
  exit 1
fi

if [[ "${SKIP_BACKEND_BUILD:-0}" != "1" ]]; then
  "$ROOT_DIR/script/build_backend.sh"
fi

if [[ ! -x "$BACKEND_DIR/BioToolsBackend" ]]; then
  echo "缺少已构建的内置后端：$BACKEND_DIR" >&2
  exit 1
fi

"$ROOT_DIR/script/prepare_icon.sh"
swift build \
  --package-path "$ROOT_DIR" \
  -c release \
  --product "$SWIFT_PRODUCT"

SWIFT_BIN_DIR="$(swift build --package-path "$ROOT_DIR" -c release --show-bin-path)"
SWIFT_BINARY="$SWIFT_BIN_DIR/$SWIFT_PRODUCT"
if [[ ! -x "$SWIFT_BINARY" ]]; then
  echo "缺少 Swift 可执行文件：$SWIFT_BINARY" >&2
  exit 1
fi

mkdir -p "$ROOT_DIR/build"
STAGE_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/my-bio-tools-app-stage.XXXXXX")"
STAGED_APP="$STAGE_ROOT/$APP_NAME.app"
CONTENTS="$STAGED_APP/Contents"
MACOS_DIR="$CONTENTS/MacOS"
RESOURCES_DIR="$CONTENTS/Resources"

mkdir -p "$MACOS_DIR" "$RESOURCES_DIR"
ditto "$SWIFT_BINARY" "$MACOS_DIR/$APP_NAME"
chmod +x "$MACOS_DIR/$APP_NAME"
ditto "$ROOT_DIR/packaging/Info.plist" "$CONTENTS/Info.plist"
if [[ -n "${MY_BIO_TOOLS_LICENSE_PUBLIC_JWK:-}" ]]; then
  plutil -replace MyBioToolsLicensePublicJWK -string "$MY_BIO_TOOLS_LICENSE_PUBLIC_JWK" "$CONTENTS/Info.plist"
fi
ditto "$ROOT_DIR/Resources/AppIcon.icns" "$RESOURCES_DIR/AppIcon.icns"
ditto "$BACKEND_DIR" "$RESOURCES_DIR/backend"
ditto "$ROOT_DIR/app_source" "$RESOURCES_DIR/app_source"

xattr -cr "$STAGED_APP"
codesign --force --deep --sign - "$STAGED_APP"
codesign --verify --deep --strict --verbose=2 "$STAGED_APP"

mkdir -p "$DIST_DIR"
if [[ -e "$APP_BUNDLE" ]]; then
  ARCHIVE_DIR="$DIST_DIR/archive"
  mkdir -p "$ARCHIVE_DIR"
  mv "$APP_BUNDLE" "$ARCHIVE_DIR/$APP_NAME-$(date +%Y%m%d-%H%M%S).app"
fi
mv "$STAGED_APP" "$APP_BUNDLE"

du -sh "$APP_BUNDLE"
echo "macOS App 已生成：$APP_BUNDLE"
