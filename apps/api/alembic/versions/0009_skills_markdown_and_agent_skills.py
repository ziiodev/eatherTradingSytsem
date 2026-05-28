"""0009_skills_markdown_and_agent_skills — skills go markdown-by-default + agent_skills join table.

Charter correction follow-up to ``0003_skills``:

* Skills are now **markdown-by-default** capability artifacts (prompts,
  decision frameworks, entry/exit rules). Python is still legal for
  computational skills (indicators, correlation calculators, risk math)
  but is no longer the only runtime — the ``skills_runtime_only_python``
  CHECK is replaced by ``skills_runtime_valid CHECK (runtime IN
  ('markdown','python'))`` and the default flips to ``'markdown'``.

* Agents reference skills via a new ``agent_skills`` join table. Each
  binding is a per-(agent_id, skill_id) pair with an optional ``notes``
  field for agent-side context. CASCADE from agents (deleting an agent
  removes its bindings); RESTRICT from skills (cannot delete a skill that
  is still attached — same pattern as projects → agents). Multi-tenant
  integrity is enforced at the application layer: ``agent.user_id`` and
  ``skill.user_id`` must match ``current_user.id`` before the row is
  inserted.

Revision ID: 0009_skills_markdown_and_agent_skills
Revises: 0008_sleep_phase
Create Date: 2026-05-28
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

# Alembic identifiers.
revision: str = "0009_skills_md_and_agent_skills"
down_revision: str | Sequence[str] | None = "0008_sleep_phase"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# -----------------------------------------------------------------------------
# Upgrade
# -----------------------------------------------------------------------------
def upgrade() -> None:
    # ------------------------------------------------------------------
    # skills.runtime: drop python-only CHECK, relax default to markdown,
    # then add the markdown-or-python CHECK.
    # ------------------------------------------------------------------
    # Pre-existing rows (if any) carrying runtime='python' are LEFT AS-IS —
    # they remain legal under the new CHECK. Only the default flips.
    op.execute(
        "ALTER TABLE skills DROP CONSTRAINT IF EXISTS skills_runtime_only_python"
    )
    op.execute("ALTER TABLE skills ALTER COLUMN runtime SET DEFAULT 'markdown'")
    op.execute(
        "ALTER TABLE skills ADD CONSTRAINT skills_runtime_valid "
        "CHECK (runtime IN ('markdown', 'python'))"
    )

    # ------------------------------------------------------------------
    # agent_skills: per-(agent, skill) binding.
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_skills (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),

            -- CASCADE from agents: deleting an agent removes all its
            -- bindings. The skill itself survives.
            agent_id    UUID NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
            -- RESTRICT from skills: a skill that is still attached to an
            -- agent cannot be hard-deleted. The router catches the
            -- ``IntegrityError`` and maps it to a 409. Same pattern as
            -- projects → agents.
            skill_id    UUID NOT NULL REFERENCES skills(id) ON DELETE RESTRICT,

            notes       TEXT,

            created_at  TIMESTAMP NOT NULL DEFAULT NOW(),

            CONSTRAINT uq_agent_skills_pair UNIQUE (agent_id, skill_id)
        )
        """
    )

    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_agent_skills_agent "
        "ON agent_skills(agent_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_agent_skills_skill "
        "ON agent_skills(skill_id)"
    )


# -----------------------------------------------------------------------------
# Downgrade
# -----------------------------------------------------------------------------
def downgrade() -> None:
    # Reverse agent_skills first (skills CHECK depends on no markdown rows
    # being present — best-effort, see note below).
    op.execute("DROP INDEX IF EXISTS idx_agent_skills_skill")
    op.execute("DROP INDEX IF EXISTS idx_agent_skills_agent")
    op.execute("DROP TABLE IF EXISTS agent_skills")

    # Reverse the skills.runtime relaxation. NOTE: if rows with
    # runtime='markdown' exist at downgrade time, adding the python-only
    # CHECK will fail. The downgrade is a best-effort symmetry — operators
    # downgrading a database with markdown skills are expected to migrate
    # those rows first.
    op.execute(
        "ALTER TABLE skills DROP CONSTRAINT IF EXISTS skills_runtime_valid"
    )
    op.execute("ALTER TABLE skills ALTER COLUMN runtime SET DEFAULT 'python'")
    op.execute(
        "ALTER TABLE skills ADD CONSTRAINT skills_runtime_only_python "
        "CHECK (runtime = 'python')"
    )
