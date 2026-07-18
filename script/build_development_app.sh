#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEV_BACKEND="$ROOT_DIR/build/dev-backend"
DEV_DIST="$ROOT_DIR/dist-dev"

if [[ ! -x /opt/miniconda3/envs/stbio/bin/python ]]; then
  echo "本机 stbio 环境不存在，无法执行开发态外壳测试。" >&2
  exit 1
fi

mkdir -p "$DEV_BACKEND"
ditto "$ROOT_DIR/backend/launcher.py" "$DEV_BACKEND/launcher.py"
ditto "$ROOT_DIR/backend/dev_backend_entry.sh" "$DEV_BACKEND/BioToolsBackend"
chmod +x "$DEV_BACKEND/BioToolsBackend"

SKIP_BACKEND_BUILD=1 \
BACKEND_DIR_OVERRIDE="$DEV_BACKEND" \
DIST_DIR_OVERRIDE="$DEV_DIST" \
  "$ROOT_DIR/script/build_app_bundle.sh"

echo "开发态 App 已生成：$DEV_DIST/My Bio Tools.app"
