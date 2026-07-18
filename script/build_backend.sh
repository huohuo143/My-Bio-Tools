#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${BUILD_PYTHON:-$ROOT_DIR/.build-venv/bin/python}"
BACKEND_DIR="${BACKEND_DIR_OVERRIDE:-$ROOT_DIR/build/backend/BioToolsBackend}"
BACKEND_BINARY="$BACKEND_DIR/BioToolsBackend"
LOCAL_BUILD_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/my-bio-tools-pyinstaller.XXXXXX")"
LOCAL_DIST_DIR="$LOCAL_BUILD_ROOT/dist"
LOCAL_WORK_DIR="$LOCAL_BUILD_ROOT/work"
LOCAL_BACKEND_DIR="$LOCAL_DIST_DIR/BioToolsBackend"

if [[ ! -x "$PYTHON" ]]; then
  echo "缺少隔离构建环境。请先运行：./script/bootstrap_build_env.sh" >&2
  exit 1
fi

if ! "$PYTHON" -c "import PyInstaller, matplotlib, primer3" >/dev/null 2>&1; then
  echo "构建环境中缺少 PyInstaller、Matplotlib 或 primer3-py。" >&2
  echo "请先运行：./script/bootstrap_build_env.sh" >&2
  exit 1
fi

"$ROOT_DIR/script/build_nlstradamus.sh"
"$PYTHON" "$ROOT_DIR/script/verify_source.py"
"$PYTHON" "$ROOT_DIR/script/test_report_interpretation.py"
"$PYTHON" "$ROOT_DIR/script/test_codex_chatgpt.py"

export PYINSTALLER_CONFIG_DIR="$LOCAL_BUILD_ROOT/config"
"$PYTHON" -m PyInstaller \
  --noconfirm \
  --clean \
  --distpath "$LOCAL_DIST_DIR" \
  --workpath "$LOCAL_WORK_DIR" \
  "$ROOT_DIR/packaging/BioToolsBackend.spec"

if [[ ! -x "$LOCAL_BACKEND_DIR/BioToolsBackend" ]]; then
  echo "未生成后端可执行文件：$LOCAL_BACKEND_DIR/BioToolsBackend" >&2
  exit 1
fi

mkdir -p "$(dirname "$BACKEND_DIR")"
if [[ -e "$BACKEND_DIR" ]]; then
  mv "$BACKEND_DIR" "$(dirname "$BACKEND_DIR")/BioToolsBackend.previous-$(date +%Y%m%d-%H%M%S)"
fi
cp -R -X "$LOCAL_BACKEND_DIR" "$BACKEND_DIR"

"$BACKEND_BINARY" --help >/dev/null
"$BACKEND_BINARY" --runtime-smoke-test

UNITTEST_RUNTIME_DIR="$BACKEND_DIR/_internal/unittest"
if [[ ! -r "$UNITTEST_RUNTIME_DIR/__init__.py" ]]; then
  echo "冻结后端缺少外部 unittest 源码包：$UNITTEST_RUNTIME_DIR" >&2
  exit 1
fi
echo "Matplotlib PNG/SVG 与 unittest 冻结运行时验证通过。"

DOCX_RUNTIME_DIR="$BACKEND_DIR/_internal/docx"
if [[ ! -d "$DOCX_RUNTIME_DIR/parts" ]]; then
  echo "冻结后端缺少 python-docx 实体目录：$DOCX_RUNTIME_DIR/parts" >&2
  exit 1
fi
for template in default-header.xml default-footer.xml; do
  raw_template_path="$DOCX_RUNTIME_DIR/parts/../templates/$template"
  if [[ ! -r "$raw_template_path" ]]; then
    echo "冻结后端无法按 python-docx 原始相对路径读取模板：$raw_template_path" >&2
    exit 1
  fi
done
if [[ ! -r "$DOCX_RUNTIME_DIR/templates/default.docx" ]]; then
  echo "冻结后端缺少 python-docx 默认文档模板。" >&2
  exit 1
fi
echo "python-docx 冻结运行时布局验证通过。"

du -sh "$BACKEND_DIR"
echo "内置后端构建完成：$BACKEND_DIR"
