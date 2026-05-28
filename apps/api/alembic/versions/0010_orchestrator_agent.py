"""0010_orchestrator_agent — promote Orchestrator to a definable agent.

Charter-level course correction. Original `0001_init` modelled only three
user-definable agent types (`worker | investigator | auditor`) on the
assumption that the Orquestador was the backend control plane and not a
row in `agents`. That interpretation was wrong: every project needs FOUR
agents — Orquestador + Investigador + Worker + Auditor — each a
reusable definition with its own `logica` body and its own per-project
parameter block.

Changes:

* `agents.agents_type_valid` CHECK is dropped and re-created to include
  the new value `'orchestrator'`. Pre-existing rows (worker /
  investigator / auditor) remain legal under the new constraint.
* `projects` gets two new columns:
    - `orchestrator_agent_id  UUID REFERENCES agents(id) ON DELETE RESTRICT`
      (NULLable like the other three FKs — existing rows keep NULL until
      the operator wires an orchestrator).
    - `orchestrator_params    JSONB NOT NULL DEFAULT '{}'::jsonb` to mirror
      the existing per-agent params blocks.

Backwards-compat note: no row migrations needed. Old projects keep
`orchestrator_agent_id = NULL` until the operator assigns one in the
dashboard.

Revision ID: 0010_orchestrator_agent
Revises: 0009_skills_md_and_agent_skills
Create Date: 2026-05-28
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

# Alembic identifiers.
revision: str = "0010_orchestrator_agent"
down_revision: str | Sequence[str] | None = "0009_skills_md_and_agent_skills"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# -----------------------------------------------------------------------------
# Upgrade
# -----------------------------------------------------------------------------
def upgrade() -> None:
    # ------------------------------------------------------------------
    # agents.type — extend the CHECK constraint with the new value.
    # ------------------------------------------------------------------
    # Drop the old CHECK and replace it with the wider 4-value version.
    # Existing rows stay legal because the previous set is a subset of
    # the new one.
    op.execute("ALTER TABLE agents DROP CONSTRAINT IF EXISTS agents_type_valid")
    op.execute(
        "ALTER TABLE agents ADD CONSTRAINT agents_type_valid "
        "CHECK (type IN ('orchestrator', 'worker', 'investigator', 'auditor'))"
    )

    # ------------------------------------------------------------------
    # projects — add the 4th FK + the matching JSONB params block.
    # ------------------------------------------------------------------
    op.execute(
        "ALTER TABLE projects "
        "ADD COLUMN IF NOT EXISTS orchestrator_agent_id UUID "
        "REFERENCES agents(id) ON DELETE RESTRICT"
    )
    op.execute(
        "ALTER TABLE projects "
        "ADD COLUMN IF NOT EXISTS orchestrator_params JSONB "
        "NOT NULL DEFAULT '{}'::jsonb"
    )


# -----------------------------------------------------------------------------
# Downgrade
# -----------------------------------------------------------------------------
def downgrade() -> None:
    # Reverse projects first — the FK depends on the CHECK staying wide
    # while we drop it, so order matters when an orchestrator row exists.
    op.execute(
        "ALTER TABLE projects DROP COLUMN IF EXISTS orchestrator_params"
    )
    op.execute(
        "ALTER TABLE projects DROP COLUMN IF EXISTS orchestrator_agent_id"
    )

    # Reverse the CHECK. NOTE: if any agent rows have type='orchestrator'
    # at downgrade time, the narrower CHECK will fail. This is a
    # best-effort symmetry — operators downgrading a database with
    # orchestrator agents are expected to migrate / delete those rows
    # first (same pattern as 0009's skills downgrade note).
    op.execute("ALTER TABLE agents DROP CONSTRAINT IF EXISTS agents_type_valid")
    op.execute(
        "ALTER TABLE agents ADD CONSTRAINT agents_type_valid "
        "CHECK (type IN ('worker', 'investigator', 'auditor'))"
    )
