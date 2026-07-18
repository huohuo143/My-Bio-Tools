import assert from "node:assert/strict";
import test from "node:test";
import {
  hashPassword,
  isValidEmail,
  normalizeEmail,
  parseEd25519Jwk,
  randomToken,
  signJWT,
  validateSigningConfiguration,
  verifyJWT,
} from "../src/crypto.ts";
import type { LicenseClaims } from "../src/types.ts";

test("password hashing is deterministic only for the same salt and pepper", async () => {
  const password = "A-valid-research-password-2026";
  const first = await hashPassword(password, "MDEyMzQ1Njc4OWFiY2RlZg", "test-pepper");
  const second = await hashPassword(password, "MDEyMzQ1Njc4OWFiY2RlZg", "test-pepper");
  const differentSalt = await hashPassword(password, "ZmVkY2JhOTg3NjU0MzIxMA", "test-pepper");
  assert.equal(first, second);
  assert.notEqual(first, differentSalt);
  assert.equal(first.length, 43);
});

test("Ed25519 offline license can be verified and rejects tampering", async () => {
  const pair = await crypto.subtle.generateKey({ name: "Ed25519" }, true, ["sign", "verify"]);
  const privateJwk = JSON.stringify(await crypto.subtle.exportKey("jwk", pair.privateKey));
  const publicJwk = JSON.stringify(await crypto.subtle.exportKey("jwk", pair.publicKey));
  const now = Math.floor(Date.now() / 1000);
  const claims: LicenseClaims = {
    typ: "offline-license", sub: "user-1", device: "device-hash",
    iat: now, exp: now + 60, version: 1,
    omics_key_b64: "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
  };
  const token = await signJWT(claims, privateJwk);
  assert.deepEqual(await verifyJWT<LicenseClaims>(token, publicJwk), claims);
  const parts = token.split(".");
  parts[1] = `${parts[1].slice(0, -1)}${parts[1].endsWith("A") ? "B" : "A"}`;
  await assert.rejects(() => verifyJWT(parts.join("."), publicJwk));
});

test("Node-exported Ed25519 JWK is normalized for Cloudflare Workers", async () => {
  const pair = await crypto.subtle.generateKey({ name: "Ed25519" }, true, ["sign", "verify"]);
  const exported = await crypto.subtle.exportKey("jwk", pair.privateKey);
  const legacy = JSON.stringify({ ...exported, alg: "Ed25519" });
  const normalized = parseEd25519Jwk(legacy);
  assert.equal(normalized.alg, undefined);
  assert.equal(normalized.kty, "OKP");
  assert.equal(normalized.crv, "Ed25519");
  assert.equal(normalized.d, exported.d);
  assert.equal(normalized.x, exported.x);
});

test("signing configuration validates a matching pair and rejects a mismatch", async () => {
  const first = await crypto.subtle.generateKey({ name: "Ed25519" }, true, ["sign", "verify"]);
  const second = await crypto.subtle.generateKey({ name: "Ed25519" }, true, ["sign", "verify"]);
  const privateJwk = JSON.stringify(await crypto.subtle.exportKey("jwk", first.privateKey));
  const publicJwk = JSON.stringify(await crypto.subtle.exportKey("jwk", first.publicKey));
  const wrongPublicJwk = JSON.stringify(await crypto.subtle.exportKey("jwk", second.publicKey));
  await validateSigningConfiguration(privateJwk, publicJwk);
  await assert.rejects(() => validateSigningConfiguration(privateJwk, wrongPublicJwk));
});

test("expired licenses are rejected", async () => {
  const pair = await crypto.subtle.generateKey({ name: "Ed25519" }, true, ["sign", "verify"]);
  const privateJwk = JSON.stringify(await crypto.subtle.exportKey("jwk", pair.privateKey));
  const publicJwk = JSON.stringify(await crypto.subtle.exportKey("jwk", pair.publicKey));
  const expired: LicenseClaims = {
    typ: "offline-license", sub: "user-1", device: "device-hash", iat: 1, exp: 2, version: 1,
    omics_key_b64: "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
  };
  await assert.rejects(() => signJWT(expired, privateJwk).then((token) => verifyJWT(token, publicJwk)), /Expired/u);
});

test("email normalization and token generation follow the public contract", () => {
  assert.equal(normalizeEmail("  Member@EXAMPLE.COM "), "member@example.com");
  assert.equal(isValidEmail("member@example.com"), true);
  assert.equal(isValidEmail("not-an-email"), false);
  assert.notEqual(randomToken(), randomToken());
});
