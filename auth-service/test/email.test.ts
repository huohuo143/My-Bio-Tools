import assert from "node:assert/strict";
import test from "node:test";
import { sendVerificationEmail } from "../src/email.ts";
import type { Env, UserRow } from "../src/types.ts";

const user = {
  email: "member@example.test",
  real_name: "测试成员",
} as UserRow;

test("Resend transport sends only the expected transactional email payload", async () => {
  const originalFetch = globalThis.fetch;
  let requestURL = "";
  let requestInit: RequestInit | undefined;
  globalThis.fetch = async (input, init) => {
    requestURL = String(input);
    requestInit = init;
    return new Response(JSON.stringify({ id: "test-message-id" }), { status: 200 });
  };

  try {
    await sendVerificationEmail({
      APP_ORIGIN: "https://auth.test",
      EMAIL_FROM: "My Bio Tools <noreply@example.test>",
      RESEND_API_KEY: "test-resend-key",
      ENVIRONMENT: "production",
    } as Env, user, "verification-token");
  } finally {
    globalThis.fetch = originalFetch;
  }

  assert.equal(requestURL, "https://api.resend.com/emails");
  assert.equal(requestInit?.method, "POST");
  const headers = new Headers(requestInit?.headers);
  assert.equal(headers.get("authorization"), "Bearer test-resend-key");
  const body = JSON.parse(String(requestInit?.body)) as Record<string, unknown>;
  assert.deepEqual(body.to, ["member@example.test"]);
  assert.equal(body.from, "My Bio Tools <noreply@example.test>");
  assert.match(String(body.text), /verify-email\?token=verification-token/u);
  assert.equal(Object.hasOwn(body, "applicationNote"), false);
});

test("Resend transport reports a stable status-only error", async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => new Response("provider detail must not be logged", { status: 429 });

  try {
    await assert.rejects(
      sendVerificationEmail({
        APP_ORIGIN: "https://auth.test",
        EMAIL_FROM: "My Bio Tools <noreply@example.test>",
        RESEND_API_KEY: "test-resend-key",
        ENVIRONMENT: "production",
      } as Env, user, "verification-token"),
      /HTTP 429/u,
    );
  } finally {
    globalThis.fetch = originalFetch;
  }
});
