#!/usr/bin/env bash
set -euo pipefail

MODE="${1:-run}"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_NAME="My Bio Tools"
BUNDLE_ID="top.aizs.my-bio-tools"
APP_BUNDLE="$ROOT_DIR/dist/$APP_NAME.app"
APP_BINARY="$APP_BUNDLE/Contents/MacOS/$APP_NAME"
BACKEND_PATTERN="$APP_BUNDLE/Contents/Resources/backend/BioToolsBackend"
LOG_FILE="$HOME/Library/Logs/My Bio Tools/backend.log"

pkill -x "$APP_NAME" >/dev/null 2>&1 || true
pkill -f "/My Bio Tools.app/Contents/Resources/backend/BioToolsBackend" >/dev/null 2>&1 || true
pkill -f "$BACKEND_PATTERN" >/dev/null 2>&1 || true
pkill -f "$APP_BINARY" >/dev/null 2>&1 || true
"$ROOT_DIR/script/build_app_bundle.sh"

open_app() {
  /usr/bin/open -n "$APP_BUNDLE"
}

verify_runtime() {
  local backend_pid=""
  local app_pid=""
  local command_line=""
  local port=""
  local health=""

  for _ in {1..120}; do
    app_pid="$(pgrep -f "$APP_BINARY" | head -n 1 || true)"
    if [[ -n "$app_pid" ]]; then
      backend_pid="$(pgrep -P "$app_pid" | head -n 1 || true)"
    fi
    if [[ -n "$backend_pid" ]]; then
      command_line="$(ps -ww -p "$backend_pid" -o command=)"
      port="$(printf "%s" "$command_line" | sed -n 's/.*--port \([0-9][0-9]*\).*/\1/p')"
      if [[ -n "$port" ]]; then
        health="$(curl -fsS --max-time 1 "http://127.0.0.1:$port/_stcore/health" 2>/dev/null || true)"
        if [[ "$health" == *"ok"* ]]; then
          echo "App 进程与内置服务验证通过（端口 ${port}）。"
          return 0
        fi
      fi
    fi
    sleep 0.25
  done

  echo "App 已启动，但内置服务未在 30 秒内通过健康检查。" >&2
  return 1
}

case "$MODE" in
  run)
    open_app
    ;;
  --debug|debug)
    lldb -- "$APP_BINARY"
    ;;
  --logs|logs)
    open_app
    mkdir -p "$(dirname "$LOG_FILE")"
    touch "$LOG_FILE"
    tail -f "$LOG_FILE"
    ;;
  --telemetry|telemetry)
    open_app
    /usr/bin/log stream --info --style compact \
      --predicate "subsystem == \"$BUNDLE_ID\" OR process == \"$APP_NAME\" OR process == \"BioToolsBackend\""
    ;;
  --verify|verify)
    open_app
    verify_runtime
    ;;
  *)
    echo "usage: $0 [run|--debug|--logs|--telemetry|--verify]" >&2
    exit 2
    ;;
esac
