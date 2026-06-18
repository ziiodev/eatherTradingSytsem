"""0001_init — cumulative initial schema (accounts-pairs hierarchy).

SQUASH MIGRATION. This single migration replaces the former linear chain
``0001_init`` → … → ``0014_chat`` (now deleted) and re-authors the full
cumulative schema with the new hierarchy applied:

    Exchange  →  Account (Cuenta)  →  Pair (Par)  →  Agents

The rename + reparent applied versus the pre-squash schema:

* ``projects``  →  ``pairs`` (adds ``account_id`` FK RESTRICT; KEEPS the
  denormalised ``user_id NOT NULL``; the 7 broker-credential columns are
  LIFTED OFF the pair onto the new ``accounts`` table).
* ``project_id``  →  ``pair_id`` on every dependent table that carried it
  (agent_runs, container_events, orders, order_log, order_approvals,
  sleep_runs, config_versions, q_tables, episodic_memory, semantic_memory,
  chat_conversations, chat_action_proposals). Each FK keeps its ORIGINAL
  ``ON DELETE`` semantics — RESTRICT for the audit-trail tables, CASCADE for
  the learning / chat tables. Renamed constraints follow suit
  (e.g. ``uq_q_tables_pair_version``).
* two NEW parent tables: ``exchanges`` (first-class venue, user-scoped) and
  ``accounts`` (grouping layer, owns the broker-credential block, FK
  ``exchange_id`` RESTRICT).

This migration is BYTE-FAITHFUL to the cumulative state of the old
0001→0014 chain (modulo the rename/reparent above): the 6-value agents.type
CHECK (0010 + 0012), the 8-value orders.status CHECK (0013), the 4-value
sleep_reflections.agent_type CHECK incl. ``orchestrator`` (0011), the orders
operativa extension (0013), the learning-loop tables (0011), and the chat
plane (0014) are all folded in.

Tables are created in FK-dependency order:

    users → sessions → agents → skills
          → exchanges → accounts → pairs
          → audit_log, mfa_recovery_codes, agent_skills, agent_runs,
            container_events, orders → order_log / order_approvals,
            sleep_runs → sleep_reflections / config_versions,
            q_tables, episodic_memory, semantic_memory, sleep_reports,
            chat_conversations → chat_messages → chat_action_proposals

Downgrade tears everything down in reverse FK order.

pgcrypto is installed first so ``gen_random_uuid()`` is available as the
default for every UUID PK.

Revision ID: 0001_init
Revises: None
Create Date: 2026-06-18
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
    op.execute(
        """
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
    # Created BEFORE pairs: pairs.{...}_agent_id reference agents(id).
    # agents.type CHECK carries the full 6-value set (folds in 0010's
    # 'orchestrator' and 0012's 'marker'/'tutor').
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
                CHECK (type IN ('orchestrator', 'investigator', 'marker',
                                'worker', 'tutor', 'auditor')),
            CONSTRAINT agents_runtime_only_python
                CHECK (runtime = 'python')
        )
        """
    )

    # ------------------------------------------------------------------
    # skills  (markdown-by-default — folds in 0009 relaxation)
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE skills (
            id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id             UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,

            name                VARCHAR(100) NOT NULL,
            type                VARCHAR(20)  NOT NULL,
            description         TEXT,

            version             INTEGER NOT NULL DEFAULT 1,

            code                TEXT    NOT NULL,
            runtime             VARCHAR(20) NOT NULL DEFAULT 'markdown',

            input_signature     JSONB NOT NULL DEFAULT '{}'::jsonb,
            output_signature    JSONB NOT NULL DEFAULT '{}'::jsonb,

            is_active           BOOLEAN NOT NULL DEFAULT true,

            created_at          TIMESTAMP DEFAULT NOW(),
            updated_at          TIMESTAMP DEFAULT NOW(),

            CONSTRAINT skills_type_valid
                CHECK (type IN ('indicator', 'data_source', 'analytic', 'executor', 'risk')),
            CONSTRAINT skills_runtime_valid
                CHECK (runtime IN ('markdown', 'python'))
        )
        """
    )
    op.execute(
        "CREATE INDEX idx_skills_user_active "
        "ON skills(user_id) WHERE is_active = true"
    )

    # ------------------------------------------------------------------
    # exchanges  (NEW — first-class trading venue, user-scoped)
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE exchanges (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id     UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,

            name        VARCHAR(100) NOT NULL,
            code        VARCHAR(40)  NOT NULL,
            kind        VARCHAR(20)  NOT NULL DEFAULT 'broker',

            meta_data   JSONB NOT NULL DEFAULT '{}'::jsonb,

            created_at  TIMESTAMP DEFAULT NOW(),
            updated_at  TIMESTAMP DEFAULT NOW(),

            CONSTRAINT exchanges_kind_valid
                CHECK (kind IN ('broker', 'exchange', 'prop', 'demo')),
            CONSTRAINT uq_exchanges_user_code
                UNIQUE (user_id, code)
        )
        """
    )
    op.execute("CREATE INDEX idx_exchanges_user ON exchanges(user_id)")

    # ------------------------------------------------------------------
    # accounts  (NEW — grouping layer; owns the broker-credential block
    # lifted off the old projects table)
    # ------------------------------------------------------------------
    # ON DELETE RESTRICT from BOTH users and exchanges: an account groups
    # live trading pairs, so neither its owner nor its venue can be
    # hard-deleted while it exists.
    op.execute(
        """
        CREATE TABLE accounts (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id     UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
            exchange_id UUID NOT NULL REFERENCES exchanges(id) ON DELETE RESTRICT,

            name        VARCHAR(100) NOT NULL,
            description TEXT,

            -- Broker-credential block — MOVED here off the pair. Every pair
            -- under this account INHERITS these credentials (no per-pair
            -- override). account_credential_ref is ALWAYS a pointer into an
            -- external secret store, never a plaintext password.
            account_login           VARCHAR(50),
            account_server          VARCHAR(100),
            broker_name             VARCHAR(80),
            account_credential_ref  VARCHAR(255),
            account_currency        VARCHAR(10),
            account_leverage        INTEGER,
            account_type            VARCHAR(20),

            meta_data   JSONB NOT NULL DEFAULT '{}'::jsonb,

            created_at  TIMESTAMP DEFAULT NOW(),
            updated_at  TIMESTAMP DEFAULT NOW()
        )
        """
    )
    op.execute("CREATE INDEX idx_accounts_user ON accounts(user_id)")
    op.execute("CREATE INDEX idx_accounts_exchange ON accounts(exchange_id)")

    # ------------------------------------------------------------------
    # pairs  (renamed from projects; reparented onto accounts)
    # ------------------------------------------------------------------
    # Cumulative state of the old projects table folding in 0010
    # (orchestrator slot + params) and 0012 (marker/tutor slots + params),
    # MINUS the 7 broker-credential columns (now on accounts), PLUS
    # account_id FK RESTRICT. The denormalised user_id NOT NULL is kept
    # (matches the one-hop ``_for_user`` tenancy pattern).
    op.execute(
        """
        CREATE TABLE pairs (
            id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id             UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
            account_id          UUID NOT NULL REFERENCES accounts(id) ON DELETE RESTRICT,

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

            -- Vinculacion a agentes (6 slots — orchestrator/investigator/
            -- marker/worker/tutor/auditor)
            orchestrator_agent_id  UUID REFERENCES agents(id) ON DELETE RESTRICT,
            investigator_agent_id  UUID REFERENCES agents(id) ON DELETE RESTRICT,
            marker_agent_id        UUID REFERENCES agents(id) ON DELETE RESTRICT,
            worker_agent_id        UUID REFERENCES agents(id) ON DELETE RESTRICT,
            tutor_agent_id         UUID REFERENCES agents(id) ON DELETE RESTRICT,
            auditor_agent_id       UUID REFERENCES agents(id) ON DELETE RESTRICT,

            -- Ventanas operativas
            trading_sessions    TEXT[] NOT NULL DEFAULT '{}'
                CHECK (trading_sessions <@ ARRAY['sydney','shanghai','tokyo','europe','new_york']::text[]),

            -- Parametros por agente (JSONB) — 6 blocks
            orchestrator_params  JSONB NOT NULL DEFAULT '{}'::jsonb,
            investigator_params  JSONB NOT NULL DEFAULT '{}'::jsonb,
            marker_params        JSONB NOT NULL DEFAULT '{}'::jsonb,
            worker_params        JSONB NOT NULL DEFAULT '{}'::jsonb,
            tutor_params         JSONB NOT NULL DEFAULT '{}'::jsonb,
            auditor_params       JSONB NOT NULL DEFAULT '{}'::jsonb,

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
    op.execute("CREATE INDEX idx_pairs_user ON pairs(user_id)")
    op.execute("CREATE INDEX idx_pairs_account ON pairs(account_id)")

    # ------------------------------------------------------------------
    # audit_log  (folds in 0002)
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE audit_log (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),

            user_id         UUID REFERENCES users(id) ON DELETE RESTRICT,

            action          VARCHAR(100) NOT NULL,

            target_type     VARCHAR(50) NOT NULL,
            target_id       UUID,

            before_state    JSONB,
            after_state     JSONB,

            ip_address      INET,
            user_agent      TEXT,

            created_at      TIMESTAMP DEFAULT NOW()
        )
        """
    )
    op.execute(
        "CREATE INDEX idx_audit_log_user_created "
        "ON audit_log(user_id, created_at DESC)"
    )
    op.execute(
        "CREATE INDEX idx_audit_log_action_created "
        "ON audit_log(action, created_at DESC)"
    )

    # ------------------------------------------------------------------
    # mfa_recovery_codes  (folds in 0005)
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE mfa_recovery_codes (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            code_hash   VARCHAR(255) NOT NULL,
            used_at     TIMESTAMP,
            created_at  TIMESTAMP DEFAULT NOW()
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_mfa_recovery_codes_user_id_code_hash "
        "ON mfa_recovery_codes(user_id, code_hash)"
    )
    op.execute(
        "CREATE INDEX idx_mfa_recovery_codes_user_unused "
        "ON mfa_recovery_codes(user_id) WHERE used_at IS NULL"
    )

    # ------------------------------------------------------------------
    # agent_skills  (folds in 0009)
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE agent_skills (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),

            agent_id    UUID NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
            skill_id    UUID NOT NULL REFERENCES skills(id) ON DELETE RESTRICT,

            notes       TEXT,

            created_at  TIMESTAMP NOT NULL DEFAULT NOW(),

            CONSTRAINT uq_agent_skills_pair UNIQUE (agent_id, skill_id)
        )
        """
    )
    op.execute("CREATE INDEX idx_agent_skills_agent ON agent_skills(agent_id)")
    op.execute("CREATE INDEX idx_agent_skills_skill ON agent_skills(skill_id)")

    # ------------------------------------------------------------------
    # agent_runs  (folds in 0004; project_id → pair_id RESTRICT)
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE agent_runs (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),

            user_id         UUID NOT NULL REFERENCES users(id)  ON DELETE RESTRICT,
            agent_id        UUID NOT NULL REFERENCES agents(id) ON DELETE RESTRICT,
            pair_id         UUID NOT NULL REFERENCES pairs(id)  ON DELETE RESTRICT,

            started_at      TIMESTAMP NOT NULL DEFAULT NOW(),
            ended_at        TIMESTAMP,

            status          VARCHAR(20) NOT NULL,

            exit_code       INTEGER,

            stdout          TEXT,
            stderr          TEXT,

            denial_reason   TEXT,

            resource_usage  JSONB NOT NULL DEFAULT '{}'::jsonb,

            CONSTRAINT agent_runs_status_valid
                CHECK (status IN (
                    'running',
                    'success',
                    'denied_import',
                    'denied_network',
                    'denied_file',
                    'timeout',
                    'oom',
                    'error'
                )),
            CONSTRAINT agent_runs_running_no_ended
                CHECK (
                    (status = 'running' AND ended_at IS NULL)
                    OR (status <> 'running' AND ended_at IS NOT NULL)
                )
        )
        """
    )
    op.execute(
        "CREATE INDEX idx_agent_runs_user_started "
        "ON agent_runs(user_id, started_at DESC)"
    )
    op.execute(
        "CREATE INDEX idx_agent_runs_agent_started "
        "ON agent_runs(agent_id, started_at DESC)"
    )
    op.execute(
        "CREATE INDEX idx_agent_runs_pair_started "
        "ON agent_runs(pair_id, started_at DESC)"
    )

    # ------------------------------------------------------------------
    # container_events  (folds in 0006; project_id → pair_id RESTRICT)
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE container_events (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),

            pair_id         UUID NOT NULL REFERENCES pairs(id) ON DELETE RESTRICT,
            user_id         UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,

            action          VARCHAR(50) NOT NULL,
            status          VARCHAR(20) NOT NULL,

            payload         JSONB NOT NULL DEFAULT '{}'::jsonb,

            error           TEXT,

            created_at      TIMESTAMP DEFAULT NOW()
        )
        """
    )
    op.execute(
        "CREATE INDEX idx_container_events_pair_created "
        "ON container_events(pair_id, created_at DESC)"
    )
    op.execute(
        "CREATE INDEX idx_container_events_user_created "
        "ON container_events(user_id, created_at DESC)"
    )

    # ------------------------------------------------------------------
    # orders  (folds in 0007 + 0013 operativa extension; pair_id RESTRICT)
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE orders (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),

            pair_id         UUID NOT NULL REFERENCES pairs(id) ON DELETE RESTRICT,
            user_id         UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,

            agent_id        UUID REFERENCES agents(id) ON DELETE SET NULL,

            symbol          VARCHAR(20) NOT NULL,
            side            VARCHAR(10) NOT NULL
                              CHECK (side IN ('buy','sell')),
            volume          DECIMAL(10,4) NOT NULL CHECK (volume > 0),

            -- CHARTER: SL mandatory on every order.
            sl              DECIMAL(15,5) NOT NULL,
            tp              DECIMAL(15,5),

            mt5_ticket      BIGINT,

            -- 8-value lifecycle (folds in 0013's 'closed'/'cancelled').
            status          VARCHAR(20) NOT NULL,

            comment         VARCHAR(255),
            magic           INTEGER,

            created_at      TIMESTAMP DEFAULT NOW(),
            filled_at       TIMESTAMP,

            -- Operativa extension (0013) — all NULLABLE.
            open_time       TIMESTAMPTZ,
            open_price      NUMERIC(18,8),
            close_time      TIMESTAMPTZ,
            close_price     NUMERIC(18,8),
            commission      NUMERIC(18,4),
            swap            NUMERIC(18,4),
            profit_gross    NUMERIC(18,4),
            profit_net      NUMERIC(18,4),

            meta_data       JSONB NOT NULL DEFAULT '{}'::jsonb,

            CONSTRAINT orders_status_valid
                CHECK (status IN (
                    'pending',
                    'approved_pending_send',
                    'filled',
                    'failed',
                    'rejected',
                    'expired',
                    'closed',
                    'cancelled'
                ))
        )
        """
    )
    op.execute(
        "CREATE INDEX idx_orders_pair_created "
        "ON orders(pair_id, created_at DESC)"
    )
    op.execute("CREATE INDEX idx_orders_pair_status ON orders(pair_id, status)")
    op.execute("CREATE INDEX idx_orders_pair_symbol ON orders(pair_id, symbol)")
    op.execute(
        "CREATE INDEX idx_orders_pair_open_time "
        "ON orders(pair_id, open_time DESC)"
    )
    op.execute(
        "CREATE UNIQUE INDEX idx_orders_mt5_ticket "
        "ON orders(mt5_ticket) WHERE mt5_ticket IS NOT NULL"
    )

    # ------------------------------------------------------------------
    # order_log  (folds in 0007; pair_id RESTRICT)
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE order_log (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),

            order_id        UUID REFERENCES orders(id) ON DELETE CASCADE,

            pair_id         UUID NOT NULL REFERENCES pairs(id) ON DELETE RESTRICT,
            user_id         UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,

            action          VARCHAR(50) NOT NULL,

            payload_in      JSONB,
            payload_out     JSONB,
            risk_check      JSONB,

            status          VARCHAR(20) NOT NULL
                              CHECK (status IN (
                                'pending',
                                'filled',
                                'failed',
                                'blocked'
                              )),
            error           TEXT,

            created_at      TIMESTAMP DEFAULT NOW()
        )
        """
    )
    op.execute(
        "CREATE INDEX idx_order_log_pair_created "
        "ON order_log(pair_id, created_at DESC)"
    )

    # ------------------------------------------------------------------
    # order_approvals  (folds in 0007; pair_id RESTRICT)
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE order_approvals (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),

            pair_id         UUID NOT NULL REFERENCES pairs(id) ON DELETE RESTRICT,
            user_id         UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
            agent_id        UUID REFERENCES agents(id) ON DELETE SET NULL,

            payload         JSONB NOT NULL,

            status          VARCHAR(20) NOT NULL
                              CHECK (status IN (
                                'pending', 'approved', 'rejected', 'expired'
                              )),

            requested_at    TIMESTAMP DEFAULT NOW(),
            decided_at      TIMESTAMP,
            decided_by      UUID REFERENCES users(id),
            expires_at      TIMESTAMP NOT NULL
        )
        """
    )
    op.execute(
        "CREATE INDEX idx_order_approvals_pair_created "
        "ON order_approvals(pair_id, requested_at DESC)"
    )

    # ------------------------------------------------------------------
    # sleep_runs  (folds in 0008; pair_id RESTRICT)
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE sleep_runs (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),

            pair_id         UUID NOT NULL REFERENCES pairs(id) ON DELETE RESTRICT,
            user_id         UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,

            phase_type      VARCHAR(20) NOT NULL,

            started_at      TIMESTAMP DEFAULT NOW(),
            ended_at        TIMESTAMP,

            status          VARCHAR(20) NOT NULL,

            summary         TEXT,
            error           TEXT,

            CONSTRAINT sleep_runs_phase_type_valid
                CHECK (phase_type IN ('micro', 'profundo', 'critico')),
            CONSTRAINT sleep_runs_status_valid
                CHECK (status IN (
                    'running', 'succeeded', 'failed',
                    'crashed', 'skipped', 'partial'
                ))
        )
        """
    )
    op.execute(
        "CREATE INDEX idx_sleep_runs_pair_started "
        "ON sleep_runs(pair_id, started_at DESC)"
    )
    op.execute(
        "CREATE INDEX idx_sleep_runs_user_started "
        "ON sleep_runs(user_id, started_at DESC)"
    )
    op.execute(
        "CREATE INDEX idx_sleep_runs_status_started "
        "ON sleep_runs(status, started_at)"
    )

    # ------------------------------------------------------------------
    # sleep_reflections  (folds in 0008 + 0011 agent_type CHECK)
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE sleep_reflections (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),

            sleep_run_id    UUID NOT NULL REFERENCES sleep_runs(id) ON DELETE CASCADE,
            agent_type      VARCHAR(20) NOT NULL,

            reflection_md   TEXT,
            suggested_changes JSONB NOT NULL DEFAULT '{}'::jsonb,

            created_at      TIMESTAMP DEFAULT NOW(),

            CONSTRAINT sleep_reflections_agent_type_valid
                CHECK (agent_type IN ('orchestrator', 'worker', 'investigator', 'auditor')),
            CONSTRAINT uq_sleep_reflections_run_agent
                UNIQUE (sleep_run_id, agent_type)
        )
        """
    )
    op.execute(
        "CREATE INDEX idx_sleep_reflections_run "
        "ON sleep_reflections(sleep_run_id)"
    )

    # ------------------------------------------------------------------
    # config_versions  (folds in 0008 + 0011 learning columns; pair_id RESTRICT)
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE config_versions (
            id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),

            pair_id             UUID NOT NULL REFERENCES pairs(id) ON DELETE RESTRICT,
            parent_version_id   UUID REFERENCES config_versions(id),
            sleep_run_id        UUID REFERENCES sleep_runs(id),

            snapshot            JSONB NOT NULL,

            risk_class          VARCHAR(10) NOT NULL,
            status              VARCHAR(20) NOT NULL,

            proposed_at         TIMESTAMP DEFAULT NOW(),
            decided_at          TIMESTAMP,
            decided_by          UUID REFERENCES users(id),
            applied_at          TIMESTAMP,

            -- Learning-loop columns (0011).
            q_table_version     VARCHAR(30),
            prompt_snapshot     TEXT,
            version_name        VARCHAR(80),

            CONSTRAINT config_versions_risk_class_valid
                CHECK (risk_class IN ('bajo', 'medio', 'alto')),
            CONSTRAINT config_versions_status_valid
                CHECK (status IN (
                    'pending', 'approved', 'rejected', 'applied', 'reverted'
                ))
        )
        """
    )
    op.execute(
        "CREATE INDEX idx_config_versions_pair_proposed "
        "ON config_versions(pair_id, proposed_at DESC)"
    )
    op.execute(
        "CREATE INDEX idx_config_versions_status "
        "ON config_versions(status) WHERE status = 'pending'"
    )
    op.execute(
        "CREATE INDEX idx_config_versions_sleep_run "
        "ON config_versions(sleep_run_id)"
    )

    # ------------------------------------------------------------------
    # q_tables  (folds in 0011; project_id → pair_id CASCADE)
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE q_tables (
            id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),

            pair_id                  UUID NOT NULL
                REFERENCES pairs(id) ON DELETE CASCADE,

            version                  INTEGER NOT NULL,
            "table"                  JSONB NOT NULL,

            alpha_normal             NUMERIC(4,3) NOT NULL DEFAULT 0.150,
            alpha_special            NUMERIC(4,3) NOT NULL DEFAULT 0.350,
            gamma                    NUMERIC(4,3) NOT NULL DEFAULT 0.920,

            episode_count            INTEGER NOT NULL DEFAULT 0,

            created_by_sleep_run_id  UUID
                REFERENCES sleep_runs(id) ON DELETE SET NULL,

            created_at               TIMESTAMP NOT NULL DEFAULT NOW(),

            CONSTRAINT q_tables_version_positive
                CHECK (version >= 1),
            CONSTRAINT q_tables_alpha_range
                CHECK (alpha_normal >= 0 AND alpha_normal <= 1
                       AND alpha_special >= 0 AND alpha_special <= 1),
            CONSTRAINT q_tables_gamma_range
                CHECK (gamma >= 0 AND gamma <= 1),
            CONSTRAINT q_tables_episode_count_nonneg
                CHECK (episode_count >= 0)
        )
        """
    )
    op.execute("CREATE INDEX idx_q_tables_pair ON q_tables(pair_id)")
    op.execute(
        "CREATE UNIQUE INDEX uq_q_tables_pair_version "
        "ON q_tables(pair_id, version)"
    )
    op.execute(
        "CREATE INDEX idx_q_tables_pair_created_at "
        "ON q_tables(pair_id, created_at DESC)"
    )

    # ------------------------------------------------------------------
    # episodic_memory  (folds in 0011; project_id → pair_id CASCADE)
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE episodic_memory (
            id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),

            pair_id             UUID NOT NULL
                REFERENCES pairs(id) ON DELETE CASCADE,

            state_key           VARCHAR(120) NOT NULL,
            action              VARCHAR(60)  NOT NULL,
            reward              NUMERIC(12,6) NOT NULL,
            next_state_key      VARCHAR(120),

            order_id            UUID REFERENCES orders(id) ON DELETE SET NULL,

            consumed_by_sleep_run_id UUID
                REFERENCES sleep_runs(id) ON DELETE SET NULL,

            metadata            JSONB NOT NULL DEFAULT '{}'::jsonb,

            created_at          TIMESTAMP NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute(
        "CREATE INDEX idx_episodic_pair_created "
        "ON episodic_memory(pair_id, created_at DESC)"
    )
    op.execute(
        "CREATE INDEX idx_episodic_pair_state "
        "ON episodic_memory(pair_id, state_key)"
    )
    op.execute(
        "CREATE INDEX idx_episodic_sleep_run "
        "ON episodic_memory(consumed_by_sleep_run_id)"
    )

    # ------------------------------------------------------------------
    # semantic_memory  (folds in 0011; project_id → pair_id CASCADE)
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE semantic_memory (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),

            pair_id         UUID NOT NULL
                REFERENCES pairs(id) ON DELETE CASCADE,

            rule_type       VARCHAR(40) NOT NULL,

            body            TEXT NOT NULL,

            payload         JSONB NOT NULL DEFAULT '{}'::jsonb,

            superseded_by   UUID
                REFERENCES semantic_memory(id) ON DELETE SET NULL,

            active          BOOLEAN NOT NULL DEFAULT TRUE,

            created_by_sleep_run_id UUID
                REFERENCES sleep_runs(id) ON DELETE SET NULL,

            created_at      TIMESTAMP NOT NULL DEFAULT NOW(),
            updated_at      TIMESTAMP NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute(
        "CREATE INDEX idx_semantic_pair_active "
        "ON semantic_memory(pair_id, active)"
    )
    op.execute(
        "CREATE INDEX idx_semantic_pair_rule_type "
        "ON semantic_memory(pair_id, rule_type)"
    )

    # ------------------------------------------------------------------
    # sleep_reports  (folds in 0011; 1:1 with sleep_runs)
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE sleep_reports (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),

            sleep_run_id    UUID NOT NULL UNIQUE
                REFERENCES sleep_runs(id) ON DELETE CASCADE,

            payload         JSONB NOT NULL DEFAULT '{}'::jsonb,

            summary_md      TEXT,

            created_at      TIMESTAMP NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute(
        "CREATE INDEX idx_sleep_reports_sleep_run ON sleep_reports(sleep_run_id)"
    )

    # ------------------------------------------------------------------
    # chat_conversations  (folds in 0014; project_id → pair_id CASCADE)
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE chat_conversations (
            id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),

            pair_id              UUID NOT NULL
                REFERENCES pairs(id) ON DELETE CASCADE,

            user_id              UUID NOT NULL
                REFERENCES users(id) ON DELETE RESTRICT,

            title                VARCHAR(200) NOT NULL DEFAULT '(sin título)',

            created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            archived_at          TIMESTAMPTZ NULL,

            meta_data            JSONB NOT NULL DEFAULT '{}'::jsonb,

            tokens_in_total      INTEGER NOT NULL DEFAULT 0,
            usd_estimated_total  NUMERIC(12,6) NOT NULL DEFAULT 0,

            CONSTRAINT chat_conversations_title_nonempty
                CHECK (length(title) >= 1),
            CONSTRAINT chat_conversations_tokens_nonneg
                CHECK (tokens_in_total >= 0),
            CONSTRAINT chat_conversations_usd_nonneg
                CHECK (usd_estimated_total >= 0)
        )
        """
    )
    op.execute(
        "CREATE INDEX idx_chat_conv_pair_created "
        "ON chat_conversations(pair_id, created_at DESC) "
        "WHERE archived_at IS NULL"
    )
    op.execute(
        "CREATE INDEX idx_chat_conv_user_created "
        "ON chat_conversations(user_id, created_at DESC) "
        "WHERE archived_at IS NULL"
    )

    # ------------------------------------------------------------------
    # chat_messages  (folds in 0014)
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE chat_messages (
            id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),

            conversation_id   UUID NOT NULL
                REFERENCES chat_conversations(id) ON DELETE CASCADE,

            role              VARCHAR(20) NOT NULL,

            content           TEXT NOT NULL,

            tool_calls        JSONB NULL,
            tool_results      JSONB NULL,

            tokens_in         INTEGER NULL,
            tokens_out        INTEGER NULL,

            model             VARCHAR(50) NULL,
            stop_reason       VARCHAR(50) NULL,

            action_proposal   JSONB NULL,

            meta_data         JSONB NOT NULL DEFAULT '{}'::jsonb,

            created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),

            CONSTRAINT chat_messages_role_valid
                CHECK (role IN ('user', 'assistant', 'system', 'tool')),
            CONSTRAINT chat_messages_tokens_in_nonneg
                CHECK (tokens_in IS NULL OR tokens_in >= 0),
            CONSTRAINT chat_messages_tokens_out_nonneg
                CHECK (tokens_out IS NULL OR tokens_out >= 0)
        )
        """
    )
    op.execute(
        "CREATE INDEX idx_chat_msg_conv_created "
        "ON chat_messages(conversation_id, created_at)"
    )

    # ------------------------------------------------------------------
    # chat_action_proposals  (folds in 0014; project_id → pair_id CASCADE)
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE chat_action_proposals (
            id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),

            message_id        UUID NOT NULL
                REFERENCES chat_messages(id) ON DELETE CASCADE,

            conversation_id   UUID NOT NULL
                REFERENCES chat_conversations(id) ON DELETE CASCADE,
            pair_id           UUID NOT NULL
                REFERENCES pairs(id) ON DELETE CASCADE,

            tool_name         VARCHAR(80) NOT NULL,

            payload           JSONB NOT NULL,

            status            VARCHAR(20) NOT NULL DEFAULT 'pending',

            decided_at        TIMESTAMPTZ NULL,
            decided_by        UUID NULL
                REFERENCES users(id) ON DELETE SET NULL,
            decision_note     TEXT NULL,

            executed_at       TIMESTAMPTZ NULL,
            execution_result  JSONB NULL,

            created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),

            CONSTRAINT chat_action_proposals_status_valid
                CHECK (status IN (
                    'pending', 'approved', 'rejected', 'expired', 'executed'
                )),
            CONSTRAINT chat_action_proposals_tool_name_nonempty
                CHECK (length(tool_name) >= 1)
        )
        """
    )
    op.execute(
        "CREATE INDEX idx_chat_action_proposals_conv "
        "ON chat_action_proposals(conversation_id, created_at DESC)"
    )
    op.execute(
        "CREATE INDEX idx_chat_action_proposals_status "
        "ON chat_action_proposals(pair_id, status) "
        "WHERE status = 'pending'"
    )

    # ------------------------------------------------------------------
    # Append-only grants for the optional 'aether' role. Guarded so the
    # migration is a no-op when the role is absent (CI / testcontainers).
    # Folds in the grant blocks of 0002 / 0004 / 0008 / 0011 / 0014.
    # ------------------------------------------------------------------
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'aether') THEN
                -- audit_log: insert/select only.
                EXECUTE 'GRANT INSERT, SELECT ON audit_log TO aether';
                EXECUTE 'REVOKE UPDATE, DELETE ON audit_log FROM aether';

                -- agent_runs: insert/select/update (terminal-status write).
                EXECUTE 'GRANT INSERT, SELECT, UPDATE ON agent_runs TO aether';
                EXECUTE 'REVOKE DELETE ON agent_runs FROM aether';

                -- sleep phase.
                EXECUTE 'GRANT INSERT, SELECT, UPDATE ON sleep_runs TO aether';
                EXECUTE 'GRANT INSERT, SELECT, UPDATE ON sleep_reflections TO aether';
                EXECUTE 'GRANT INSERT, SELECT, UPDATE ON config_versions TO aether';
                EXECUTE 'REVOKE DELETE ON sleep_runs FROM aether';
                EXECUTE 'REVOKE DELETE ON sleep_reflections FROM aether';
                EXECUTE 'REVOKE DELETE ON config_versions FROM aether';

                -- learning loop.
                EXECUTE 'GRANT INSERT, SELECT, UPDATE ON q_tables TO aether';
                EXECUTE 'GRANT INSERT, SELECT, UPDATE ON episodic_memory TO aether';
                EXECUTE 'GRANT INSERT, SELECT, UPDATE ON semantic_memory TO aether';
                EXECUTE 'GRANT INSERT, SELECT, UPDATE ON sleep_reports TO aether';
                EXECUTE 'REVOKE DELETE ON q_tables FROM aether';
                EXECUTE 'REVOKE DELETE ON episodic_memory FROM aether';
                EXECUTE 'REVOKE DELETE ON sleep_reports FROM aether';

                -- chat plane (DELETE granted — soft-delete is app-level but
                -- hard-delete cascade is permitted for the chat tables).
                EXECUTE 'GRANT INSERT, SELECT, UPDATE ON chat_conversations TO aether';
                EXECUTE 'GRANT INSERT, SELECT, UPDATE ON chat_messages TO aether';
                EXECUTE 'GRANT INSERT, SELECT, UPDATE ON chat_action_proposals TO aether';
                EXECUTE 'GRANT DELETE ON chat_conversations TO aether';
                EXECUTE 'GRANT DELETE ON chat_messages TO aether';
                EXECUTE 'GRANT DELETE ON chat_action_proposals TO aether';
            END IF;
        END
        $$;
        """
    )


# -----------------------------------------------------------------------------
# Downgrade
# -----------------------------------------------------------------------------
def downgrade() -> None:
    # Reverse FK dependency order.
    op.execute("DROP TABLE IF EXISTS chat_action_proposals")
    op.execute("DROP TABLE IF EXISTS chat_messages")
    op.execute("DROP TABLE IF EXISTS chat_conversations")

    op.execute("DROP TABLE IF EXISTS sleep_reports")
    op.execute("DROP TABLE IF EXISTS semantic_memory")
    op.execute("DROP TABLE IF EXISTS episodic_memory")
    op.execute("DROP TABLE IF EXISTS q_tables")

    op.execute("DROP TABLE IF EXISTS config_versions")
    op.execute("DROP TABLE IF EXISTS sleep_reflections")
    op.execute("DROP TABLE IF EXISTS sleep_runs")

    op.execute("DROP TABLE IF EXISTS order_approvals")
    op.execute("DROP TABLE IF EXISTS order_log")
    op.execute("DROP TABLE IF EXISTS orders")

    op.execute("DROP TABLE IF EXISTS container_events")
    op.execute("DROP TABLE IF EXISTS agent_runs")
    op.execute("DROP TABLE IF EXISTS agent_skills")
    op.execute("DROP TABLE IF EXISTS mfa_recovery_codes")
    op.execute("DROP TABLE IF EXISTS audit_log")

    op.execute("DROP TABLE IF EXISTS pairs")
    op.execute("DROP TABLE IF EXISTS accounts")
    op.execute("DROP TABLE IF EXISTS exchanges")

    op.execute("DROP TABLE IF EXISTS skills")
    op.execute("DROP TABLE IF EXISTS agents")
    op.execute("DROP TABLE IF EXISTS sessions")
    op.execute("DROP TABLE IF EXISTS users")

    # pgcrypto extension intentionally NOT dropped (database-wide).
