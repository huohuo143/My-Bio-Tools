export type UserStatus =
  | "unverified"
  | "pending"
  | "active"
  | "rejected"
  | "suspended"
  | "deleted";

export interface D1Result<T = Record<string, unknown>> {
  success: boolean;
  meta: { changes?: number };
  results?: T[];
}

export interface D1Statement {
  bind(...values: unknown[]): D1Statement;
  first<T = Record<string, unknown>>(): Promise<T | null>;
  all<T = Record<string, unknown>>(): Promise<D1Result<T>>;
  run<T = Record<string, unknown>>(): Promise<D1Result<T>>;
}

export interface D1Database {
  prepare(query: string): D1Statement;
  batch(statements: D1Statement[]): Promise<D1Result[]>;
}

export interface EmailSender {
  send(message: {
    to: string | { email: string; name?: string };
    from: string | { email: string; name?: string };
    subject: string;
    html?: string;
    text?: string;
  }): Promise<{ messageId: string }>;
}

export interface RateLimitBinding {
  limit(input: { key: string }): Promise<{ success: boolean }>;
}

export interface Env {
  DB: D1Database;
  EMAIL_TEST_SENDER?: EmailSender;
  AUTH_RATE_LIMITER?: RateLimitBinding;
  APP_ORIGIN: string;
  EMAIL_FROM: string;
  RESEND_API_KEY: string;
  ENVIRONMENT: string;
  ACCESS_TEAM_DOMAIN: string;
  ACCESS_AUD: string;
  ADMIN_EMAILS: string;
  ADMIN_NOTIFICATION_EMAIL: string;
  PASSWORD_PEPPER: string;
  LICENSE_PRIVATE_JWK: string;
  LICENSE_PUBLIC_JWK: string;
  OMICS_DATABASE_KEY_B64: string;
  IP_HASH_SALT: string;
  DEV_ADMIN_TOKEN?: string;
  UPDATE_MANIFEST_JSON: string;
  GITHUB_RELEASES_TOKEN: string;
  GITHUB_TEST_FETCH?: typeof fetch;
}

export interface UserRow {
  id: string;
  email: string;
  real_name: string;
  lab_role: string;
  application_note: string;
  password_hash: string;
  password_salt: string;
  status: UserStatus;
  email_verified_at: number | null;
  reviewed_at: number | null;
  reviewed_by: string | null;
  review_reason: string | null;
  authorization_expires_at: number | null;
  failed_attempts: number;
  locked_until: number | null;
  created_at: number;
  updated_at: number;
}

export interface DeviceRow {
  id: string;
  user_id: string;
  installation_hash: string;
  platform: "macos" | "windows";
  device_name: string;
  app_version: string;
  first_seen_at: number;
  last_seen_at: number;
  revoked_at: number | null;
}

export interface SessionRow {
  id: string;
  user_id: string;
  device_id: string;
  refresh_hash: string;
  created_at: number;
  last_seen_at: number;
  expires_at: number;
  revoked_at: number | null;
}

export interface AccessClaims {
  typ: "access";
  sub: string;
  sid: string;
  device: string;
  iat: number;
  exp: number;
}

export interface LicenseClaims {
  typ: "offline-license";
  sub: string;
  device: string;
  iat: number;
  exp: number;
  version: 1;
  omics_key_b64: string;
}

export interface UpdateManifestClaims {
  typ: "app-update";
  iat: number;
  exp: number;
  schema_version: 1;
  platform: "macos-arm64";
  bundle_identifier: "top.aizs.my-bio-tools";
  app_version: string;
  build: number;
  minimum_system_version: string;
  size: number;
  sha256: string;
  release_source: "github";
  github_repository: string;
  github_asset_id: number;
  release_notes: string;
  published_at: string;
  mandatory: boolean;
}
