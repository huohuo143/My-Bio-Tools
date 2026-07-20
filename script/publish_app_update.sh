#!/usr/bin/env bash
set -euo pipefail

TARGET="${1:-}"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
AUTH_DIR="$ROOT_DIR/auth-service"
WRANGLER="$AUTH_DIR/node_modules/.bin/wrangler"
CONFIG="$AUTH_DIR/wrangler.jsonc"
DMG="${2:-$ROOT_DIR/dist/My-Bio-Tools-1.9.8-arm64.dmg}"
BUNDLE_ID="top.aizs.my-bio-tools"
PLATFORM="macos-arm64"
GITHUB_REPOSITORY="${MY_BIO_TOOLS_GITHUB_REPOSITORY:-huohuo143/My-Bio-Tools}"

if [[ "$TARGET" != "staging" && "$TARGET" != "production" ]]; then
  echo "usage: $0 <staging|production> [DMG]" >&2
  exit 2
fi
if [[ ! -x "$WRANGLER" || ! -f "$CONFIG" || ! -f "$DMG" ]] || ! command -v gh >/dev/null 2>&1; then
  echo "缺少 GitHub CLI、Wrangler、部署配置或 DMG，拒绝发布。" >&2
  exit 1
fi
if [[ "$(uname -m)" != "arm64" ]]; then
  echo "只能在 Apple Silicon Mac 上发布 arm64 更新。" >&2
  exit 1
fi

TEMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/my-bio-tools-update.XXXXXX")"
MOUNT_DIR="$TEMP_DIR/mount"
MANIFEST_FILE="$TEMP_DIR/update-manifest.json"
mkdir -p "$MOUNT_DIR"
cleanup() {
  /usr/bin/hdiutil detach "$MOUNT_DIR" >/dev/null 2>&1 || true
  /bin/rm -rf "$TEMP_DIR"
}
trap cleanup EXIT

/usr/bin/hdiutil verify "$DMG" >/dev/null
/usr/bin/hdiutil attach "$DMG" -nobrowse -readonly -mountpoint "$MOUNT_DIR" >/dev/null
APP="$MOUNT_DIR/My Bio Tools.app"
PLIST="$APP/Contents/Info.plist"
EXECUTABLE="$APP/Contents/MacOS/My Bio Tools"
if [[ ! -d "$APP" || ! -f "$PLIST" || ! -x "$EXECUTABLE" ]]; then
  echo "DMG 中没有完整的 My Bio Tools.app。" >&2
  exit 1
fi

VERSION="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$PLIST")"
BUILD="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleVersion' "$PLIST")"
ACTUAL_BUNDLE_ID="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$PLIST")"
MINIMUM_SYSTEM="$(/usr/libexec/PlistBuddy -c 'Print :LSMinimumSystemVersion' "$PLIST")"
if [[ "$ACTUAL_BUNDLE_ID" != "$BUNDLE_ID" || ! "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ || ! "$BUILD" =~ ^[0-9]+$ ]]; then
  echo "应用身份或版本元数据无效。" >&2
  exit 1
fi
/usr/bin/codesign --verify --deep --strict "$APP"
if ! /usr/bin/lipo -archs "$EXECUTABLE" | /usr/bin/grep -Eq '(^| )arm64( |$)'; then
  echo "更新包不是 arm64 应用。" >&2
  exit 1
fi

SHA256="$(/usr/bin/shasum -a 256 "$DMG" | /usr/bin/awk '{print $1}')"
SIZE="$(/usr/bin/stat -f '%z' "$DMG")"
ASSET_NAME="My-Bio-Tools-${VERSION}-arm64.dmg"
CHECKSUM_NAME="${ASSET_NAME}.sha256"
TAG="v${VERSION}"
WRANGLER_ARGS=(--config "$CONFIG")
if [[ "$TARGET" == "staging" ]]; then
  TAG="v${VERSION}-staging"
  WRANGLER_ARGS+=(--env staging)
else
  WRANGLER_ARGS+=(--env "")
fi

RELEASE_NOTES="${MY_BIO_TOOLS_RELEASE_NOTES:-多组学按差异统计、仅定量观察和论文证据分层完整展示；Word 与 AI 深度解读改为清晰的中文科研综述体。}"
CHECKSUM_FILE="$TEMP_DIR/$CHECKSUM_NAME"
printf '%s  %s\n' "$SHA256" "$ASSET_NAME" > "$CHECKSUM_FILE"

gh auth status >/dev/null
if gh release view "$TAG" --repo "$GITHUB_REPOSITORY" >/dev/null 2>&1; then
  ASSET_ID="$(gh api "repos/$GITHUB_REPOSITORY/releases/tags/$TAG" --jq ".assets[] | select(.name == \"$ASSET_NAME\") | .id" | head -n 1)"
  if [[ -z "$ASSET_ID" ]]; then
    if [[ "${MY_BIO_TOOLS_REPLACE_EXISTING:-0}" != "1" ]]; then
      echo "GitHub Release $TAG 已存在但缺少 ${ASSET_NAME}，拒绝覆盖现有发布。" >&2
      exit 1
    fi
    gh release upload "$TAG" "$DMG#$ASSET_NAME" "$CHECKSUM_FILE#$CHECKSUM_NAME" \
      --repo "$GITHUB_REPOSITORY" --clobber
    ASSET_ID="$(gh api "repos/$GITHUB_REPOSITORY/releases/tags/$TAG" --jq ".assets[] | select(.name == \"$ASSET_NAME\") | .id" | head -n 1)"
  elif [[ "${MY_BIO_TOOLS_REPLACE_EXISTING:-0}" == "1" ]]; then
    gh release upload "$TAG" "$DMG#$ASSET_NAME" "$CHECKSUM_FILE#$CHECKSUM_NAME" \
      --repo "$GITHUB_REPOSITORY" --clobber
    ASSET_ID="$(gh api "repos/$GITHUB_REPOSITORY/releases/tags/$TAG" --jq ".assets[] | select(.name == \"$ASSET_NAME\") | .id" | head -n 1)"
  fi
else
  release_args=(release create "$TAG" "$DMG#$ASSET_NAME" "$CHECKSUM_FILE#$CHECKSUM_NAME" --repo "$GITHUB_REPOSITORY" --title "My Bio Tools ${VERSION} (build ${BUILD})" --notes "$RELEASE_NOTES")
  if [[ "$TARGET" == "staging" ]]; then
    release_args+=(--prerelease)
  else
    release_args+=(--latest)
  fi
  gh "${release_args[@]}"
  ASSET_ID="$(gh api "repos/$GITHUB_REPOSITORY/releases/tags/$TAG" --jq ".assets[] | select(.name == \"$ASSET_NAME\") | .id" | head -n 1)"
fi

ASSET_SIZE="$(gh api "repos/$GITHUB_REPOSITORY/releases/assets/$ASSET_ID" --jq '.size')"
if [[ "$ASSET_SIZE" != "$SIZE" ]]; then
  echo "GitHub Release 资源大小与本地 DMG 不一致，拒绝发布清单。" >&2
  exit 1
fi

export VERSION BUILD MINIMUM_SYSTEM SIZE SHA256 RELEASE_NOTES BUNDLE_ID PLATFORM MANIFEST_FILE GITHUB_REPOSITORY ASSET_ID
node -e '
const fs = require("node:fs");
const manifest = {
  schemaVersion: 1,
  platform: process.env.PLATFORM,
  bundleIdentifier: process.env.BUNDLE_ID,
  appVersion: process.env.VERSION,
  build: Number(process.env.BUILD),
  minimumSystemVersion: process.env.MINIMUM_SYSTEM,
  size: Number(process.env.SIZE),
  sha256: process.env.SHA256,
  releaseSource: "github",
  githubRepository: process.env.GITHUB_REPOSITORY,
  githubAssetId: Number(process.env.ASSET_ID),
  releaseNotes: process.env.RELEASE_NOTES,
  publishedAt: new Date().toISOString(),
  mandatory: false,
};
fs.writeFileSync(process.env.MANIFEST_FILE, JSON.stringify(manifest));
'

cd "$AUTH_DIR"
gh auth token | "$WRANGLER" secret put GITHUB_RELEASES_TOKEN "${WRANGLER_ARGS[@]}"
tr -d '\r\n' < "$MANIFEST_FILE" | "$WRANGLER" secret put UPDATE_MANIFEST_JSON "${WRANGLER_ARGS[@]}"
"$WRANGLER" deploy "${WRANGLER_ARGS[@]}"

echo "已通过 GitHub Release 发布 My Bio Tools ${VERSION}（build ${BUILD}）到 ${TARGET}。"
echo "https://github.com/${GITHUB_REPOSITORY}/releases/tag/${TAG}"
echo "SHA-256: ${SHA256}"
