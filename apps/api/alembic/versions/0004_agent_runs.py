"""0004_agent_runs — append-only audit table for sandboxed agent executions.

Every invocation of ``aether_api.sandbox.engine.run_agent(...)`` writes a
row here. The shape is intentionally narrow:

* ``user_id`` / ``agent_id`` / ``project_id`` — RESTRICT FKs so a sandbox
  run history pins its referents against hard deletion (the application
  surfaces a 409 in those flows).
* ``status`` — terminal disposition of the run. CHECK-constrained to the
  closed enum the sandbox engine produces:

      running        — in-flight, set on INSERT, replaced on completion.
      success        — child returned cleanly.
      denied_import  — module allowlist tripped.
      denied_network — socket guard tripped.
      denied_file    — RLIMIT_FSIZE / file open denied.
      timeout        — CPU or wall-clock deadline exceeded.
      oom            — RLIMIT_AS tripped.
      error          — uncaught exception inside the child.

* ``stdout`` / ``stderr`` — captured pipe drains, tail-truncated by the
  engine before INSERT. Stored as TEXT (NOT JSONB) because the engine
  also captures non-UTF8 bytes that we want to keep loss-free.
* ``denial_reason`` — short opaque marker (e.g. ``"import:ctypes"``) the
  child pipes back when a guard fires; saves the UI from re-parsing
  ``stderr``.
* ``resource_usage`` — JSONB of the parent's view of the child:
  ``{"cpu_seconds": ..., "max_rss_kb": ..., "wall_seconds": ...,
  "exit_signal": "SIGKILL"|None}``. Shape is informational, NOT
  schema-locked — the engine adds keys as observability grows.

Append-only at the application layer (no UPDATE/DELETE endpoint); the
prod GRANT block follows the same shape as ``0002_audit_log``.

Revision ID: 0004_agent_runs
Revises: 0003_skills
Create Date: 2026-05-28
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

# Alembic identifiers.
revision: str = "0004_agent_runs"
# Chained off the current head (skills landed last; observability landed
# before it). If a sibling change re-stitches the head, edit ONLY this
# line — upgrade()/downgrade() are idempotent.
down_revision: Union[str, Sequence[str], None] = "0003_skills"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# -----------------------------------------------------------------------------
# Upgrade
# -----------------------------------------------------------------------------
def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_runs (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),

            -- RESTRICT on all three: an audit row pins its referents.
            user_id         UUID NOT NULL REFERENCES users(id)    ON DELETE RESTRICT,
            agent_id        UUID NOT NULL REFERENCES agents(id)   ON DELETE RESTRICT,
            project_id      UUID NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,

            -- Lifecycle timestamps. ``started_at`` defaults to NOW() so a
            -- pre-INSERT row that fails to update ``ended_at`` is still
            -- queryable as "running" via the CHECK below.
            started_at      TIMESTAMP NOT NULL DEFAULT NOW(),
            ended_at        TIMESTAMP,

            -- Closed-enum status. ``running`` is the only state allowed
            -- with NULL ended_at; everything else is terminal.
            status          VARCHAR(20) NOT NULL,

            -- Child exit code (None on signal kill — see resource_usage.exit_signal).
            exit_code       INTEGER,

            -- Captured pipe drains. Tail-truncated by the engine.
            stdout          TEXT,
            stderr          TEXT,

            -- Short opaque marker piped from the child when a guard fires.
            denial_reason   TEXT,

            -- Parent-view resource accounting.
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

    # Tenant-scoped list query: every UI surface filters by user_id and
    # orders by recency. Partial index keeps the common case cheap.
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_agent_runs_user_started "
        "ON agent_runs(user_id, started_at DESC)"
    )
    # Per-agent history view (GET /api/agents/{id}/runs).
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_agent_runs_agent_started "
        "ON agent_runs(agent_id, started_at DESC)"
    )
    # Per-project history view (used by the Sleep Phase replay walker).
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_agent_runs_project_started "
        "ON agent_runs(project_id, started_at DESC)"
    )

    # Append-only grants. Same idempotent guard as audit_log.
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'aether') THEN
                EXECUTE 'GRANT INSERT, SELECT, UPDATE ON agent_runs TO aether';
                EXECUTE 'REVOKE DELETE ON agent_runs FROM aether';
            END IF;
        END
        $$;
        """
    )
    # Note on UPDATE: the engine inserts a row with status='running' before
    # spawning the child and updates it to the terminal status on completion.
    # DELETE is forbidden so a successful run is never erasable post-hoc.


# -----------------------------------------------------------------------------
# Downgrade
# -----------------------------------------------------------------------------
def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_agent_runs_project_started")
    op.execute("DROP INDEX IF EXISTS idx_agent_runs_agent_started")
    op.execute("DROP INDEX IF EXISTS idx_agent_runs_user_started")
    op.execute("DROP TABLE IF EXISTS agent_runs")
