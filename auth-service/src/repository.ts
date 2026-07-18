import type {
  D1Database,
  DeviceRow,
  SessionRow,
  UserRow,
  UserStatus,
} from "./types.ts";

export interface TokenRow {
  id: string;
  user_id: string;
  purpose: "verify_email" | "reset_password";
  token_hash: string;
  created_at: number;
  expires_at: number;
  used_at: number | null;
}

export class Repository {
  private readonly db: D1Database;

  constructor(db: D1Database) {
    this.db = db;
  }

  getUserByEmail(email: string): Promise<UserRow | null> {
    return this.db.prepare("SELECT * FROM users WHERE email = ? LIMIT 1").bind(email).first<UserRow>();
  }

  getUserById(id: string): Promise<UserRow | null> {
    return this.db.prepare("SELECT * FROM users WHERE id = ? LIMIT 1").bind(id).first<UserRow>();
  }

  async createUser(user: UserRow): Promise<void> {
    await this.db.prepare(`
      INSERT INTO users (
        id, email, real_name, lab_role, application_note, password_hash, password_salt,
        status, email_verified_at, reviewed_at, reviewed_by, review_reason,
        failed_attempts, locked_until, created_at, updated_at
      ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    `).bind(
      user.id, user.email, user.real_name, user.lab_role, user.application_note,
      user.password_hash, user.password_salt, user.status, user.email_verified_at,
      user.reviewed_at, user.reviewed_by, user.review_reason, user.failed_attempts,
      user.locked_until, user.created_at, user.updated_at,
    ).run();
  }

  async replaceOneTimeToken(
    userId: string,
    purpose: "verify_email" | "reset_password",
    tokenHash: string,
    now: number,
    expiresAt: number,
  ): Promise<void> {
    await this.db.batch([
      this.db.prepare("UPDATE one_time_tokens SET used_at = ? WHERE user_id = ? AND purpose = ? AND used_at IS NULL")
        .bind(now, userId, purpose),
      this.db.prepare(`
        INSERT INTO one_time_tokens (id, user_id, purpose, token_hash, created_at, expires_at, used_at)
        VALUES (?, ?, ?, ?, ?, ?, NULL)
      `).bind(crypto.randomUUID(), userId, purpose, tokenHash, now, expiresAt),
    ]);
  }

  getValidToken(tokenHash: string, purpose: TokenRow["purpose"], now: number): Promise<TokenRow | null> {
    return this.db.prepare(`
      SELECT * FROM one_time_tokens
      WHERE token_hash = ? AND purpose = ? AND used_at IS NULL AND expires_at > ?
      LIMIT 1
    `).bind(tokenHash, purpose, now).first<TokenRow>();
  }

  async markEmailVerified(token: TokenRow, now: number): Promise<boolean> {
    const result = await this.db.batch([
      this.db.prepare("UPDATE one_time_tokens SET used_at = ? WHERE id = ? AND used_at IS NULL")
        .bind(now, token.id),
      this.db.prepare(`
        UPDATE users SET status = 'pending', email_verified_at = ?, updated_at = ?
        WHERE id = ? AND status = 'unverified'
      `).bind(now, now, token.user_id),
    ]);
    return (result[0].meta.changes ?? 0) === 1 && (result[1].meta.changes ?? 0) === 1;
  }

  async resetPassword(
    token: TokenRow,
    passwordHash: string,
    passwordSalt: string,
    now: number,
  ): Promise<boolean> {
    const result = await this.db.batch([
      this.db.prepare("UPDATE one_time_tokens SET used_at = ? WHERE id = ? AND used_at IS NULL")
        .bind(now, token.id),
      this.db.prepare(`
        UPDATE users SET password_hash = ?, password_salt = ?, failed_attempts = 0,
          locked_until = NULL, updated_at = ? WHERE id = ? AND status != 'deleted'
      `).bind(passwordHash, passwordSalt, now, token.user_id),
      this.db.prepare("UPDATE sessions SET revoked_at = ? WHERE user_id = ? AND revoked_at IS NULL")
        .bind(now, token.user_id),
    ]);
    return (result[0].meta.changes ?? 0) === 1 && (result[1].meta.changes ?? 0) === 1;
  }

  async recordFailedLogin(user: UserRow, now: number): Promise<void> {
    const attempts = user.failed_attempts + 1;
    const lockedUntil = attempts >= 10 ? now + 15 * 60 : null;
    await this.db.prepare("UPDATE users SET failed_attempts = ?, locked_until = ?, updated_at = ? WHERE id = ?")
      .bind(attempts >= 10 ? 0 : attempts, lockedUntil, now, user.id).run();
  }

  async clearFailedLogin(userId: string, now: number): Promise<void> {
    await this.db.prepare("UPDATE users SET failed_attempts = 0, locked_until = NULL, updated_at = ? WHERE id = ?")
      .bind(now, userId).run();
  }

  getDevice(userId: string, installationHash: string): Promise<DeviceRow | null> {
    return this.db.prepare("SELECT * FROM devices WHERE user_id = ? AND installation_hash = ? LIMIT 1")
      .bind(userId, installationHash).first<DeviceRow>();
  }

  async bindDevice(input: {
    userId: string;
    installationHash: string;
    platform: "macos" | "windows";
    deviceName: string;
    appVersion: string;
    now: number;
  }): Promise<DeviceRow | null> {
    const existing = await this.getDevice(input.userId, input.installationHash);
    if (existing && existing.revoked_at === null) {
      await this.db.prepare(`
        UPDATE devices SET device_name = ?, app_version = ?, last_seen_at = ? WHERE id = ?
      `).bind(input.deviceName, input.appVersion, input.now, existing.id).run();
      return { ...existing, device_name: input.deviceName, app_version: input.appVersion, last_seen_at: input.now };
    }

    if (existing) {
      const reactivated = await this.db.prepare(`
        UPDATE devices SET revoked_at = NULL, device_name = ?, app_version = ?, last_seen_at = ?
        WHERE id = ? AND (SELECT COUNT(*) FROM devices WHERE user_id = ? AND revoked_at IS NULL) < 2
      `).bind(input.deviceName, input.appVersion, input.now, existing.id, input.userId).run();
      if ((reactivated.meta.changes ?? 0) !== 1) return null;
      return this.getDevice(input.userId, input.installationHash);
    }

    const id = crypto.randomUUID();
    const inserted = await this.db.prepare(`
      INSERT INTO devices (
        id, user_id, installation_hash, platform, device_name, app_version,
        first_seen_at, last_seen_at, revoked_at
      )
      SELECT ?, ?, ?, ?, ?, ?, ?, ?, NULL
      WHERE (SELECT COUNT(*) FROM devices WHERE user_id = ? AND revoked_at IS NULL) < 2
    `).bind(
      id, input.userId, input.installationHash, input.platform, input.deviceName,
      input.appVersion, input.now, input.now, input.userId,
    ).run();
    if ((inserted.meta.changes ?? 0) !== 1) return null;
    return this.getDevice(input.userId, input.installationHash);
  }

  async listDevices(userId: string): Promise<DeviceRow[]> {
    const result = await this.db.prepare(`
      SELECT * FROM devices WHERE user_id = ? ORDER BY revoked_at IS NULL DESC, last_seen_at DESC
    `).bind(userId).all<DeviceRow>();
    return result.results ?? [];
  }

  async revokeDevice(userId: string, deviceId: string, now: number): Promise<boolean> {
    const result = await this.db.batch([
      this.db.prepare("UPDATE devices SET revoked_at = ? WHERE id = ? AND user_id = ? AND revoked_at IS NULL")
        .bind(now, deviceId, userId),
      this.db.prepare("UPDATE sessions SET revoked_at = ? WHERE device_id = ? AND user_id = ? AND revoked_at IS NULL")
        .bind(now, deviceId, userId),
    ]);
    return (result[0].meta.changes ?? 0) === 1;
  }

  async createSession(userId: string, deviceId: string, refreshHash: string, now: number): Promise<SessionRow> {
    const session: SessionRow = {
      id: crypto.randomUUID(), user_id: userId, device_id: deviceId, refresh_hash: refreshHash,
      created_at: now, last_seen_at: now, expires_at: now + 30 * 24 * 60 * 60, revoked_at: null,
    };
    await this.db.prepare(`
      INSERT INTO sessions (id, user_id, device_id, refresh_hash, created_at, last_seen_at, expires_at, revoked_at)
      VALUES (?, ?, ?, ?, ?, ?, ?, NULL)
    `).bind(
      session.id, session.user_id, session.device_id, session.refresh_hash,
      session.created_at, session.last_seen_at, session.expires_at,
    ).run();
    return session;
  }

  getSessionByRefresh(refreshHash: string, now: number): Promise<SessionRow | null> {
    return this.db.prepare(`
      SELECT * FROM sessions WHERE refresh_hash = ? AND revoked_at IS NULL AND expires_at > ? LIMIT 1
    `).bind(refreshHash, now).first<SessionRow>();
  }

  getActiveSession(id: string, now: number): Promise<SessionRow | null> {
    return this.db.prepare(`
      SELECT * FROM sessions WHERE id = ? AND revoked_at IS NULL AND expires_at > ? LIMIT 1
    `).bind(id, now).first<SessionRow>();
  }

  async rotateSession(id: string, oldHash: string, newHash: string, now: number): Promise<boolean> {
    const result = await this.db.prepare(`
      UPDATE sessions SET refresh_hash = ?, last_seen_at = ?
      WHERE id = ? AND refresh_hash = ? AND revoked_at IS NULL AND expires_at > ?
    `).bind(newHash, now, id, oldHash, now).run();
    return (result.meta.changes ?? 0) === 1;
  }

  async revokeSession(id: string, now: number): Promise<void> {
    await this.db.prepare("UPDATE sessions SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL")
      .bind(now, id).run();
  }

  async revokeAllSessions(userId: string, now: number): Promise<void> {
    await this.db.prepare("UPDATE sessions SET revoked_at = ? WHERE user_id = ? AND revoked_at IS NULL")
      .bind(now, userId).run();
  }

  async listUsers(status: string, query: string): Promise<UserRow[]> {
    const where: string[] = ["status != 'deleted'"];
    const values: unknown[] = [];
    if (status !== "all") { where.push("status = ?"); values.push(status); }
    if (query) {
      where.push("(email LIKE ? OR real_name LIKE ?)");
      const pattern = `%${query.replaceAll("%", "\\%").replaceAll("_", "\\_")}%`;
      values.push(pattern, pattern);
    }
    const result = await this.db.prepare(`
      SELECT * FROM users WHERE ${where.join(" AND ")} ORDER BY created_at DESC LIMIT 200
    `).bind(...values).all<UserRow>();
    return result.results ?? [];
  }

  async userStatusCounts(): Promise<Record<string, number>> {
    const result = await this.db.prepare(`
      SELECT status, COUNT(*) AS count FROM users WHERE status != 'deleted' GROUP BY status
    `).all<{ status: string; count: number }>();
    return Object.fromEntries((result.results ?? []).map((row) => [row.status, Number(row.count)]));
  }

  async setUserStatus(
    userId: string,
    status: Extract<UserStatus, "active" | "rejected" | "suspended">,
    reason: string,
    adminEmail: string,
    now: number,
  ): Promise<boolean> {
    const result = await this.db.prepare(`
      UPDATE users SET status = ?, review_reason = ?, reviewed_at = ?, reviewed_by = ?, updated_at = ?
      WHERE id = ? AND status != 'deleted' AND email_verified_at IS NOT NULL
    `).bind(status, reason, now, adminEmail, now, userId).run();
    if ((result.meta.changes ?? 0) === 1 && status !== "active") await this.revokeAllSessions(userId, now);
    return (result.meta.changes ?? 0) === 1;
  }

  async permanentlyDeleteUser(userId: string, now: number): Promise<boolean> {
    const anonymizedEmail = `deleted+${userId}@invalid.local`;
    const result = await this.db.batch([
      this.db.prepare("DELETE FROM sessions WHERE user_id = ?").bind(userId),
      this.db.prepare("DELETE FROM devices WHERE user_id = ?").bind(userId),
      this.db.prepare("DELETE FROM one_time_tokens WHERE user_id = ?").bind(userId),
      this.db.prepare(`
        UPDATE users SET email = ?, real_name = '已删除用户', lab_role = '', application_note = '',
          password_hash = '', password_salt = '', status = 'deleted', email_verified_at = NULL,
          reviewed_at = ?, reviewed_by = NULL, review_reason = NULL, failed_attempts = 0,
          locked_until = NULL, updated_at = ?
        WHERE id = ? AND status != 'deleted'
      `).bind(anonymizedEmail, now, now, userId),
    ]);
    return (result[3].meta.changes ?? 0) === 1;
  }

  async listAuditLogs(): Promise<Record<string, unknown>[]> {
    const result = await this.db.prepare("SELECT * FROM audit_logs ORDER BY created_at DESC LIMIT 300")
      .all<Record<string, unknown>>();
    return result.results ?? [];
  }

  async audit(input: {
    actorType: "user" | "admin" | "system";
    actorId?: string;
    action: string;
    targetType: string;
    targetId?: string;
    metadata?: Record<string, unknown>;
    sourceHash?: string;
    now: number;
  }): Promise<void> {
    await this.db.prepare(`
      INSERT INTO audit_logs (
        id, actor_type, actor_id, action, target_type, target_id, metadata_json, source_hash, created_at
      ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    `).bind(
      crypto.randomUUID(), input.actorType, input.actorId ?? null, input.action,
      input.targetType, input.targetId ?? null, JSON.stringify(input.metadata ?? {}),
      input.sourceHash ?? null, input.now,
    ).run();
  }
}
