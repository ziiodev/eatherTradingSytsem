"""0012_marker_and_tutor_agents — split Investigador and add Tutor.

Charter-level course correction. After ``0010_orchestrator_agent`` the
system had 1 Orquestador + 3 specialised agents (Investigador / Worker /
Auditor). Operational experience reshaped the topology:

* The **Investigador** is now scoped to **news only** — it reads and
  summarises every news source the project subscribes to. Its prior
  market-signal responsibility moves out.
* The new **Marker** agent owns market-signal generation: it emits the
  current market regime + the option the system should switch on. This
  is exactly what the previous Investigador used to do; we keep the
  Investigador rows around (no retro-mutation) and add ``marker`` as a
  first-class type so operators can re-tag manually if they wish.
* The new **Tutor** agent owns the Sleep Phase mechanics (Micro /
  Profundo / Crítico). The Orquestador still supervises and applies the
  derived proposals, but the learning loop runtime lives in the Tutor.
* The **Auditor** scope expands to include q-table review and MT5
  broker reports in addition to the original risk/threshold duties.
  No DDL change is needed for that scope expansion — just docs.

Changes:

* ``agents.agents_type_valid`` CHECK is dropped and re-created to include
  ``'marker'`` and ``'tutor'``. Pre-existing rows (orchestrator /
  worker / investigator / auditor) remain legal under the new
  constraint.
* ``projects`` gets four new columns:
    - ``marker_agent_id  UUID REFERENCES agents(id) ON DELETE RESTRICT``
    - ``marker_params    JSONB NOT NULL DEFAULT '{}'::jsonb``
    - ``tutor_agent_id   UUID REFERENCES agents(id) ON DELETE RESTRICT``
    - ``tutor_params     JSONB NOT NULL DEFAULT '{}'::jsonb``

Backwards-compat note: no row migrations needed. Existing projects keep
``marker_agent_id = NULL`` and ``tutor_agent_id = NULL`` until the
operator wires them. Existing ``agents`` rows with ``type='investigator'``
KEEP that type — the semantic shift is operator-driven, not migrated
automatically.

Downgrade caveat: if any row in ``agents`` carries ``type='marker'`` or
``type='tutor'`` at downgrade time, the narrower CHECK re-add will fail
with an integrity error. Operators must archive / re-tag those rows
before downgrading (same pattern as 0010's note).

Revision ID: 0012_marker_and_tutor_agents
Revises: 0011_sleep_learning_loop
Create Date: 2026-05-29
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

# Alembic identifiers.
revision: str = "0012_marker_and_tutor_agents"
down_revision: str | Sequence[str] | None = "0011_sleep_learning_loop"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# -----------------------------------------------------------------------------
# Upgrade
# -----------------------------------------------------------------------------
def upgrade() -> None:
    # ------------------------------------------------------------------
    # agents.type — extend the CHECK constraint with the two new values.
    # ------------------------------------------------------------------
    # Drop the old CHECK and replace it with the wider 6-value version.
    # Existing rows stay legal because the previous set is a subset of
    # the new one.
    op.execute("ALTER TABLE agents DROP CONSTRAINT IF EXISTS agents_type_valid")
    op.execute(
        "ALTER TABLE agents ADD CONSTRAINT agents_type_valid "
        "CHECK (type IN ('orchestrator', 'investigator', 'marker', "
        "'worker', 'tutor', 'auditor'))"
    )

    # ------------------------------------------------------------------
    # projects — add the Marker + Tutor FKs and matching JSONB params.
    # ------------------------------------------------------------------
    op.execute(
        "ALTER TABLE projects "
        "ADD COLUMN IF NOT EXISTS marker_agent_id UUID "
        "REFERENCES agents(id) ON DELETE RESTRICT"
    )
    op.execute(
        "ALTER TABLE projects "
        "ADD COLUMN IF NOT EXISTS marker_params JSONB "
        "NOT NULL DEFAULT '{}'::jsonb"
    )
    op.execute(
        "ALTER TABLE projects "
        "ADD COLUMN IF NOT EXISTS tutor_agent_id UUID "
        "REFERENCES agents(id) ON DELETE RESTRICT"
    )
    op.execute(
        "ALTER TABLE projects "
        "ADD COLUMN IF NOT EXISTS tutor_params JSONB "
        "NOT NULL DEFAULT '{}'::jsonb"
    )


# -----------------------------------------------------------------------------
# Downgrade
# -----------------------------------------------------------------------------
def downgrade() -> None:
    # Reverse projects first — the FKs depend on the CHECK staying wide
    # while we drop them, so order matters when marker/tutor rows exist.
    op.execute("ALTER TABLE projects DROP COLUMN IF EXISTS tutor_params")
    op.execute("ALTER TABLE projects DROP COLUMN IF EXISTS tutor_agent_id")
    op.execute("ALTER TABLE projects DROP COLUMN IF EXISTS marker_params")
    op.execute("ALTER TABLE projects DROP COLUMN IF EXISTS marker_agent_id")

    # Reverse the CHECK. NOTE: if any agent rows have type='marker' or
    # type='tutor' at downgrade time, the narrower CHECK will fail. This
    # is a best-effort symmetry — operators downgrading a database with
    # marker / tutor agents are expected to migrate / delete those rows
    # first (same pattern as 0010 / 0011's downgrade notes).
    op.execute("ALTER TABLE agents DROP CONSTRAINT IF EXISTS agents_type_valid")
    op.execute(
        "ALTER TABLE agents ADD CONSTRAINT agents_type_valid "
        "CHECK (type IN ('orchestrator', 'worker', 'investigator', 'auditor'))"
    )
