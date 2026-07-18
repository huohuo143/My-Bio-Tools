import { base64UrlToBytes } from "./crypto.ts";
import type { Env } from "./types.ts";

interface AccessHeader { alg: string; kid: string }
interface AccessClaims { aud: string[] | string; email: string; exp: number; iss: string }
interface JwksResponse { keys: JsonWebKey[] }

const encoder = new TextEncoder();
const decoder = new TextDecoder();
let cachedJwks: { expiresAt: number; keys: JsonWebKey[] } | undefined;

async function getKeys(teamDomain: string): Promise<JsonWebKey[]> {
  if (cachedJwks && cachedJwks.expiresAt > Date.now()) return cachedJwks.keys;
  const response = await fetch(`https://${teamDomain}/cdn-cgi/access/certs`);
  if (!response.ok) throw new Error("Unable to load Cloudflare Access signing keys");
  const body = await response.json<JwksResponse>();
  cachedJwks = { expiresAt: Date.now() + 60 * 60 * 1000, keys: body.keys };
  return body.keys;
}

export async function requireAdmin(request: Request, env: Env): Promise<string> {
  if (
    env.ENVIRONMENT !== "production" &&
    env.DEV_ADMIN_TOKEN &&
    request.headers.get("x-dev-admin-token") === env.DEV_ADMIN_TOKEN
  ) {
    return "local-development-admin";
  }

  const token = request.headers.get("Cf-Access-Jwt-Assertion");
  if (!token) throw new Response("Cloudflare Access authentication required", { status: 401 });
  const parts = token.split(".");
  if (parts.length !== 3) throw new Response("Invalid Access token", { status: 401 });

  const header = JSON.parse(decoder.decode(base64UrlToBytes(parts[0]))) as AccessHeader;
  const claims = JSON.parse(decoder.decode(base64UrlToBytes(parts[1]))) as AccessClaims;
  const key = (await getKeys(env.ACCESS_TEAM_DOMAIN)).find((candidate) => candidate.kid === header.kid);
  if (!key || header.alg !== "RS256") throw new Response("Invalid Access signing key", { status: 401 });

  const publicKey = await crypto.subtle.importKey(
    "jwk",
    key,
    { name: "RSASSA-PKCS1-v1_5", hash: "SHA-256" },
    false,
    ["verify"],
  );
  const verified = await crypto.subtle.verify(
    "RSASSA-PKCS1-v1_5",
    publicKey,
    base64UrlToBytes(parts[2]),
    encoder.encode(`${parts[0]}.${parts[1]}`),
  );
  const audience = Array.isArray(claims.aud) ? claims.aud : [claims.aud];
  const allowed = new Set(env.ADMIN_EMAILS.split(",").map((email) => email.trim().toLowerCase()));
  const expectedIssuer = `https://${env.ACCESS_TEAM_DOMAIN}`;
  if (
    !verified ||
    claims.exp <= Math.floor(Date.now() / 1000) ||
    claims.iss !== expectedIssuer ||
    !audience.includes(env.ACCESS_AUD) ||
    !allowed.has(claims.email.toLowerCase())
  ) {
    throw new Response("Administrator access denied", { status: 403 });
  }
  return claims.email.toLowerCase();
}
