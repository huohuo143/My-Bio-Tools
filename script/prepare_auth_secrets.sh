#!/usr/bin/env bash
set -euo pipefail

MODE="${1:-prepare}"
SERVICE_PREFIX="top.aizs.my-bio-tools.auth"

if [[ "$MODE" != "prepare" && "$MODE" != "--rotate" ]]; then
  echo "usage: $0 [prepare|--rotate]" >&2
  exit 2
fi

for command_name in node openssl security; do
  if ! command -v "$command_name" >/dev/null 2>&1; then
    echo "缺少必需命令：${command_name}" >&2
    exit 1
  fi
done

keychain_has() {
  security find-generic-password -a "$1" -s "$SERVICE_PREFIX.$2" >/dev/null 2>&1
}

keychain_put() {
  local environment="$1"
  local name="$2"
  local value="$3"
  security add-generic-password \
    -a "$environment" \
    -s "$SERVICE_PREFIX.$name" \
    -U \
    -w "$value" >/dev/null
}

random_secret() {
  openssl rand -base64 48 | tr -d '\n'
}

generate_ed25519_pair() {
  node --input-type=module <<'NODE'
const pair = await crypto.subtle.generateKey({ name: "Ed25519" }, true, ["sign", "verify"]);
const privateJWK = await crypto.subtle.exportKey("jwk", pair.privateKey);
const publicJWK = await crypto.subtle.exportKey("jwk", pair.publicKey);
// Node exports a non-standard alg="Ed25519" member that Cloudflare Workers
// rejects. JWK alg is optional; omit it for cross-runtime compatibility.
delete privateJWK.alg;
delete publicJWK.alg;
process.stdout.write(`${JSON.stringify(privateJWK)}\n${JSON.stringify(publicJWK)}`);
NODE
}

prepare_environment() {
  local environment="$1"
  local rotate="$2"
  local pair private_jwk public_jwk

  if [[ "$rotate" == "1" ]] || ! keychain_has "$environment" PASSWORD_PEPPER; then
    keychain_put "$environment" PASSWORD_PEPPER "$(random_secret)"
  fi
  if [[ "$rotate" == "1" ]] || ! keychain_has "$environment" IP_HASH_SALT; then
    keychain_put "$environment" IP_HASH_SALT "$(random_secret)"
  fi

  if [[ "$rotate" == "1" ]] || \
     ! keychain_has "$environment" LICENSE_PRIVATE_JWK || \
     ! keychain_has "$environment" LICENSE_PUBLIC_JWK; then
    pair="$(generate_ed25519_pair)"
    private_jwk="${pair%%$'\n'*}"
    public_jwk="${pair#*$'\n'}"
    keychain_put "$environment" LICENSE_PRIVATE_JWK "$private_jwk"
    keychain_put "$environment" LICENSE_PUBLIC_JWK "$public_jwk"
  fi

  if [[ "$environment" == "staging" ]] && \
     { [[ "$rotate" == "1" ]] || ! keychain_has "$environment" DEV_ADMIN_TOKEN; }; then
    keychain_put "$environment" DEV_ADMIN_TOKEN "$(random_secret)"
  fi

  echo "${environment}：Keychain 授权密钥已准备（未输出密钥内容）。"
}

rotate=0
if [[ "$MODE" == "--rotate" ]]; then
  rotate=1
fi

prepare_environment staging "$rotate"
prepare_environment production "$rotate"
