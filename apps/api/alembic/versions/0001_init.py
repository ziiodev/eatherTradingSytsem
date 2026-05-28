"""0001_init — initial schema.

CRITICAL: This migration MUST stay in sync with CHARTER.md
("Modelo de Datos: tabla `users`/`sessions`/`projects`/`agents`").
Any change to those sections MUST be reflected here and vice versa.
If you change this file, also update CHARTER.md, or this migration
diverges from the charter that future agents read as source of truth.

Tables are created in FK-dependency order:

    users  →  sessions       (sessions.user_id → users.id, ON DELETE CASCADE)
           →  agents         (agents.user_id   → users.id, ON DELETE RESTRICT)
           →  projects       (projects.user_id → users.id, ON DELETE RESTRICT)
                                projects.{worker,investigator,auditor}_agent_id
                                → agents.id, ON DELETE RESTRICT

Downgrade tears them down in reverse:

    projects  →  agents  →  sessions  →  users

pgcrypto is installed first so `gen_random_uuid()` is available as the
default for every UUID PK. CHARTER.md uses `gen_random_uuid()` literally
in every DDL block; do not switch to `uuid-ossp` without updating the
charter too.

Revision ID: 0001_init
Revises: None
Create Date: 2026-05-28
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

# Alembic identifiers.
revision: str = "0001_init"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# -----------------------------------------------------------------------------
# Upgrade
# -----------------------------------------------------------------------------
def upgrade() -> None:
    # ------------------------------------------------------------------
    # Extensions
    # ------------------------------------------------------------------
    # pgcrypto provides gen_random_uuid(). Postgres 16 ships it but the
    # extension still has to be CREATE'd in the target database — the
    # function is not registered until then. IF NOT EXISTS keeps this
    # idempotent for re-applied migrations.
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    # ------------------------------------------------------------------
    # users
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE users (
            id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),

            -- Identidad
            email               VARCHAR(255) UNIQUE NOT NULL,
            display_name        VARCHAR(100),
            avatar_url          TEXT,

            -- Credenciales (NULL si el usuario solo auth-ea por OAuth)
            password_hash       VARCHAR(255),

            -- Estado y roles
            is_active           BOOLEAN NOT NULL DEFAULT true,
            is_admin            BOOLEAN NOT NULL DEFAULT false,
            email_verified_at   TIMESTAMP,

            -- MFA (preparado para activacion posterior)
            mfa_enabled         BOOLEAN NOT NULL DEFAULT false,
            mfa_secret_ref      VARCHAR(255),

            -- Actividad
            last_login_at       TIMESTAMP,
            failed_login_count  INTEGER NOT NULL DEFAULT 0,
            locked_until        TIMESTAMP,

            -- Fechas
            created_at          TIMESTAMP DEFAULT NOW(),
            updated_at          TIMESTAMP DEFAULT NOW(),

            CONSTRAINT users_email_lower CHECK (email = LOWER(email))
        )
        """
    )
    op.execute(
        "CREATE INDEX idx_users_active ON users(id) WHERE is_active = true"
    )

    # ------------------------------------------------------------------
    # sessions
    # ------------------------------------------------------------------
    # ON DELETE CASCADE here (not RESTRICT): if a user is hard-deleted,
    # their sessions are meaningless and should disappear with them.
    op.execute(
        """
        CREATE TABLE sessions (
            id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id             UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,

            -- Token de refresco (NUNCA el JWT de acceso, NUNCA en plaintext)
            refresh_token_hash  VARCHAR(255) NOT NULL UNIQUE,

            -- Contexto del cliente
            ip_address          INET,
            user_agent          TEXT,

            -- Lifecycle
            issued_at           TIMESTAMP NOT NULL DEFAULT NOW(),
            expires_at          TIMESTAMP NOT NULL,
            last_used_at        TIMESTAMP NOT NULL DEFAULT NOW(),
            revoked_at          TIMESTAMP,

            CONSTRAINT sessions_expires_after_issued
                CHECK (expires_at > issued_at),
            CONSTRAINT sessions_revoked_after_issued
                CHECK (revoked_at IS NULL OR revoked_at >= issued_at)
        )
        """
    )
    op.execute(
        "CREATE INDEX idx_sessions_user_active "
        "ON sessions(user_id) WHERE revoked_at IS NULL"
    )
    op.execute(
        "CREATE INDEX idx_sessions_token_hash "
        "ON sessions(refresh_token_hash) WHERE revoked_at IS NULL"
    )

    # ------------------------------------------------------------------
    # agents
    # ------------------------------------------------------------------
    # Created BEFORE projects: projects.{worker,investigator,auditor}_agent_id
    # reference agents(id), so the target must exist first.
    #
    # ON DELETE RESTRICT from users: cannot drop a user that owns agents.
    # Soft-disable via users.is_active = false.
    op.execute(
        """
        CREATE TABLE agents (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id         UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,

            -- Identificacion
            name            VARCHAR(100) NOT NULL,
            type            VARCHAR(20) NOT NULL,
            description     TEXT,

            -- Logica ejecutable
            logica          TEXT NOT NULL,
            runtime         VARCHAR(20) NOT NULL DEFAULT 'python',
            entrypoint      VARCHAR(120),

            -- Versionado y estado
            version         INTEGER NOT NULL DEFAULT 1,
            is_active       BOOLEAN NOT NULL DEFAULT true,

            -- Fechas
            created_at      TIMESTAMP DEFAULT NOW(),
            updated_at      TIMESTAMP DEFAULT NOW(),

            CONSTRAINT agents_type_valid
                CHECK (type IN ('worker', 'investigator', 'auditor')),
            CONSTRAINT agents_runtime_only_python
                CHECK (runtime = 'python')
        )
        """
    )

    # ------------------------------------------------------------------
    # projects
    # ------------------------------------------------------------------
    # ON DELETE RESTRICT from users AND from agents: cannot drop a user
    # that owns projects, and cannot drop an agent referenced by any
    # project. Both are part of the multi-tenant isolation invariant.
    op.execute(
        """
        CREATE TABLE projects (
            id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id             UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,

            -- Informacion basica
            name                VARCHAR(100) NOT NULL,
            description         TEXT,
            symbol              VARCHAR(20) NOT NULL,
            timeframe           VARCHAR(10) NOT NULL,
            status              VARCHAR(20) NOT NULL DEFAULT 'inactive',

            -- Docker / Infraestructura
            container_id        VARCHAR(100),
            container_name      VARCHAR(80) UNIQUE,
            docker_image        VARCHAR(100) DEFAULT 'mt5-base:latest',
            mcp_url             VARCHAR(255) NOT NULL,
            mcp_port            INTEGER,

            -- Cuenta de trading
            account_login           VARCHAR(50),
            account_server          VARCHAR(100),
            broker_name             VARCHAR(80),
            account_credential_ref  VARCHAR(255),
            account_currency        VARCHAR(10),
            account_leverage        INTEGER,
            account_type            VARCHAR(20),

            -- Costes / Comisiones
            commission_per_lot      DECIMAL(10,4),
            commission_currency     VARCHAR(10),
            swap_long               DECIMAL(10,4),
            swap_short              DECIMAL(10,4),
            spread_typical          DECIMAL(8,2),

            -- Configuracion de riesgo
            capital_asignado    DECIMAL(15,2),
            risk_per_trade      DECIMAL(5,2) DEFAULT 1.0,
            max_daily_dd        DECIMAL(5,2) DEFAULT 3.0,
            max_total_dd        DECIMAL(5,2) DEFAULT 8.0,
            max_exposure        DECIMAL(5,2) DEFAULT 10.0,

            -- Estrategia
            strategy_version    INTEGER DEFAULT 1,
            strategy_description TEXT,
            base_logic          TEXT,

            -- Vinculacion a agentes
            worker_agent_id        UUID REFERENCES agents(id) ON DELETE RESTRICT,
            investigator_agent_id  UUID REFERENCES agents(id) ON DELETE RESTRICT,
            auditor_agent_id       UUID REFERENCES agents(id) ON DELETE RESTRICT,

            -- Ventanas operativas
            trading_sessions    TEXT[] NOT NULL DEFAULT '{}'
                CHECK (trading_sessions <@ ARRAY['sydney','shanghai','tokyo','europe','new_york']::text[]),

            -- Parametros por agente (JSONB)
            auditor_params       JSONB NOT NULL DEFAULT '{}'::jsonb,
            investigator_params  JSONB NOT NULL DEFAULT '{}'::jsonb,
            worker_params        JSONB NOT NULL DEFAULT '{}'::jsonb,

            -- Fechas y control
            created_at          TIMESTAMP DEFAULT NOW(),
            updated_at          TIMESTAMP DEFAULT NOW(),
            last_active_at      TIMESTAMP,
            last_sleep_at       TIMESTAMP,
            stopped_at          TIMESTAMP,

            -- Metadata
            tags                TEXT[],
            notes               TEXT,
            error_count         INTEGER DEFAULT 0,
            last_error          TEXT
        )
        """
    )


# -----------------------------------------------------------------------------
# Downgrade
# -----------------------------------------------------------------------------
def downgrade() -> None:
    # Reverse FK dependency order: projects → agents → sessions → users.
    # CASCADE on DROP is intentionally NOT used — if something else
    # depends on one of these tables (it shouldn't, this is the first
    # migration), we want the downgrade to fail loudly rather than
    # silently nuke unrelated objects.
    op.execute("DROP TABLE IF EXISTS projects")
    op.execute("DROP TABLE IF EXISTS agents")
    op.execute("DROP TABLE IF EXISTS sessions")
    op.execute("DROP TABLE IF EXISTS users")

    # Deliberately NOT dropping the pgcrypto extension. Extensions are
    # database-wide; another schema (or another future migration) may
    # rely on gen_random_uuid(). Dropping it on every downgrade is
    # noisy and rarely what an operator wants. If you ever truly need
    # to remove it, do so out-of-band:
    #     DROP EXTENSION pgcrypto;
