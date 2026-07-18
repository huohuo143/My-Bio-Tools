#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_BUNDLE="${1:-$ROOT_DIR/dist/My Bio Tools.app}"
DMG="${2:-$ROOT_DIR/dist/My-Bio-Tools-1.8.0-$(uname -m).dmg}"

if [[ ! -d "$APP_BUNDLE" ]]; then
  echo "找不到 App：$APP_BUNDLE" >&2
  exit 1
fi

plutil -lint "$APP_BUNDLE/Contents/Info.plist"
PUBLIC_JWK="$(plutil -extract MyBioToolsLicensePublicJWK raw "$APP_BUNDLE/Contents/Info.plist")"
if [[ -z "$PUBLIC_JWK" ]]; then
  echo "App 未注入授权公钥。" >&2
  exit 1
fi
if plutil -extract MyBioToolsOmicsKeyB64 raw "$APP_BUNDLE/Contents/Info.plist" >/dev/null 2>&1; then
  echo "App 包内不应嵌入多组学解锁密钥。" >&2
  exit 1
fi
if [[ ! -r "$APP_BUNDLE/Contents/Resources/app_source/data/lab_omics/wulab_omics_v1.sqlite.zlib.aesctr" ]]; then
  echo "App 内缺少加密多组学数据库。" >&2
  exit 1
fi
codesign --verify --deep --strict --verbose=2 "$APP_BUNDLE"
codesign -dvvv --entitlements :- "$APP_BUNDLE" 2>&1

if spctl -a -vv --type execute "$APP_BUNDLE"; then
  echo "Gatekeeper：accepted"
else
  echo "Gatekeeper：rejected（未公证或仅临时签名时属于预期结果）"
fi

file "$APP_BUNDLE/Contents/MacOS/My Bio Tools"
file "$APP_BUNDLE/Contents/Resources/backend/BioToolsBackend"
"$APP_BUNDLE/Contents/Resources/backend/BioToolsBackend" --runtime-smoke-test

UNITTEST_RUNTIME_DIR="$APP_BUNDLE/Contents/Resources/backend/_internal/unittest"
if [[ ! -r "$UNITTEST_RUNTIME_DIR/__init__.py" ]]; then
  echo "App 内缺少外部 unittest 源码包：$UNITTEST_RUNTIME_DIR" >&2
  exit 1
fi
echo "Matplotlib 与 unittest App 运行时：通过"

DOCX_RUNTIME_DIR="$APP_BUNDLE/Contents/Resources/backend/_internal/docx"
if [[ ! -d "$DOCX_RUNTIME_DIR/parts" ]]; then
  echo "App 内缺少 python-docx 实体目录：$DOCX_RUNTIME_DIR/parts" >&2
  exit 1
fi
for template in default-header.xml default-footer.xml; do
  raw_template_path="$DOCX_RUNTIME_DIR/parts/../templates/$template"
  if [[ ! -r "$raw_template_path" ]]; then
    echo "App 内无法按 python-docx 原始相对路径读取模板：$raw_template_path" >&2
    exit 1
  fi
done
if [[ ! -r "$DOCX_RUNTIME_DIR/templates/default.docx" ]]; then
  echo "App 内缺少 python-docx 默认文档模板。" >&2
  exit 1
fi
echo "python-docx App 运行时布局：通过"

if [[ -f "$DMG" ]]; then
  hdiutil verify "$DMG"
  if spctl -a -vv -t install "$DMG"; then
    echo "DMG Gatekeeper：accepted"
  else
    echo "DMG Gatekeeper：rejected（未公证时属于预期结果）"
  fi
fi
