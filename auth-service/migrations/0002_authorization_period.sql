ALTER TABLE users ADD COLUMN authorization_expires_at INTEGER
    CHECK (authorization_expires_at IS NULL OR authorization_expires_at > 0);

CREATE INDEX users_authorization_expiry_idx
    ON users(status, authorization_expires_at);
