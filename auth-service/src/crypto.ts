import type { AccessClaims, LicenseClaims, UpdateManifestClaims } from "./types.ts";
import { scrypt as nodeScrypt, timingSafeEqual } from "node:crypto";

export const SCRYPT_PARAMETERS = Object.freeze({
  N: 32_768,
  r: 8,
  p: 2,
  maxmem: 64 * 1024 * 1024,
});
const encoder = new TextEncoder();
const decoder = new TextDecoder();

export function bytesToBase64Url(bytes: ArrayBuffer | Uint8Array): string {
  const view = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes);
  let binary = "";
  for (const value of view) binary += String.fromCharCode(value);
  return btoa(binary).replaceAll("+", "-").replaceAll("/", "_").replace(/=+$/u, "");
}

export function base64UrlToBytes(value: string): Uint8Array {
  const padding = "=".repeat((4 - (value.length % 4)) % 4);
  const binary = atob(value.replaceAll("-", "+").replaceAll("_", "/") + padding);
  return Uint8Array.from(binary, (character) => character.charCodeAt(0));
}

export function randomToken(byteCount = 32): string {
  const bytes = new Uint8Array(byteCount);
  crypto.getRandomValues(bytes);
  return bytesToBase64Url(bytes);
}

export async function sha256(value: string): Promise<string> {
  return bytesToBase64Url(await crypto.subtle.digest("SHA-256", encoder.encode(value)));
}

async function pepperPassword(password: string, pepper: string): Promise<ArrayBuffer> {
  const key = await crypto.subtle.importKey(
    "raw",
    encoder.encode(pepper),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  return crypto.subtle.sign("HMAC", key, encoder.encode(password));
}

export async function hashPassword(password: string, salt: string, pepper: string): Promise<string> {
  const peppered = await pepperPassword(password, pepper);
  const derived = await new Promise<Uint8Array>((resolve, reject) => {
    nodeScrypt(
      new Uint8Array(peppered),
      base64UrlToBytes(salt),
      32,
      SCRYPT_PARAMETERS,
      (caught, key) => caught ? reject(caught) : resolve(key),
    );
  });
  return bytesToBase64Url(derived);
}

export async function verifyPassword(
  password: string,
  salt: string,
  expected: string,
  pepper: string,
): Promise<boolean> {
  const actual = base64UrlToBytes(await hashPassword(password, salt, pepper));
  const wanted = base64UrlToBytes(expected);
  if (actual.length !== wanted.length) return false;
  return timingSafeEqual(actual, wanted);
}

export function parseEd25519Jwk(value: string): JsonWebKey {
  const parsed = JSON.parse(value) as JsonWebKey;
  if (!parsed.kty) throw new Error("JWK is missing kty");
  // Node's WebCrypto exporter currently emits alg="Ed25519". Cloudflare
  // Workers implements the standards-based Ed25519 algorithm and rejects that
  // non-standard JWK alg value with DataError. The alg member is optional, so
  // remove it while preserving the key material and intended key usages.
  if (parsed.alg === "Ed25519") delete parsed.alg;
  return parsed;
}

async function importPrivateSigningKey(jwkJSON: string): Promise<CryptoKey> {
  return crypto.subtle.importKey("jwk", parseEd25519Jwk(jwkJSON), { name: "Ed25519" }, false, ["sign"]);
}

async function importPublicSigningKey(jwkJSON: string): Promise<CryptoKey> {
  return crypto.subtle.importKey("jwk", parseEd25519Jwk(jwkJSON), { name: "Ed25519" }, false, ["verify"]);
}

export async function signJWT(payload: AccessClaims | LicenseClaims | UpdateManifestClaims, privateJwk: string): Promise<string> {
  const header = bytesToBase64Url(encoder.encode(JSON.stringify({ alg: "EdDSA", typ: "JWT" })));
  const body = bytesToBase64Url(encoder.encode(JSON.stringify(payload)));
  const signingInput = `${header}.${body}`;
  const signature = await crypto.subtle.sign(
    { name: "Ed25519" },
    await importPrivateSigningKey(privateJwk),
    encoder.encode(signingInput),
  );
  return `${signingInput}.${bytesToBase64Url(signature)}`;
}

export async function verifyJWT<T extends AccessClaims | LicenseClaims | UpdateManifestClaims>(
  token: string,
  publicJwk: string,
): Promise<T> {
  const parts = token.split(".");
  if (parts.length !== 3) throw new Error("Malformed token");
  const valid = await crypto.subtle.verify(
    { name: "Ed25519" },
    await importPublicSigningKey(publicJwk),
    base64UrlToBytes(parts[2]),
    encoder.encode(`${parts[0]}.${parts[1]}`),
  );
  if (!valid) throw new Error("Invalid token signature");
  const claims = JSON.parse(decoder.decode(base64UrlToBytes(parts[1]))) as T;
  if (!claims.exp || claims.exp <= Math.floor(Date.now() / 1000)) throw new Error("Expired token");
  return claims;
}

export async function validateSigningConfiguration(privateJwk: string, publicJwk: string): Promise<void> {
  const now = Math.floor(Date.now() / 1000);
  const probe: LicenseClaims = {
    typ: "offline-license",
    sub: "signing-health-check",
    device: "signing-health-check",
    iat: now,
    exp: now + 60,
    version: 1,
    omics_key_b64: "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
  };
  const verified = await verifyJWT<LicenseClaims>(await signJWT(probe, privateJwk), publicJwk);
  if (verified.sub !== probe.sub || verified.device !== probe.device) {
    throw new Error("Signing key pair validation failed");
  }
}

export function normalizeEmail(value: string): string {
  return value.trim().toLocaleLowerCase("en-US");
}

export function isValidEmail(value: string): boolean {
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/u.test(value) && value.length <= 254;
}

export function escapeHTML(value: string): string {
  return value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}
