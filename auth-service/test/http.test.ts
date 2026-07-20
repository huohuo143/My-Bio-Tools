import assert from "node:assert/strict";
import test from "node:test";
import worker from "../src/index.ts";
import { adminDashboard, resetPasswordForm } from "../src/pages.ts";
import type { Env, UserRow } from "../src/types.ts";

const sampleAdminUser: UserRow = {
  id: "user-1", email: "member@example.test", real_name: "测试成员", lab_role: "博士研究生",
  application_note: "课题组内部使用", password_hash: "hash", password_salt: "salt", status: "pending",
  email_verified_at: 1, reviewed_at: null, reviewed_by: null, review_reason: null,
  authorization_expires_at: null,
  failed_attempts: 0, locked_until: null, created_at: 1, updated_at: 1,
};

test("health endpoint exposes no secret configuration", async () => {
  const pair = await crypto.subtle.generateKey({ name: "Ed25519" }, true, ["sign", "verify"]);
  const env = {
    LICENSE_PRIVATE_JWK: JSON.stringify(await crypto.subtle.exportKey("jwk", pair.privateKey)),
    LICENSE_PUBLIC_JWK: JSON.stringify(await crypto.subtle.exportKey("jwk", pair.publicKey)),
    OMICS_DATABASE_KEY_B64: "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
    UPDATE_MANIFEST_JSON: JSON.stringify({
      platform: "macos-arm64", bundleIdentifier: "top.aizs.my-bio-tools",
      appVersion: "1.9.1", build: 20, minimumSystemVersion: "13.0",
      size: 1, sha256: "a".repeat(64),
      releaseSource: "github", githubRepository: "huohuo143/My-Bio-Tools", githubAssetId: 190019,
      releaseNotes: "fixture", publishedAt: "2026-07-18T18:30:00+08:00",
    }),
  } as Env;
  const response = await worker.fetch(new Request("https://example.test/health"), env);
  assert.equal(response.status, 200);
  const body = await response.json() as { status: string; licenseSigning: string; omicsKeyDelivery: string; appUpdate: string };
  assert.equal(body.status, "ok");
  assert.equal(body.licenseSigning, "ok");
  assert.equal(body.omicsKeyDelivery, "ok");
  assert.equal(body.appUpdate, "ok");
  assert.equal((body as { authorizationPeriod?: string }).authorizationPeriod, "ok");
  assert.equal(response.headers.get("cache-control"), "no-store");
});

test("unknown routes return a stable JSON error", async () => {
  const response = await worker.fetch(new Request("https://example.test/not-found"), {} as Env);
  assert.equal(response.status, 404);
  const body = await response.json() as { error: { code: string } };
  assert.equal(body.error.code, "NOT_FOUND");
});

test("admin page is server-rendered without client scripts", async () => {
  const response = adminDashboard();
  assert.doesNotMatch(response.headers.get("content-security-policy") ?? "", /script-src/u);
  assert.doesNotMatch(await response.text(), /<script/u);
});

test("admin page exposes reviewed account operations", async () => {
  const html = await adminDashboard([sampleAdminUser], { pending: 1 }).text();
  assert.match(html, /发送密码重置邮件/u);
  assert.match(html, /设备/u);
  assert.match(html, /永久删除/u);
  assert.match(html, /name="confirmation"/u);
  assert.match(html, /action="\/admin\/action"/u);
  for (const label of ["1月", "6月", "1年", "2年", "永久", "自定义时间"]) assert.match(html, new RegExp(label, "u"));
  assert.match(html, /name="authorizationPeriod"/u);
  assert.match(html, /name="customExpiresOn" type="date"/u);
});

test("reset form never places the token in external URLs", async () => {
  const response = resetPasswordForm("test-token");
  const html = await response.text();
  assert.match(html, /action="\/reset-password"/u);
  assert.match(html, /value="test-token"/u);
  assert.match(html, /minlength="8"/u);
  assert.equal(response.headers.get("referrer-policy"), "no-referrer");
});
