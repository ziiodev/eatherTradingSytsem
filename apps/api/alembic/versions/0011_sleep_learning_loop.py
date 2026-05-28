"""0011_sleep_learning_loop — Sleep-Phase learning substrate.

Builds the four new tables that turn the existing Sleep Phase loop into
an actual learning loop:

* ``q_tables``           — per-project versioned Q-value snapshots
                           (``state_key → action → q_value``). Each Sleep
                           Phase run promotes a new version monotonically
                           per ``project_id``.
* ``episodic_memory``    — append-only history of (state, action, reward,
                           next_state) tuples. Episodes are mined by the
                           Investigator/Auditor during Sleep Phase to
                           power the TD update.
* ``semantic_memory``    — long-term "lessons learned" rules distilled
                           by the Orquestador. ``active = false`` plus
                           ``superseded_by`` track lineage; rules are
                           never deleted.
* ``sleep_reports``      — exactly ONE structured outcome row per
                           ``sleep_runs.id`` (1:1). Aggregates
                           Q-update deltas, episode counts, semantic
                           rule diffs, the new config_versions promotion
                           identifier — all in JSONB.

Plus two ALTERs:

* ``config_versions``    — adds three optional snapshot columns the
                           learning loop populates:
                             - ``q_table_version VARCHAR(30) NULL`` —
                               canonical reference to the Q-Table that
                               this config snapshot pinned at the time
                               of promotion (e.g. ``"v42"``).
                             - ``prompt_snapshot TEXT NULL`` — frozen
                               agent prompt(s) at promotion time, so
                               Sleep Phase reverts truly roll back
                               behaviour, not just numbers.
                             - ``version_name VARCHAR(80) NULL`` —
                               operator-friendly label for the
                               dashboard ("daily-deep-2026-05-28", etc.).

* ``sleep_reflections``  — extends the ``agent_type`` CHECK to admit
                           the new value ``'orchestrator'`` so the
                           Orquestador can land its own reflection row
                           alongside worker/investigator/auditor (see
                           ``sleep-phase-delta`` spec).

Multi-tenancy:

    ``q_tables``, ``episodic_memory``, ``semantic_memory`` all carry
    ``project_id UUID NOT NULL REFERENCES projects(id) ON DELETE
    CASCADE``. They do NOT carry ``user_id`` directly — tenant isolation
    is enforced transitively at the repository layer by JOINing
    ``projects.user_id = :user_id``. See ``multi-tenancy-delta`` spec.

    ``sleep_reports`` inherits its tenant via ``sleep_run_id →
    sleep_runs.project_id → projects.user_id``. The relationship to
    ``sleep_runs`` is a UNIQUE NOT NULL FK with CASCADE so deleting a
    sleep run takes its report with it.

Revision ID: 0011_sleep_learning_loop
Revises: 0010_orchestrator_agent
Create Date: 2026-05-28
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

# Alembic identifiers.
revision: str = "0011_sleep_learning_loop"
down_revision: str | Sequence[str] | None = "0010_orchestrator_agent"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# -----------------------------------------------------------------------------
# Upgrade
# -----------------------------------------------------------------------------
def upgrade() -> None:
    # ------------------------------------------------------------------
    # q_tables — versioned per-project Q-value snapshots.
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS q_tables (
            id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),

            -- CASCADE: when a project is hard-deleted (admin path) its
            -- learning history dies with it. Repository layer enforces
            -- tenant isolation via JOIN through projects.user_id.
            project_id               UUID NOT NULL
                REFERENCES projects(id) ON DELETE CASCADE,

            version                  INTEGER NOT NULL,
            -- "table" is a reserved word in PG, hence the double quotes.
            -- The Python ORM exposes this as ``QTable.table_data``.
            "table"                  JSONB NOT NULL,

            alpha_normal             NUMERIC(4,3) NOT NULL DEFAULT 0.150,
            alpha_special            NUMERIC(4,3) NOT NULL DEFAULT 0.350,
            gamma                    NUMERIC(4,3) NOT NULL DEFAULT 0.920,

            episode_count            INTEGER NOT NULL DEFAULT 0,

            -- A sleep run produces a new Q-Table; on hard-delete of the
            -- run we keep the resulting Q-Table (SET NULL).
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
    op.execute("CREATE INDEX IF NOT EXISTS idx_q_tables_project ON q_tables(project_id)")
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_q_tables_project_version "
        "ON q_tables(project_id, version)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_q_tables_project_created_at "
        "ON q_tables(project_id, created_at DESC)"
    )

    # ------------------------------------------------------------------
    # episodic_memory — append-only (state, action, reward, next_state).
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS episodic_memory (
            id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),

            project_id          UUID NOT NULL
                REFERENCES projects(id) ON DELETE CASCADE,

            -- The (s, a, r, s') tuple. state/next_state are opaque strings
            -- (canonical state_key) so the Q-update stays storage-agnostic.
            state_key           VARCHAR(120) NOT NULL,
            action              VARCHAR(60)  NOT NULL,
            reward              NUMERIC(12,6) NOT NULL,
            next_state_key      VARCHAR(120),

            -- Optional FKs to the trade / order that generated the episode.
            -- SET NULL so episode rows survive hard-deletes of audit-trail rows.
            order_id            UUID REFERENCES orders(id) ON DELETE SET NULL,

            -- Sleep run that consumed this episode (NULL until ingested).
            consumed_by_sleep_run_id UUID
                REFERENCES sleep_runs(id) ON DELETE SET NULL,

            metadata            JSONB NOT NULL DEFAULT '{}'::jsonb,

            created_at          TIMESTAMP NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_episodic_project_created "
        "ON episodic_memory(project_id, created_at DESC)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_episodic_project_state "
        "ON episodic_memory(project_id, state_key)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_episodic_sleep_run "
        "ON episodic_memory(consumed_by_sleep_run_id)"
    )

    # ------------------------------------------------------------------
    # semantic_memory — "lessons learned" rules with supersession lineage.
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS semantic_memory (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),

            project_id      UUID NOT NULL
                REFERENCES projects(id) ON DELETE CASCADE,

            -- Rule category — keep loose so the Orquestador can introduce
            -- new buckets without a migration. Indexed for filtering.
            rule_type       VARCHAR(40) NOT NULL,

            -- Human-readable lesson body (markdown allowed).
            body            TEXT NOT NULL,

            -- Structured payload alongside the markdown body (parameters,
            -- thresholds, etc.).
            payload         JSONB NOT NULL DEFAULT '{}'::jsonb,

            -- Lineage: a new rule that replaces an older one points at it.
            -- On hard-delete of the parent rule, the FK is cleared (SET NULL)
            -- so the child does not get cascade-deleted.
            superseded_by   UUID
                REFERENCES semantic_memory(id) ON DELETE SET NULL,

            -- Soft toggle. Rules are NEVER hard-deleted; the Orquestador
            -- promotes new ones and marks old ones inactive.
            active          BOOLEAN NOT NULL DEFAULT TRUE,

            -- Sleep run that authored this rule (NULL for hand-seeded).
            created_by_sleep_run_id UUID
                REFERENCES sleep_runs(id) ON DELETE SET NULL,

            created_at      TIMESTAMP NOT NULL DEFAULT NOW(),
            updated_at      TIMESTAMP NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_semantic_project_active "
        "ON semantic_memory(project_id, active)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_semantic_project_rule_type "
        "ON semantic_memory(project_id, rule_type)"
    )

    # ------------------------------------------------------------------
    # sleep_reports — 1:1 with sleep_runs (UNIQUE NOT NULL FK CASCADE).
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS sleep_reports (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),

            -- Exactly one report per sleep run. CASCADE: deleting the run
            -- takes its report. NOT NULL + UNIQUE enforces the 1:1.
            sleep_run_id    UUID NOT NULL UNIQUE
                REFERENCES sleep_runs(id) ON DELETE CASCADE,

            -- Aggregated outcome: Q-Table diff summary, episode counts,
            -- semantic rule diffs, the promoted config_versions id, etc.
            payload         JSONB NOT NULL DEFAULT '{}'::jsonb,

            -- Operator-friendly markdown digest (rendered by the dashboard).
            summary_md      TEXT,

            created_at      TIMESTAMP NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_sleep_reports_sleep_run ON sleep_reports(sleep_run_id)"
    )

    # ------------------------------------------------------------------
    # config_versions — add the three learning-loop columns.
    # ------------------------------------------------------------------
    op.execute("ALTER TABLE config_versions ADD COLUMN IF NOT EXISTS q_table_version VARCHAR(30)")
    op.execute("ALTER TABLE config_versions ADD COLUMN IF NOT EXISTS prompt_snapshot TEXT")
    op.execute("ALTER TABLE config_versions ADD COLUMN IF NOT EXISTS version_name VARCHAR(80)")

    # ------------------------------------------------------------------
    # sleep_reflections.agent_type — extend CHECK to include 'orchestrator'.
    # ------------------------------------------------------------------
    op.execute(
        "ALTER TABLE sleep_reflections DROP CONSTRAINT IF EXISTS sleep_reflections_agent_type_valid"
    )
    op.execute(
        "ALTER TABLE sleep_reflections "
        "ADD CONSTRAINT sleep_reflections_agent_type_valid "
        "CHECK (agent_type IN ('orchestrator', 'worker', 'investigator', 'auditor'))"
    )

    # ------------------------------------------------------------------
    # Append-only grants for the existing 'aether' role.
    # ------------------------------------------------------------------
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'aether') THEN
                EXECUTE 'GRANT INSERT, SELECT, UPDATE ON q_tables TO aether';
                EXECUTE 'GRANT INSERT, SELECT, UPDATE ON episodic_memory TO aether';
                EXECUTE 'GRANT INSERT, SELECT, UPDATE ON semantic_memory TO aether';
                EXECUTE 'GRANT INSERT, SELECT, UPDATE ON sleep_reports TO aether';
                EXECUTE 'REVOKE DELETE ON q_tables FROM aether';
                EXECUTE 'REVOKE DELETE ON episodic_memory FROM aether';
                EXECUTE 'REVOKE DELETE ON sleep_reports FROM aether';
            END IF;
        END
        $$;
        """
    )


# -----------------------------------------------------------------------------
# Downgrade
# -----------------------------------------------------------------------------
def downgrade() -> None:
    # Reverse the sleep_reflections CHECK first so removing 'orchestrator'
    # rows isn't blocked by FK constraints later. NOTE: if any reflection
    # rows have agent_type='orchestrator' at downgrade time, the narrower
    # CHECK will fail. Best-effort symmetry — operators are expected to
    # purge those rows before downgrading (same pattern as 0010's note).
    op.execute(
        "ALTER TABLE sleep_reflections DROP CONSTRAINT IF EXISTS sleep_reflections_agent_type_valid"
    )
    op.execute(
        "ALTER TABLE sleep_reflections "
        "ADD CONSTRAINT sleep_reflections_agent_type_valid "
        "CHECK (agent_type IN ('worker', 'investigator', 'auditor'))"
    )

    # config_versions — drop the three learning-loop columns.
    op.execute("ALTER TABLE config_versions DROP COLUMN IF EXISTS version_name")
    op.execute("ALTER TABLE config_versions DROP COLUMN IF EXISTS prompt_snapshot")
    op.execute("ALTER TABLE config_versions DROP COLUMN IF EXISTS q_table_version")

    # Drop tables in reverse FK order. sleep_reports → sleep_runs (CASCADE)
    # so sleep_reports must go first. semantic_memory has self-FK; dropping
    # the table drops the FK. episodic_memory references orders & sleep_runs
    # with SET NULL — independent of the other learning tables.
    op.execute("DROP INDEX IF EXISTS idx_sleep_reports_sleep_run")
    op.execute("DROP TABLE IF EXISTS sleep_reports")

    op.execute("DROP INDEX IF EXISTS idx_semantic_project_rule_type")
    op.execute("DROP INDEX IF EXISTS idx_semantic_project_active")
    op.execute("DROP TABLE IF EXISTS semantic_memory")

    op.execute("DROP INDEX IF EXISTS idx_episodic_sleep_run")
    op.execute("DROP INDEX IF EXISTS idx_episodic_project_state")
    op.execute("DROP INDEX IF EXISTS idx_episodic_project_created")
    op.execute("DROP TABLE IF EXISTS episodic_memory")

    op.execute("DROP INDEX IF EXISTS idx_q_tables_project_created_at")
    op.execute("DROP INDEX IF EXISTS uq_q_tables_project_version")
    op.execute("DROP INDEX IF EXISTS idx_q_tables_project")
    op.execute("DROP TABLE IF EXISTS q_tables")
