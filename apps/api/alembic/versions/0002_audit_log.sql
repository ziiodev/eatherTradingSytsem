-- ----------------------------------------------------------------------------
-- 0002_audit_log.sql — snapshot of the delta applied by 0002_audit_log.py.
--
-- Hand-verified against the spec (#1979) and against the upgrade() body
-- in 0002_audit_log.py. NOT executed by Alembic; exists for human review.
--
-- If you change 0002_audit_log.py, regenerate / hand-update this file too.
-- ----------------------------------------------------------------------------

-- audit_log ------------------------------------------------------------------
CREATE TABLE audit_log (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- NULLABLE: system actions (no human caller) still want a row.
    -- RESTRICT prevents hard-deleting a user that has audit history.
    user_id         UUID REFERENCES users(id) ON DELETE RESTRICT,

    -- Action verb (e.g. "project.update", "agent.create", "auth.login").
    action          VARCHAR(100) NOT NULL,

    -- Domain object touched (e.g. "project", "agent", "user", "session").
    target_type     VARCHAR(50) NOT NULL,
    target_id       UUID,

    -- JSONB snapshots — callers MUST PII-scrub before writing.
    before_state    JSONB,
    after_state     JSONB,

    -- Request context, NULL for non-HTTP entrypoints.
    ip_address      INET,
    user_agent      TEXT,

    created_at      TIMESTAMP DEFAULT NOW()
);

-- Indexes mirroring the dashboard query patterns.
CREATE INDEX idx_audit_log_user_created
    ON audit_log(user_id, created_at DESC);
CREATE INDEX idx_audit_log_action_created
    ON audit_log(action, created_at DESC);

-- Append-only grants — guarded by role-existence so the migration is a
-- no-op when the aether role is absent (CI / superuser containers).
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'aether') THEN
        EXECUTE 'GRANT INSERT, SELECT ON audit_log TO aether';
        EXECUTE 'REVOKE UPDATE, DELETE ON audit_log FROM aether';
    END IF;
END
$$;
