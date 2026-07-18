#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_BUNDLE="${1:-$ROOT_DIR/dist/My Bio Tools.app}"
IDENTITY="${SIGN_IDENTITY:-}"

if [[ -z "$IDENTITY" ]]; then
  echo "请通过 SIGN_IDENTITY 指定 Developer ID Application 证书名称。" >&2
  exit 1
fi
if [[ ! -d "$APP_BUNDLE" ]]; then
  echo "找不到 App：$APP_BUNDLE" >&2
  exit 1
fi

BACKEND_ROOT="$APP_BUNDLE/Contents/Resources/backend"
while IFS= read -r -d '' candidate; do
  if file "$candidate" | grep -q "Mach-O"; then
    codesign \
      --force \
      --options runtime \
      --timestamp \
      --sign "$IDENTITY" \
      "$candidate"
  fi
done < <(find "$BACKEND_ROOT" -type f -print0)

codesign \
  --force \
  --options runtime \
  --timestamp \
  --sign "$IDENTITY" \
  "$APP_BUNDLE/Contents/MacOS/My Bio Tools"

codesign \
  --force \
  --options runtime \
  --timestamp \
  --sign "$IDENTITY" \
  "$APP_BUNDLE"

codesign --verify --deep --strict --verbose=2 "$APP_BUNDLE"
echo "Developer ID 签名完成：$APP_BUNDLE"
