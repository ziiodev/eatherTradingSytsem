"""0003_skills — add ``skills`` table for the v1 skills catalog.

Stores user-scoped, named, versioned Python-callable capability
definitions (indicator / data_source / analytic / executor / risk).
Mirror of the ``agents`` shape, with the divergence that each skill
carries a typed input/output signature as JSONB.

NOTE on chain ordering:

    Two parallel changes (observability → ``0002_audit_log``,
    skills-catalog → this file) both target the slot directly after
    ``0001_init``. The orchestrator assigned the slots as:

        0001_init  →  0002_audit_log  →  0003_skills

    However at the moment this change lands the observability migration
    is still in flight, so ``down_revision`` is wired to ``0001_init``
    as a fallback. When ``0002_audit_log`` lands, EITHER change MAY
    re-chain by editing ``down_revision`` here to ``"0002_audit_log"``.
    Both files have idempotent ``upgrade()``/``downgrade()`` bodies, so
    re-chaining is a one-line edit and a regen of the SQL snapshot.

Revision ID: 0003_skills
Revises: 0002_audit_log
Create Date: 2026-05-28
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

# Alembic identifiers.
revision: str = "0003_skills"
# Re-stitched after observability landed: previously pointed at
# ``0001_init`` while two parallel changes raced for the slot. See the
# NOTE at the top of the docstring for the original chain dance.
down_revision: Union[str, Sequence[str], None] = "0002_audit_log"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# -----------------------------------------------------------------------------
# Upgrade
# -----------------------------------------------------------------------------
def upgrade() -> None:
    # Idempotent guard: re-running this migration on a DB that already
    # has the table is a no-op (CREATE TABLE IF NOT EXISTS), as required
    # by the spec's "idempotent on re-run" clause.
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS skills (
            id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id             UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,

            -- Identificacion
            name                VARCHAR(100) NOT NULL,
            type                VARCHAR(20)  NOT NULL,
            description         TEXT,

            -- Versionado y estado
            version             INTEGER NOT NULL DEFAULT 1,

            -- Cuerpo ejecutable y runtime
            code                TEXT    NOT NULL,
            runtime             VARCHAR(20) NOT NULL DEFAULT 'python',

            -- Firma tipada (slim TypedDict-like JSONB)
            input_signature     JSONB NOT NULL DEFAULT '{}'::jsonb,
            output_signature    JSONB NOT NULL DEFAULT '{}'::jsonb,

            -- Estado
            is_active           BOOLEAN NOT NULL DEFAULT true,

            -- Fechas
            created_at          TIMESTAMP DEFAULT NOW(),
            updated_at          TIMESTAMP DEFAULT NOW(),

            CONSTRAINT skills_type_valid
                CHECK (type IN ('indicator', 'data_source', 'analytic', 'executor', 'risk')),
            CONSTRAINT skills_runtime_only_python
                CHECK (runtime = 'python')
        )
        """
    )

    # Tenant-scoped read index — every query goes through
    # ``WHERE user_id = :uid``; this index is what makes
    # the list endpoint cheap regardless of catalog size.
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_skills_user_active "
        "ON skills(user_id) WHERE is_active = true"
    )


# -----------------------------------------------------------------------------
# Downgrade
# -----------------------------------------------------------------------------
def downgrade() -> None:
    # Reverse what upgrade() did. No CASCADE — if a future migration
    # adds a table that references skills(id) it MUST own dropping that
    # FK in its own downgrade; we do not want a silent dependency nuke.
    op.execute("DROP INDEX IF EXISTS idx_skills_user_active")
    op.execute("DROP TABLE IF EXISTS skills")
