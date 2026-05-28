"""0002_audit_log — append-only audit trail.

Charter mandate: "Every action must be logged with reasoning"; the audit
trail is a hard requirement. This migration introduces the dedicated
``audit_log`` table and locks down its grants so the application role
can INSERT and SELECT but cannot UPDATE or DELETE.

Key shape choices (mirroring :mod:`aether_api.models.audit_log`):

* ``user_id`` is NULLABLE — system actions (sleep-phase reflection,
  scheduled jobs) have no human caller but still want a row.
* ``user_id`` references ``users(id) ON DELETE RESTRICT`` so a user
  with audit history cannot be hard-deleted.
* ``before_state`` / ``after_state`` are JSONB so we can store arbitrary
  domain shapes without DDL churn. Callers are expected to PII-scrub
  before writing.
* Two indexes mirror the dashboard query patterns:
    - ``(user_id, created_at DESC)`` for the per-user audit page.
    - ``(action, created_at DESC)`` for action-type analytics.

GRANT / REVOKE block at the end is wrapped in ``DO $$ ... $$`` blocks
guarded by ``has_table_privilege`` so the migration is idempotent and
no-ops when the ``aether`` role is absent (e.g. CI containers running
as the superuser). Without an aether role the grants cannot apply; the
constraints are documented in the comment for the operator.

Revision ID: 0002_audit_log
Revises: 0001_init
Create Date: 2026-05-28
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

# Alembic identifiers.
revision: str = "0002_audit_log"
down_revision: Union[str, Sequence[str], None] = "0001_init"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# -----------------------------------------------------------------------------
# Upgrade
# -----------------------------------------------------------------------------
def upgrade() -> None:
    # ------------------------------------------------------------------
    # audit_log table
    # ------------------------------------------------------------------
    op.execute(
        """
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
        )
        """
    )

    # ------------------------------------------------------------------
    # Indexes — mirror the dashboard query patterns.
    # ------------------------------------------------------------------
    op.execute(
        "CREATE INDEX idx_audit_log_user_created "
        "ON audit_log(user_id, created_at DESC)"
    )
    op.execute(
        "CREATE INDEX idx_audit_log_action_created "
        "ON audit_log(action, created_at DESC)"
    )

    # ------------------------------------------------------------------
    # Append-only grants — application role can INSERT/SELECT but never
    # UPDATE/DELETE.
    # ------------------------------------------------------------------
    # Guarded by a role-existence check so the migration is a no-op when
    # the ``aether`` role is absent (CI, test containers running as the
    # postgres superuser). In those environments the append-only invariant
    # is documented but not enforced at the DB layer — that's fine, the
    # production deployment is where the grants matter.
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'aether') THEN
                EXECUTE 'GRANT INSERT, SELECT ON audit_log TO aether';
                EXECUTE 'REVOKE UPDATE, DELETE ON audit_log FROM aether';
            END IF;
        END
        $$;
        """
    )


# -----------------------------------------------------------------------------
# Downgrade
# -----------------------------------------------------------------------------
def downgrade() -> None:
    # Drop indexes first so the table drop is a single fast operation.
    op.execute("DROP INDEX IF EXISTS idx_audit_log_action_created")
    op.execute("DROP INDEX IF EXISTS idx_audit_log_user_created")
    op.execute("DROP TABLE IF EXISTS audit_log")
