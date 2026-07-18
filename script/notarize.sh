#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DMG="${1:-$ROOT_DIR/dist/My-Bio-Tools-1.8.0-$(uname -m).dmg}"
PROFILE="${NOTARY_PROFILE:-}"

if [[ -z "$PROFILE" ]]; then
  echo "请通过 NOTARY_PROFILE 指定已保存的 notarytool 钥匙串配置。" >&2
  exit 1
fi
if [[ ! -f "$DMG" ]]; then
  echo "找不到 DMG：$DMG" >&2
  exit 1
fi

xcrun notarytool submit "$DMG" --keychain-profile "$PROFILE" --wait
xcrun stapler staple "$DMG"
xcrun stapler validate "$DMG"
spctl -a -vv -t install "$DMG"
echo "公证与装订验证完成：$DMG"
