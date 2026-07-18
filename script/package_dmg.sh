#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_NAME="My Bio Tools"
VERSION="${VERSION:-1.9.1}"
ARCH="$(uname -m)"
DIST_DIR="${DIST_DIR_OVERRIDE:-$ROOT_DIR/dist}"
APP_BUNDLE="$DIST_DIR/$APP_NAME.app"
DMG="$DIST_DIR/My-Bio-Tools-$VERSION-$ARCH.dmg"
CHECKSUM="$DMG.sha256"

if [[ -z "${MY_BIO_TOOLS_LICENSE_PUBLIC_JWK:-}" ]]; then
  echo "缺少 MY_BIO_TOOLS_LICENSE_PUBLIC_JWK；拒绝生成无法登录的生产 DMG。" >&2
  exit 1
fi
if [[ "${SKIP_APP_BUILD:-0}" != "1" ]]; then
  "$ROOT_DIR/script/build_app_bundle.sh"
elif [[ ! -d "$APP_BUNDLE" ]]; then
  echo "SKIP_APP_BUILD=1，但找不到已构建的 App：$APP_BUNDLE" >&2
  exit 1
fi

if [[ -n "${SIGN_IDENTITY:-}" ]]; then
  "$ROOT_DIR/script/sign_for_distribution.sh" "$APP_BUNDLE"
fi

STAGE_DIR="$(mktemp -d "$ROOT_DIR/build/dmg-stage.XXXXXX")"
ditto "$APP_BUNDLE" "$STAGE_DIR/$APP_NAME.app"
ln -s /Applications "$STAGE_DIR/Applications"
ditto "$ROOT_DIR/packaging/首次打开说明.txt" "$STAGE_DIR/首次打开说明.txt"

if [[ -e "$DMG" ]]; then
  ARCHIVE_DIR="$DIST_DIR/archive"
  mkdir -p "$ARCHIVE_DIR"
  STAMP="$(date +%Y%m%d-%H%M%S)"
  mv "$DMG" "$ARCHIVE_DIR/My-Bio-Tools-$VERSION-$ARCH-$STAMP.dmg"
  if [[ -e "$CHECKSUM" ]]; then
    mv "$CHECKSUM" "$ARCHIVE_DIR/My-Bio-Tools-$VERSION-$ARCH-$STAMP.dmg.sha256"
  fi
fi

hdiutil create \
  -volname "$APP_NAME $VERSION" \
  -srcfolder "$STAGE_DIR" \
  -ov \
  -format UDZO \
  "$DMG"

if [[ -n "${SIGN_IDENTITY:-}" ]]; then
  codesign --force --timestamp --sign "$SIGN_IDENTITY" "$DMG"
fi

hdiutil verify "$DMG"
(cd "$DIST_DIR" && shasum -a 256 "$(basename "$DMG")" > "$(basename "$CHECKSUM")")
du -sh "$DMG"
echo "DMG 已生成：$DMG"
echo "SHA-256 已生成：$CHECKSUM"
