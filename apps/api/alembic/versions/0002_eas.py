"""0002_eas — Expert Advisor (EA) artifact table.

Adds the standalone, user-scoped ``eas`` table that persists user-authored
Expert Advisors whose body is the serialized React Flow graph
(``{nodes, edges}`` envelope as JSONB).

Modelled on the existing ``skills`` domain (the closest analogue: user-scoped,
reusable, versioned, soft-archive via ``is_active``). Per the locked decisions
for the ``ea-management`` change:

* EAs are flat and directly owned by a user — newTCN's hidden ``projects``
  container is intentionally NOT ported.
* ``user_id`` is ``NOT NULL`` with ``ON DELETE RESTRICT`` (charter tenancy
  invariant — you can't drop a user with live resources; soft-disable
  instead).
* This is a NEW forward migration chained off the squashed ``0001_init``;
  the squash is NOT edited.

Revision ID: 0002_eas
Revises: 0001_init
Create Date: 2026-06-19
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

# Alembic identifiers.
revision: str = "0002_eas"
down_revision: Union[str, Sequence[str], None] = "0001_init"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# -----------------------------------------------------------------------------
# Upgrade
# -----------------------------------------------------------------------------
def upgrade() -> None:
    # ------------------------------------------------------------------
    # eas  (user-scoped EA artifact — modelled on skills)
    # ------------------------------------------------------------------
    # The graph column holds the serialized React Flow graph as the
    # {nodes, edges} envelope. A fresh row defaults to an empty, valid
    # graph so the editor never has to special-case a NULL/absent body.
    op.execute(
        """
        CREATE TABLE eas (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id         UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,

            name            VARCHAR(100) NOT NULL,
            description     TEXT,

            -- Serialized React Flow graph: the {nodes, edges} envelope.
            graph           JSONB NOT NULL DEFAULT '{"nodes": [], "edges": []}'::jsonb,

            version         INTEGER NOT NULL DEFAULT 1,
            is_active       BOOLEAN NOT NULL DEFAULT true,

            created_at      TIMESTAMP DEFAULT NOW(),
            updated_at      TIMESTAMP DEFAULT NOW(),

            CONSTRAINT eas_version_positive CHECK (version >= 1)
        )
        """
    )
    op.execute(
        "CREATE INDEX idx_eas_user_active "
        "ON eas(user_id) WHERE is_active = true"
    )

    # Append-only / role grant — guarded so the migration is a no-op when
    # the optional 'aether' role is absent (CI / testcontainers). Mirrors
    # the guarded grant block in 0001_init.
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'aether') THEN
                EXECUTE 'GRANT INSERT, SELECT, UPDATE ON eas TO aether';
            END IF;
        END
        $$;
        """
    )


# -----------------------------------------------------------------------------
# Downgrade
# -----------------------------------------------------------------------------
def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS eas")
