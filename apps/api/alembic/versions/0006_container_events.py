"""0006_container_events — per-project Docker lifecycle audit trail.

Every successful (and many failed) lifecycle calls that the
``docker_control`` module makes against the docker-socket-proxy write
exactly one row here. The shape is intentionally narrow:

* ``user_id`` / ``project_id`` — RESTRICT FKs. A project (or user) that
  has any audit history cannot be hard-deleted: the application surfaces
  that as a 409 in the projects/users delete flows. The router contract
  for ``DELETE /api/projects/{id}`` already refuses while
  ``container_id IS NOT NULL`` — this RESTRICT is the belt to that
  suspender at the DB layer.
* ``action`` — verb (``"build"``, ``"create"``, ``"start"``, ``"pause"``,
  ``"unpause"``, ``"stop"``, ``"recreate"``, ``"remove"``,
  ``"reconcile_drift"``). Free-form VARCHAR(50); no DB CHECK so the next
  reconciler verb doesn't require a migration.
* ``status`` — ``"ok"`` / ``"error"`` for build/start/etc., or
  ``"observed"`` for reconcile rows. Free-form VARCHAR(20).
* ``payload`` — JSONB envelope. Callers PII-scrub before writing.
* ``error`` — optional TEXT error message captured from aiodocker.
* ``created_at`` — TIMESTAMP DEFAULT NOW(), unindexed at the column level
  but covered by the composite index ``(project_id, created_at DESC)``
  which drives the project infraestructura panel's events feed.

NOTE on chain ordering:

    Three changes (sandbox → ``0004_agent_runs``, mfa-totp → ``0005_*``,
    this → ``0006_container_events``) landed in the same wave. The
    orchestrator slot allocation is:

        0004_agent_runs → 0005_mfa_totp → 0006_container_events

    However at the moment this change opens, ``0005_*`` is still
    in flight on the mfa-totp sibling, so ``down_revision`` is wired to
    ``0004_agent_runs`` as a fallback. When ``0005_*`` lands, either
    change MAY re-stitch by editing ``down_revision`` here to point at
    the mfa migration. Both ``upgrade()`` / ``downgrade()`` bodies are
    idempotent, so re-stitching is a one-line edit.

Revision ID: 0006_container_events
Revises: 0004_agent_runs
Create Date: 2026-05-28
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

# Alembic identifiers.
revision: str = "0006_container_events"
# Chain re-stitched to land directly on top of the mfa-totp sibling's
# 0005_mfa_recovery_codes — the slot allocation was 0004 → 0005 → 0006
# and the parallel wave landed 0005_mfa_recovery_codes at apply time.
# Both this file and 0005 have idempotent upgrade()/downgrade() bodies.
down_revision: str | Sequence[str] | None = "0005_mfa_recovery_codes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# -----------------------------------------------------------------------------
# Upgrade
# -----------------------------------------------------------------------------
def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS container_events (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),

            -- RESTRICT: a project (or user) that has lifecycle audit
            -- history cannot be hard-deleted. The application maps that
            -- to HTTP 409 in the project DELETE handler.
            project_id      UUID NOT NULL
                              REFERENCES projects(id) ON DELETE RESTRICT,
            user_id         UUID NOT NULL
                              REFERENCES users(id) ON DELETE RESTRICT,

            -- Verb. Free-form so the next reconciler verb is a code
            -- change, not a migration.
            action          VARCHAR(50) NOT NULL,

            -- Free-form: typical values "ok" | "error" | "observed".
            status          VARCHAR(20) NOT NULL,

            -- Caller-scrubbed envelope (e.g. {"container_id": "...",
            -- "image": "...", "from_status": "...", "to_status": "..."}).
            payload         JSONB NOT NULL DEFAULT '{}'::jsonb,

            -- Optional aiodocker / proxy error tail.
            error           TEXT,

            created_at      TIMESTAMP DEFAULT NOW()
        )
        """
    )

    # Composite index driving the project infraestructura events feed.
    # DESC on created_at because the dashboard reads newest-first.
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_container_events_project_created "
        "ON container_events(project_id, created_at DESC)"
    )

    # Secondary index for the per-user lifecycle audit view.
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_container_events_user_created "
        "ON container_events(user_id, created_at DESC)"
    )


# -----------------------------------------------------------------------------
# Downgrade
# -----------------------------------------------------------------------------
def downgrade() -> None:
    # Drop indexes first so the table drop is a single fast operation.
    op.execute("DROP INDEX IF EXISTS idx_container_events_user_created")
    op.execute("DROP INDEX IF EXISTS idx_container_events_project_created")
    op.execute("DROP TABLE IF EXISTS container_events")
