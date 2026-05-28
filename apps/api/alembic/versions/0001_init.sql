-- ----------------------------------------------------------------------------
-- 0001_init.sql — snapshot of the expected schema after `alembic upgrade head`
-- from a clean database.
--
-- Hand-verified against CHARTER.md ("Modelo de Datos: tabla `users`/`sessions`/
-- `projects`/`agents`") and against 0001_init.py.
--
-- This file is NOT executed by Alembic. It exists for human review:
--   * diff against CHARTER.md DDL to detect drift,
--   * paste into a fresh Postgres if you need the schema without installing
--     Alembic.
--
-- If you change 0001_init.py, regenerate / hand-update this file too.
-- ----------------------------------------------------------------------------

-- Extensions ------------------------------------------------------------------
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- users -----------------------------------------------------------------------
CREATE TABLE users (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    email               VARCHAR(255) UNIQUE NOT NULL,
    display_name        VARCHAR(100),
    avatar_url          TEXT,

    password_hash       VARCHAR(255),

    is_active           BOOLEAN NOT NULL DEFAULT true,
    is_admin            BOOLEAN NOT NULL DEFAULT false,
    email_verified_at   TIMESTAMP,

    mfa_enabled         BOOLEAN NOT NULL DEFAULT false,
    mfa_secret_ref      VARCHAR(255),

    last_login_at       TIMESTAMP,
    failed_login_count  INTEGER NOT NULL DEFAULT 0,
    locked_until        TIMESTAMP,

    created_at          TIMESTAMP DEFAULT NOW(),
    updated_at          TIMESTAMP DEFAULT NOW(),

    CONSTRAINT users_email_lower CHECK (email = LOWER(email))
);

CREATE INDEX idx_users_active ON users(id) WHERE is_active = true;

-- sessions --------------------------------------------------------------------
CREATE TABLE sessions (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,

    refresh_token_hash  VARCHAR(255) NOT NULL UNIQUE,

    ip_address          INET,
    user_agent          TEXT,

    issued_at           TIMESTAMP NOT NULL DEFAULT NOW(),
    expires_at          TIMESTAMP NOT NULL,
    last_used_at        TIMESTAMP NOT NULL DEFAULT NOW(),
    revoked_at          TIMESTAMP,

    CONSTRAINT sessions_expires_after_issued
        CHECK (expires_at > issued_at),
    CONSTRAINT sessions_revoked_after_issued
        CHECK (revoked_at IS NULL OR revoked_at >= issued_at)
);

CREATE INDEX idx_sessions_user_active
    ON sessions(user_id) WHERE revoked_at IS NULL;
CREATE INDEX idx_sessions_token_hash
    ON sessions(refresh_token_hash) WHERE revoked_at IS NULL;

-- agents ----------------------------------------------------------------------
CREATE TABLE agents (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,

    name            VARCHAR(100) NOT NULL,
    type            VARCHAR(20) NOT NULL,
    description     TEXT,

    logica          TEXT NOT NULL,
    runtime         VARCHAR(20) NOT NULL DEFAULT 'python',
    entrypoint      VARCHAR(120),

    version         INTEGER NOT NULL DEFAULT 1,
    is_active       BOOLEAN NOT NULL DEFAULT true,

    created_at      TIMESTAMP DEFAULT NOW(),
    updated_at      TIMESTAMP DEFAULT NOW(),

    CONSTRAINT agents_type_valid
        CHECK (type IN ('worker', 'investigator', 'auditor')),
    CONSTRAINT agents_runtime_only_python
        CHECK (runtime = 'python')
);

-- projects --------------------------------------------------------------------
CREATE TABLE projects (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,

    name                VARCHAR(100) NOT NULL,
    description         TEXT,
    symbol              VARCHAR(20) NOT NULL,
    timeframe           VARCHAR(10) NOT NULL,
    status              VARCHAR(20) NOT NULL DEFAULT 'inactive',

    container_id        VARCHAR(100),
    container_name      VARCHAR(80) UNIQUE,
    docker_image        VARCHAR(100) DEFAULT 'mt5-base:latest',
    mcp_url             VARCHAR(255) NOT NULL,
    mcp_port            INTEGER,

    account_login           VARCHAR(50),
    account_server          VARCHAR(100),
    broker_name             VARCHAR(80),
    account_credential_ref  VARCHAR(255),
    account_currency        VARCHAR(10),
    account_leverage        INTEGER,
    account_type            VARCHAR(20),

    commission_per_lot      DECIMAL(10,4),
    commission_currency     VARCHAR(10),
    swap_long               DECIMAL(10,4),
    swap_short              DECIMAL(10,4),
    spread_typical          DECIMAL(8,2),

    capital_asignado    DECIMAL(15,2),
    risk_per_trade      DECIMAL(5,2) DEFAULT 1.0,
    max_daily_dd        DECIMAL(5,2) DEFAULT 3.0,
    max_total_dd        DECIMAL(5,2) DEFAULT 8.0,
    max_exposure        DECIMAL(5,2) DEFAULT 10.0,

    strategy_version    INTEGER DEFAULT 1,
    strategy_description TEXT,
    base_logic          TEXT,

    worker_agent_id        UUID REFERENCES agents(id) ON DELETE RESTRICT,
    investigator_agent_id  UUID REFERENCES agents(id) ON DELETE RESTRICT,
    auditor_agent_id       UUID REFERENCES agents(id) ON DELETE RESTRICT,

    trading_sessions    TEXT[] NOT NULL DEFAULT '{}'
        CHECK (trading_sessions <@ ARRAY['sydney','shanghai','tokyo','europe','new_york']::text[]),

    auditor_params       JSONB NOT NULL DEFAULT '{}'::jsonb,
    investigator_params  JSONB NOT NULL DEFAULT '{}'::jsonb,
    worker_params        JSONB NOT NULL DEFAULT '{}'::jsonb,

    created_at          TIMESTAMP DEFAULT NOW(),
    updated_at          TIMESTAMP DEFAULT NOW(),
    last_active_at      TIMESTAMP,
    last_sleep_at       TIMESTAMP,
    stopped_at          TIMESTAMP,

    tags                TEXT[],
    notes               TEXT,
    error_count         INTEGER DEFAULT 0,
    last_error          TEXT
);
