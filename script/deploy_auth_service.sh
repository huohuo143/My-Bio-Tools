#!/usr/bin/env bash
set -euo pipefail

TARGET="${1:-}"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
AUTH_DIR="$ROOT_DIR/auth-service"
WRANGLER="$AUTH_DIR/node_modules/.bin/wrangler"
CONFIG="$AUTH_DIR/wrangler.jsonc"
SERVICE_PREFIX="top.aizs.my-bio-tools.auth"
OMICS_KEY_FILE="${MY_BIO_TOOLS_OMICS_KEY_FILE:-/Volumes/FAFU/analysis_results/wulab_omics_app_v1/secrets/omics_key.b64}"

if [[ "$TARGET" != "staging" && "$TARGET" != "production" ]]; then
  echo "usage: $0 <staging|production>" >&2
  exit 2
fi
if [[ ! -x "$WRANGLER" ]]; then
  echo "缺少项目内 Wrangler，请先在 auth-service 执行 npm install。" >&2
  exit 1
fi
if [[ ! -f "$CONFIG" ]]; then
  echo "缺少 $CONFIG" >&2
  exit 1
fi
if rg -q 'REPLACE_WITH_' "$CONFIG"; then
  echo "wrangler.jsonc 仍包含待替换值，拒绝部署。" >&2
  exit 1
fi
if [[ "$TARGET" == "production" ]] && rg -q 'PENDING_' "$CONFIG"; then
  echo "wrangler.jsonc 的 Production Access 配置仍待填写，拒绝部署。" >&2
  exit 1
fi
if [[ "$TARGET" == "staging" ]] && ! rg -Uq '"staging"\s*:\s*\{[\s\S]*?"routes"\s*:\s*\[\s*\]' "$CONFIG"; then
  echo "Staging 必须显式配置空 routes，避免继承 Production 自定义域名。" >&2
  exit 1
fi
if [[ -z "${CLOUDFLARE_API_TOKEN:-}" ]]; then
  "$WRANGLER" whoami >/dev/null
fi
if [[ ! -r "$OMICS_KEY_FILE" ]]; then
  echo "缺少多组学数据库密钥文件：$OMICS_KEY_FILE" >&2
  exit 1
fi
if ! tr -d '\r\n' < "$OMICS_KEY_FILE" | node -e '
let value = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", chunk => value += chunk);
process.stdin.on("end", () => {
  let decoded;
  try { decoded = Buffer.from(value, "base64"); } catch { process.exit(1); }
  if (decoded.length !== 32 || decoded.toString("base64") !== value) process.exit(1);
});
'; then
  echo "多组学数据库密钥不是 32-byte base64，拒绝部署。" >&2
  exit 1
fi

keychain_get() {
  security find-generic-password \
    -a "$TARGET" \
    -s "$SERVICE_PREFIX.$1" \
    -w
}

wrangler_args=(--config "$CONFIG")
if [[ "$TARGET" == "staging" ]]; then
  wrangler_args+=(--env staging)
else
  wrangler_args+=(--env "")
fi

cd "$AUTH_DIR"
"$WRANGLER" deploy --dry-run "${wrangler_args[@]}"
"$WRANGLER" d1 migrations apply DB --remote "${wrangler_args[@]}"

put_secrets() {
  for secret_name in PASSWORD_PEPPER IP_HASH_SALT LICENSE_PRIVATE_JWK LICENSE_PUBLIC_JWK RESEND_API_KEY; do
    keychain_get "$secret_name" | "$WRANGLER" secret put "$secret_name" "${wrangler_args[@]}"
  done
  if command -v gh >/dev/null 2>&1 && gh auth status >/dev/null 2>&1; then
    gh auth token | "$WRANGLER" secret put GITHUB_RELEASES_TOKEN "${wrangler_args[@]}"
  else
    echo "GitHub CLI 尚未登录，无法配置私有 Release 下载凭据。" >&2
    exit 1
  fi
  tr -d '\r\n' < "$OMICS_KEY_FILE" | "$WRANGLER" secret put OMICS_DATABASE_KEY_B64 "${wrangler_args[@]}"
  if [[ -n "${MY_BIO_TOOLS_UPDATE_MANIFEST_FILE:-}" ]]; then
    if ! node -e 'JSON.parse(require("node:fs").readFileSync(process.argv[1], "utf8"))' "$MY_BIO_TOOLS_UPDATE_MANIFEST_FILE"; then
      echo "更新清单不是有效 JSON，拒绝写入 Worker secret。" >&2
      exit 1
    fi
    tr -d '\r\n' < "$MY_BIO_TOOLS_UPDATE_MANIFEST_FILE" | "$WRANGLER" secret put UPDATE_MANIFEST_JSON "${wrangler_args[@]}"
  fi
  if [[ "$TARGET" == "staging" ]]; then
    keychain_get DEV_ADMIN_TOKEN | "$WRANGLER" secret put DEV_ADMIN_TOKEN "${wrangler_args[@]}"
  fi
}

if [[ "$TARGET" == "production" ]]; then
  # Production Worker already exists. Add the new secret while the old,
  # backward-compatible code is active, then deploy the current release without a login gap.
  put_secrets
  "$WRANGLER" deploy "${wrangler_args[@]}"
else
  # Staging may not exist yet; create it, inject secrets, then publish once more.
  "$WRANGLER" deploy "${wrangler_args[@]}"
  put_secrets
  "$WRANGLER" deploy "${wrangler_args[@]}"
fi

echo "${TARGET} 授权服务已部署。"
