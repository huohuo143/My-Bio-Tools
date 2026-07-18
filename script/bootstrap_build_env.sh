#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="$ROOT_DIR/.build-venv"
BASE_PYTHON="${BASE_PYTHON:-/opt/miniconda3/envs/stbio/bin/python}"
RUNTIME_REQUIREMENTS="$ROOT_DIR/packaging/runtime-requirements.txt"
RUNTIME_MARKER="$VENV_DIR/.standalone-runtime-v2"

if [[ ! -x "$BASE_PYTHON" ]]; then
  echo "找不到基础 Python：$BASE_PYTHON" >&2
  exit 1
fi

if [[ ! -x "$VENV_DIR/bin/python" ]]; then
  "$BASE_PYTHON" -m venv "$VENV_DIR"
fi

if [[ ! -f "$RUNTIME_MARKER" ]]; then
  # Install local wheel copies even when this venv was originally layered on
  # Conda. This keeps PyInstaller hooks independent of Conda metadata and
  # ensures the final App contains every runtime dependency.
  "$VENV_DIR/bin/python" -m pip install \
    --ignore-installed \
    -r "$RUNTIME_REQUIREMENTS"
  touch "$RUNTIME_MARKER"
fi

"$VENV_DIR/bin/python" -m pip install "pyinstaller>=6.0" "primer3-py>=2.0"
"$VENV_DIR/bin/python" -m pip check
"$VENV_DIR/bin/python" -m pip freeze --all | sort >"$ROOT_DIR/build-requirements.lock"

echo "隔离构建环境已就绪：$VENV_DIR"
