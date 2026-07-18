#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SOURCE="$ROOT_DIR/Resources/AppIcon-1024.png"
OUTPUT="$ROOT_DIR/Resources/AppIcon.icns"
mkdir -p "$ROOT_DIR/build"
WORK_ROOT="$(mktemp -d "$ROOT_DIR/build/icon.XXXXXX")"
ICONSET="$WORK_ROOT/AppIcon.iconset"

if [[ ! -f "$SOURCE" ]]; then
  echo "缺少 1024×1024 图标源文件：$SOURCE" >&2
  exit 1
fi

mkdir -p "$ICONSET"

sips -z 16 16 "$SOURCE" --out "$ICONSET/icon_16x16.png" >/dev/null
sips -z 32 32 "$SOURCE" --out "$ICONSET/icon_16x16@2x.png" >/dev/null
sips -z 32 32 "$SOURCE" --out "$ICONSET/icon_32x32.png" >/dev/null
sips -z 64 64 "$SOURCE" --out "$ICONSET/icon_32x32@2x.png" >/dev/null
sips -z 128 128 "$SOURCE" --out "$ICONSET/icon_128x128.png" >/dev/null
sips -z 256 256 "$SOURCE" --out "$ICONSET/icon_128x128@2x.png" >/dev/null
sips -z 256 256 "$SOURCE" --out "$ICONSET/icon_256x256.png" >/dev/null
sips -z 512 512 "$SOURCE" --out "$ICONSET/icon_256x256@2x.png" >/dev/null
sips -z 512 512 "$SOURCE" --out "$ICONSET/icon_512x512.png" >/dev/null
sips -z 1024 1024 "$SOURCE" --out "$ICONSET/icon_512x512@2x.png" >/dev/null

if [[ -f "$OUTPUT" ]]; then
  ARCHIVE_DIR="$ROOT_DIR/Resources/icon-archive"
  mkdir -p "$ARCHIVE_DIR"
  mv "$OUTPUT" "$ARCHIVE_DIR/AppIcon-$(date +%Y%m%d-%H%M%S).icns"
fi

iconutil -c icns "$ICONSET" -o "$OUTPUT"
echo "macOS 图标已生成：$OUTPUT"
