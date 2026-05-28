"""0008_sleep_phase — Sleep Phase tables (runs, reflections, config_versions).

Adds the load-bearing Sleep Phase persistence layer. Three tables:

* ``sleep_runs`` — one row per (manual or scheduled) Micro / Profundo /
  Crítico reflection run. Lifecycle: ``running`` → terminal state.
* ``sleep_reflections`` — one row per agent reflection produced during
  a sleep run. Uniqueness ``(sleep_run_id, agent_type)`` prevents a
  retry from duplicating rows.
* ``config_versions`` — append-only configuration snapshots. Self-FK
  ``parent_version_id`` carries the revert lineage. Risk class drives
  the human-approval gate.

NOTE on chain ordering:

    Wave 4 lands two parallel changes (``mt5-integration`` →
    ``0007_orders_and_approvals``, this → ``0008_sleep_phase``). The
    slot allocation is ``0007 → 0008``. At apply time, ``0007`` may
    still be in flight on its sibling; this file is wired with
    ``down_revision = "0006_container_events"`` as a fallback. When
    ``0007`` lands, re-stitch by editing ``down_revision`` here to
    point at the mt5 migration — both upgrade() and downgrade() are
    idempotent, so the edit is a one-liner.

Revision ID: 0008_sleep_phase
Revises: 0007_orders_and_approvals
Create Date: 2026-05-28
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

# Alembic identifiers.
revision: str = "0008_sleep_phase"
# Chained directly off mt5-integration's 0007_orders_and_approvals (the
# sibling change in Wave 4) so ``alembic heads`` returns exactly one
# revision. Both upgrade() and downgrade() are idempotent.
down_revision: str | Sequence[str] | None = "0007_orders_and_approvals"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# -----------------------------------------------------------------------------
# Upgrade
# -----------------------------------------------------------------------------
def upgrade() -> None:
    # sleep_runs --------------------------------------------------------
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS sleep_runs (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),

            -- RESTRICT: a run history row pins its project + user. The
            -- application surfaces this as 409 on hard delete attempts.
            project_id      UUID NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
            user_id         UUID NOT NULL REFERENCES users(id)    ON DELETE RESTRICT,

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
        "CREATE INDEX IF NOT EXISTS idx_sleep_runs_project_started "
        "ON sleep_runs(project_id, started_at DESC)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_sleep_runs_user_started "
        "ON sleep_runs(user_id, started_at DESC)"
    )
    # Boot-sweep predicate: stale running rows older than N minutes.
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_sleep_runs_status_started "
        "ON sleep_runs(status, started_at)"
    )

    # sleep_reflections -------------------------------------------------
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS sleep_reflections (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),

            sleep_run_id    UUID NOT NULL REFERENCES sleep_runs(id) ON DELETE CASCADE,
            agent_type      VARCHAR(20) NOT NULL,

            reflection_md   TEXT,
            suggested_changes JSONB NOT NULL DEFAULT '{}'::jsonb,

            created_at      TIMESTAMP DEFAULT NOW(),

            CONSTRAINT sleep_reflections_agent_type_valid
                CHECK (agent_type IN ('worker', 'investigator', 'auditor')),
            CONSTRAINT uq_sleep_reflections_run_agent
                UNIQUE (sleep_run_id, agent_type)
        )
        """
    )

    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_sleep_reflections_run "
        "ON sleep_reflections(sleep_run_id)"
    )

    # config_versions ---------------------------------------------------
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS config_versions (
            id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),

            project_id          UUID NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
            -- Self-FK carries the revert lineage. A revert append a NEW row
            -- whose ``parent_version_id`` points at the version being undone.
            parent_version_id   UUID REFERENCES config_versions(id),
            -- Sleep run that proposed this version (NULL for hand-edited /
            -- legacy snapshots).
            sleep_run_id        UUID REFERENCES sleep_runs(id),

            snapshot            JSONB NOT NULL,

            risk_class          VARCHAR(10) NOT NULL,
            status              VARCHAR(20) NOT NULL,

            proposed_at         TIMESTAMP DEFAULT NOW(),
            decided_at          TIMESTAMP,
            decided_by          UUID REFERENCES users(id),
            applied_at          TIMESTAMP,

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
        "CREATE INDEX IF NOT EXISTS idx_config_versions_project_proposed "
        "ON config_versions(project_id, proposed_at DESC)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_config_versions_status "
        "ON config_versions(status) WHERE status = 'pending'"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_config_versions_sleep_run "
        "ON config_versions(sleep_run_id)"
    )

    # Append-only grants: pre-existing 'aether' role gets INSERT/SELECT/UPDATE.
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'aether') THEN
                EXECUTE 'GRANT INSERT, SELECT, UPDATE ON sleep_runs TO aether';
                EXECUTE 'GRANT INSERT, SELECT, UPDATE ON sleep_reflections TO aether';
                EXECUTE 'GRANT INSERT, SELECT, UPDATE ON config_versions TO aether';
                EXECUTE 'REVOKE DELETE ON sleep_runs FROM aether';
                EXECUTE 'REVOKE DELETE ON sleep_reflections FROM aether';
                EXECUTE 'REVOKE DELETE ON config_versions FROM aether';
            END IF;
        END
        $$;
        """
    )


# -----------------------------------------------------------------------------
# Downgrade
# -----------------------------------------------------------------------------
def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_config_versions_sleep_run")
    op.execute("DROP INDEX IF EXISTS idx_config_versions_status")
    op.execute("DROP INDEX IF EXISTS idx_config_versions_project_proposed")
    op.execute("DROP TABLE IF EXISTS config_versions")

    op.execute("DROP INDEX IF EXISTS idx_sleep_reflections_run")
    op.execute("DROP TABLE IF EXISTS sleep_reflections")

    op.execute("DROP INDEX IF EXISTS idx_sleep_runs_status_started")
    op.execute("DROP INDEX IF EXISTS idx_sleep_runs_user_started")
    op.execute("DROP INDEX IF EXISTS idx_sleep_runs_project_started")
    op.execute("DROP TABLE IF EXISTS sleep_runs")
