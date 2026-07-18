PRAGMA foreign_keys = ON;

CREATE TABLE users (
    id TEXT PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    real_name TEXT NOT NULL,
    lab_role TEXT NOT NULL,
    application_note TEXT NOT NULL DEFAULT '',
    password_hash TEXT NOT NULL,
    password_salt TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('unverified', 'pending', 'active', 'rejected', 'suspended', 'deleted')),
    email_verified_at INTEGER,
    reviewed_at INTEGER,
    reviewed_by TEXT,
    review_reason TEXT,
    failed_attempts INTEGER NOT NULL DEFAULT 0,
    locked_until INTEGER,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
) STRICT;

CREATE INDEX users_status_created_idx ON users(status, created_at DESC);

CREATE TABLE devices (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    installation_hash TEXT NOT NULL,
    platform TEXT NOT NULL CHECK (platform IN ('macos', 'windows')),
    device_name TEXT NOT NULL,
    app_version TEXT NOT NULL,
    first_seen_at INTEGER NOT NULL,
    last_seen_at INTEGER NOT NULL,
    revoked_at INTEGER,
    UNIQUE(user_id, installation_hash)
) STRICT;

CREATE INDEX devices_user_active_idx ON devices(user_id, revoked_at, last_seen_at DESC);

CREATE TABLE sessions (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    device_id TEXT NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    refresh_hash TEXT NOT NULL UNIQUE,
    created_at INTEGER NOT NULL,
    last_seen_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    revoked_at INTEGER
) STRICT;

CREATE INDEX sessions_user_active_idx ON sessions(user_id, revoked_at, expires_at);
CREATE INDEX sessions_device_active_idx ON sessions(device_id, revoked_at, expires_at);

CREATE TABLE one_time_tokens (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    purpose TEXT NOT NULL CHECK (purpose IN ('verify_email', 'reset_password')),
    token_hash TEXT NOT NULL UNIQUE,
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    used_at INTEGER
) STRICT;

CREATE INDEX one_time_tokens_lookup_idx ON one_time_tokens(token_hash, purpose, expires_at);

CREATE TABLE audit_logs (
    id TEXT PRIMARY KEY,
    actor_type TEXT NOT NULL CHECK (actor_type IN ('user', 'admin', 'system')),
    actor_id TEXT,
    action TEXT NOT NULL,
    target_type TEXT NOT NULL,
    target_id TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    source_hash TEXT,
    created_at INTEGER NOT NULL
) STRICT;

CREATE INDEX audit_logs_created_idx ON audit_logs(created_at DESC);
CREATE INDEX audit_logs_target_idx ON audit_logs(target_type, target_id, created_at DESC);
