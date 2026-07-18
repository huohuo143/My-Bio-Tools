import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { DatabaseSync } from "node:sqlite";
import test from "node:test";
import worker from "../src/index.ts";
import type { D1Database, D1Result, D1Statement, Env, R2ObjectBody } from "../src/types.ts";

class SQLiteStatement implements D1Statement {
  values: unknown[] = [];
  readonly database: DatabaseSync;
  readonly query: string;
  constructor(database: DatabaseSync, query: string) { this.database = database; this.query = query; }
  bind(...values: unknown[]): SQLiteStatement { this.values = values; return this; }
  async first<T>(): Promise<T | null> {
    return (this.database.prepare(this.query).get(...this.values) as T | undefined) ?? null;
  }
  async all<T>(): Promise<D1Result<T>> {
    return { success: true, meta: {}, results: this.database.prepare(this.query).all(...this.values) as T[] };
  }
  async run<T>(): Promise<D1Result<T>> {
    const result = this.database.prepare(this.query).run(...this.values);
    return { success: true, meta: { changes: Number(result.changes) } };
  }
}

class SQLiteD1 implements D1Database {
  readonly database = new DatabaseSync(":memory:");
  prepare(query: string): SQLiteStatement { return new SQLiteStatement(this.database, query); }
  async batch(statements: D1Statement[]): Promise<D1Result[]> {
    this.database.exec("BEGIN IMMEDIATE");
    try {
      const results: D1Result[] = [];
      for (const statement of statements as SQLiteStatement[]) results.push(await statement.run());
      this.database.exec("COMMIT");
      return results;
    } catch (error) {
      this.database.exec("ROLLBACK");
      throw error;
    }
  }
}

interface SentMail { to: string; subject: string; text?: string; html?: string }

async function fixture() {
  const db = new SQLiteD1();
  db.database.exec(readFileSync(new URL("../migrations/0001_initial.sql", import.meta.url), "utf8"));
  const pair = await crypto.subtle.generateKey({ name: "Ed25519" }, true, ["sign", "verify"]);
  const mail: SentMail[] = [];
  const releaseBytes = new TextEncoder().encode("fixture macOS update payload");
  const releaseDigest = Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", releaseBytes)))
    .map((value) => value.toString(16).padStart(2, "0")).join("");
  const releaseKey = "releases/production/macos-arm64/My-Bio-Tools-1.9.0-arm64.dmg";
  const env: Env = {
    DB: db,
    EMAIL_TEST_SENDER: { async send(message) { mail.push(message as SentMail); return { messageId: crypto.randomUUID() }; } },
    APP_ORIGIN: "https://auth.test",
    EMAIL_FROM: "noreply@example.test",
    RESEND_API_KEY: "test-only-resend-key",
    ENVIRONMENT: "test",
    ACCESS_TEAM_DOMAIN: "example.cloudflareaccess.com",
    ACCESS_AUD: "test-audience",
    ADMIN_EMAILS: "owner@example.test",
    ADMIN_NOTIFICATION_EMAIL: "owner@example.test",
    PASSWORD_PEPPER: "integration-test-pepper",
    LICENSE_PRIVATE_JWK: JSON.stringify(await crypto.subtle.exportKey("jwk", pair.privateKey)),
    LICENSE_PUBLIC_JWK: JSON.stringify(await crypto.subtle.exportKey("jwk", pair.publicKey)),
    OMICS_DATABASE_KEY_B64: "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
    IP_HASH_SALT: "integration-test-ip-salt",
    DEV_ADMIN_TOKEN: "local-admin",
    UPDATE_MANIFEST_JSON: JSON.stringify({
      platform: "macos-arm64", bundleIdentifier: "top.aizs.my-bio-tools",
      appVersion: "1.9.0", build: 19, minimumSystemVersion: "13.0",
      size: releaseBytes.byteLength, sha256: releaseDigest, r2Key: releaseKey,
      releaseNotes: "增加科研解读与一键更新。", publishedAt: "2026-07-18T18:30:00+08:00",
      mandatory: false,
    }),
    RELEASES: {
      async get(key: string): Promise<R2ObjectBody | null> {
        if (key !== releaseKey) return null;
        return {
          body: new Response(releaseBytes).body!, size: releaseBytes.byteLength,
          httpEtag: '"fixture-etag"', writeHttpMetadata() {},
        };
      },
    },
  };
  const request = (path: string, method = "GET", body?: unknown, bearer?: string, admin = false) => {
    const headers: Record<string, string> = { "CF-Connecting-IP": "127.0.0.1" };
    if (body !== undefined) headers["content-type"] = "application/json";
    if (bearer) headers.authorization = `Bearer ${bearer}`;
    if (admin) headers["x-dev-admin-token"] = "local-admin";
    return worker.fetch(new Request(`https://auth.test${path}`, {
      method, headers, body: body === undefined ? undefined : JSON.stringify(body),
    }), env);
  };
  return { db, env, mail, request };
}

function tokenFromMail(mail: SentMail[], subject: RegExp): string {
  const message = [...mail].reverse().find((candidate) => subject.test(candidate.subject));
  assert.ok(message?.text, `missing email matching ${subject}`);
  const link = message.text.match(/https:\/\/\S+/u)?.[0];
  assert.ok(link);
  return new URL(link).searchParams.get("token") ?? "";
}

async function responseBody(response: Response): Promise<Record<string, any>> {
  return await response.json() as Record<string, any>;
}

test("complete registration, review, device, reset, suspension and deletion lifecycle", async () => {
  const { db, env, mail, request } = await fixture();
  const email = "member@example.test";
  const password = "Valid-research-password-2026";

  let response = await request("/api/v1/register", "POST", {
    email, realName: "测试成员", labRole: "博士研究生", applicationNote: "课题组内部使用", password,
  });
  assert.equal(response.status, 201);
  assert.equal(db.database.prepare("select status from users where email = ?").get(email)?.status, "unverified");

  const verifyToken = tokenFromMail(mail, /验证/u);
  response = await request(`/verify-email?token=${encodeURIComponent(verifyToken)}`);
  assert.equal(response.status, 200);
  const user = db.database.prepare("select id, status from users where email = ?").get(email) as { id: string; status: string };
  assert.equal(user.status, "pending");

  response = await request(`/api/v1/admin/users/${user.id}/status`, "PATCH", { status: "active" }, undefined, true);
  assert.equal(response.status, 200);

  const login = async (installationId: string) => request("/api/v1/login", "POST", {
    email, password, installationId, platform: "macos", deviceName: installationId, appVersion: "1.8.0",
  });
  const first = await login("installation-one");
  const firstBody = await responseBody(first);
  assert.equal(first.status, 200);
  assert.equal(typeof firstBody.offlineLicense, "string");
  const licensePayload = JSON.parse(
    Buffer.from(String(firstBody.offlineLicense).split(".")[1], "base64url").toString("utf8"),
  ) as { omics_key_b64?: string };
  assert.equal(licensePayload.omics_key_b64, env.OMICS_DATABASE_KEY_B64);
  response = await request("/api/v1/app-update", "GET", undefined, firstBody.accessToken);
  assert.equal(response.status, 200);
  const manifestToken = String((await responseBody(response)).manifestToken);
  const updateClaims = JSON.parse(Buffer.from(manifestToken.split(".")[1], "base64url").toString("utf8")) as {
    typ: string; app_version: string; build: number; sha256: string;
  };
  assert.equal(updateClaims.typ, "app-update");
  assert.equal(updateClaims.app_version, "1.9.0");
  assert.equal(updateClaims.build, 19);
  assert.equal(updateClaims.sha256.length, 64);
  response = await request("/api/v1/app-update/download", "GET", undefined, firstBody.accessToken);
  assert.equal(response.status, 200);
  assert.equal(await response.text(), "fixture macOS update payload");
  const second = await login("installation-two");
  const secondBody = await responseBody(second);
  assert.equal(second.status, 200);
  const third = await login("installation-three");
  assert.equal(third.status, 409);
  assert.equal((await responseBody(third)).error.code, "DEVICE_LIMIT_REACHED");

  response = await request("/api/v1/me/devices", "GET", undefined, firstBody.accessToken);
  const devices = (await responseBody(response)).devices as Array<{ id: string; deviceName: string }>;
  assert.equal(devices.filter((device) => device.deviceName === "installation-one" || device.deviceName === "installation-two").length, 2);
  const secondDevice = devices.find((device) => device.deviceName === "installation-two");
  assert.ok(secondDevice);
  assert.equal((await request(`/api/v1/me/devices/${secondDevice.id}`, "DELETE", undefined, firstBody.accessToken)).status, 200);
  response = await request("/api/v1/token/refresh", "POST", {
    refreshToken: secondBody.refreshToken, installationId: "installation-two",
  });
  assert.equal(response.status, 401);

  response = await request("/api/v1/password/forgot", "POST", { email });
  assert.equal(response.status, 202);
  const resetToken = tokenFromMail(mail, /重置/u);
  response = await request("/api/v1/password/reset", "POST", {
    token: resetToken, password: "New-valid-research-password-2026", confirm: "New-valid-research-password-2026",
  });
  assert.equal(response.status, 200);
  response = await request("/api/v1/token/refresh", "POST", {
    refreshToken: firstBody.refreshToken, installationId: "installation-one",
  });
  assert.equal(response.status, 401);

  response = await request(`/api/v1/admin/users/${user.id}/status`, "PATCH", { status: "suspended", reason: "测试停用" }, undefined, true);
  assert.equal(response.status, 200);
  response = await request(`/api/v1/admin/users/${user.id}/status`, "PATCH", { status: "active" }, undefined, true);
  assert.equal(response.status, 200);
  response = await request(`/api/v1/admin/users/${user.id}/send-password-reset`, "POST", {}, undefined, true);
  assert.equal(response.status, 200);

  response = await request(`/api/v1/admin/users/${user.id}`, "DELETE", { email, confirmation: "wrong" }, undefined, true);
  assert.equal(response.status, 400);
  response = await request(`/api/v1/admin/users/${user.id}`, "DELETE", { email, confirmation: "DELETE" }, undefined, true);
  assert.equal(response.status, 200);
  const deleted = db.database.prepare("select email, real_name, status from users where id = ?").get(user.id) as Record<string, string>;
  assert.equal(deleted.status, "deleted");
  assert.equal(deleted.real_name, "已删除用户");
  assert.notEqual(deleted.email, email);
  assert.equal(db.database.prepare("select count(*) as count from devices where user_id = ?").get(user.id)?.count, 0);
  assert.equal(db.database.prepare("select count(*) as count from audit_logs where target_id = ?").get(user.id)?.count !== 0, true);
});

test("admin API rejects requests without Cloudflare Access or local development token", async () => {
  const { request } = await fixture();
  assert.equal((await request("/api/v1/admin/users")).status, 401);
});

test("admin members alias supports browsers that block users URLs", async () => {
  const { request } = await fixture();
  const response = await request("/api/v1/admin/members", "GET", undefined, undefined, true);
  assert.equal(response.status, 200);
});

test("registration accepts an 8-character password and rejects shorter passwords", async () => {
  const { request } = await fixture();
  const registration = (email: string, password: string) => request("/api/v1/register", "POST", {
    email, realName: "测试成员", labRole: "硕士研究生", applicationNote: "密码长度边界测试", password,
  });

  const tooShort = await registration("short-password@example.test", "1234567");
  assert.equal(tooShort.status, 400);
  assert.equal((await responseBody(tooShort)).error.code, "WEAK_PASSWORD");

  const minimum = await registration("minimum-password@example.test", "12345678");
  assert.equal(minimum.status, 201);
});
