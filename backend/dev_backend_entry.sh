#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec /opt/miniconda3/envs/stbio/bin/python "$SCRIPT_DIR/launcher.py" "$@"
