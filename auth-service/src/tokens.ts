import { sha256, signJWT, verifyJWT } from "./crypto.ts";
import { Repository } from "./repository.ts";
import type { AccessClaims, DeviceRow, Env, LicenseClaims, SessionRow, UserRow } from "./types.ts";

export interface AuthContext {
  user: UserRow;
  device: DeviceRow;
  session: SessionRow;
  claims: AccessClaims;
}

export function validatedOmicsDatabaseKey(value: string): string {
  const normalized = value.trim();
  try {
    const decoded = Uint8Array.from(atob(normalized), (character) => character.charCodeAt(0));
    if (decoded.length !== 32) throw new Error("wrong length");
  } catch {
    throw new Error("OMICS_DATABASE_KEY_B64 must be a 32-byte base64 key");
  }
  return normalized;
}

export async function issueTokens(
  env: Env,
  session: SessionRow,
  device: DeviceRow,
  refreshToken: string,
  now: number,
): Promise<Record<string, unknown>> {
  const accessClaims: AccessClaims = {
    typ: "access", sub: session.user_id, sid: session.id, device: device.installation_hash,
    iat: now, exp: now + 15 * 60,
  };
  const licenseClaims: LicenseClaims = {
    typ: "offline-license", sub: session.user_id, device: device.installation_hash,
    iat: now, exp: now + 7 * 24 * 60 * 60, version: 1,
    omics_key_b64: validatedOmicsDatabaseKey(env.OMICS_DATABASE_KEY_B64),
  };
  return {
    accessToken: await signJWT(accessClaims, env.LICENSE_PRIVATE_JWK),
    accessExpiresAt: accessClaims.exp,
    refreshToken,
    refreshExpiresAt: session.expires_at,
    offlineLicense: await signJWT(licenseClaims, env.LICENSE_PRIVATE_JWK),
    offlineLicenseExpiresAt: licenseClaims.exp,
    serverTime: now,
  };
}

export async function authenticateAccess(token: string, env: Env): Promise<AuthContext> {
  const claims = await verifyJWT<AccessClaims>(token, env.LICENSE_PUBLIC_JWK);
  if (claims.typ !== "access") throw new Error("Wrong token type");
  const now = Math.floor(Date.now() / 1000);
  const repository = new Repository(env.DB);
  const session = await repository.getActiveSession(claims.sid, now);
  if (!session || session.user_id !== claims.sub) throw new Error("Session revoked");
  const [user, devices] = await Promise.all([
    repository.getUserById(session.user_id),
    repository.listDevices(session.user_id),
  ]);
  const device = devices.find((candidate) => candidate.id === session.device_id) ?? null;
  if (!user || user.status !== "active" || !device || device.revoked_at !== null) {
    throw new Error("Account or device revoked");
  }
  if (device.installation_hash !== claims.device) throw new Error("Device mismatch");
  return { user, device, session, claims };
}

export async function installationHash(installationId: string): Promise<string> {
  return sha256(`my-bio-tools-installation:${installationId}`);
}
