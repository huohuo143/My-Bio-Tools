#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SOURCE="$ROOT_DIR/app_source/vendor/nlstradamus/NLStradamus.cpp"
OUTPUT_DIR="$ROOT_DIR/app_source/vendor/nlstradamus/bin"
OUTPUT="$OUTPUT_DIR/NLStradamus"
CXX="${CXX:-clang++}"

if [[ ! -f "$SOURCE" ]]; then
  echo "缺少 NLStradamus 1.8 原版源码：$SOURCE" >&2
  exit 1
fi
if ! command -v "$CXX" >/dev/null 2>&1; then
  echo "找不到 C++ 编译器：$CXX" >&2
  exit 1
fi

mkdir -p "$OUTPUT_DIR"
"$CXX" -O3 -std=c++11 "$SOURCE" -o "$OUTPUT"
chmod +x "$OUTPUT"
"$OUTPUT" -h | grep -F "NLStradamus v1.8" >/dev/null
echo "NLStradamus 1.8 已构建：$OUTPUT"
