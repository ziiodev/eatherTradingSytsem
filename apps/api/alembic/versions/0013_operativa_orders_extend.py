"""0013_operativa_orders_extend — extend ``orders`` for the Operativa surface.

The new Operativa tab needs fill / close timing + pricing + cost columns,
P&L breakdown, and two more lifecycle states (``closed``, ``cancelled``)
that the prior 6-state CHECK refused.

All additions are NULLABLE on purpose:

* A row is born at ``status='pending'`` before any open exists, so
  ``open_*`` / ``close_*`` are unknown.
* ``close_*`` and ``profit_*`` are populated only when the order
  transitions to ``closed``.
* Historical rows imported by the Auditor reconciler may legitimately
  miss broker-reported fields (the broker may not surface them).

Numeric scales follow the broker-precision convention already used in
this codebase: ``NUMERIC(18,8)`` for prices, ``NUMERIC(18,4)`` for
monetary aggregates.

The ``meta_data`` JSONB column is added with ``NOT NULL DEFAULT '{}'``
so the LiveBus + Worker proxies can attach broker payloads, mt5 deal
ids, and reconciliation breadcrumbs without a follow-up migration.

The ``status`` CHECK is dropped and re-created widened to:

    pending | approved_pending_send | filled | failed |
    rejected | expired | closed | cancelled

The prior 6-state set is a strict subset of the new 8-state set, so
existing rows remain legal under the new constraint.

Indexes added:

* ``idx_orders_project_status (project_id, status)`` — drives the
  Operativa filtered-list query when callers narrow by status.
* ``idx_orders_project_symbol (project_id, symbol)`` — same, narrowing
  by symbol.
* ``idx_orders_project_open_time (project_id, open_time DESC)`` —
  the canonical "all trades for this project, most-recent first"
  read path for the closed-trades history table.
* ``idx_orders_mt5_ticket (mt5_ticket) WHERE mt5_ticket IS NOT NULL`` —
  unique partial index. The MT5 broker assigns one ticket per fill;
  enforcing uniqueness here prevents two ORM rows from ever shadowing
  the same broker fill (a class of reconciler bug we explicitly want
  the DB to refuse).

Downgrade reverses every step in the inverse order. ``ALTER COLUMN
meta_data DROP DEFAULT`` then ``DROP COLUMN`` runs before the CHECK
roll-back so a row carrying a newly-allowed status (``closed``,
``cancelled``) at downgrade time would surface as a CHECK violation —
operators downgrading data with new statuses are expected to migrate
those rows first (same pattern as ``0010`` / ``0012``).

Revision ID: 0013_operativa_orders_extend
Revises: 0012_marker_and_tutor_agents
Create Date: 2026-05-29
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

# Alembic identifiers.
revision: str = "0013_operativa_orders_extend"
down_revision: str | Sequence[str] | None = "0012_marker_and_tutor_agents"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# -----------------------------------------------------------------------------
# Upgrade
# -----------------------------------------------------------------------------
def upgrade() -> None:
    # ------------------------------------------------------------------
    # 8 new NULLABLE columns.
    # ------------------------------------------------------------------
    op.execute("ALTER TABLE orders ADD COLUMN IF NOT EXISTS open_time TIMESTAMPTZ NULL")
    op.execute("ALTER TABLE orders ADD COLUMN IF NOT EXISTS open_price NUMERIC(18,8) NULL")
    op.execute("ALTER TABLE orders ADD COLUMN IF NOT EXISTS close_time TIMESTAMPTZ NULL")
    op.execute("ALTER TABLE orders ADD COLUMN IF NOT EXISTS close_price NUMERIC(18,8) NULL")
    op.execute("ALTER TABLE orders ADD COLUMN IF NOT EXISTS commission NUMERIC(18,4) NULL")
    op.execute("ALTER TABLE orders ADD COLUMN IF NOT EXISTS swap NUMERIC(18,4) NULL")
    op.execute("ALTER TABLE orders ADD COLUMN IF NOT EXISTS profit_gross NUMERIC(18,4) NULL")
    op.execute("ALTER TABLE orders ADD COLUMN IF NOT EXISTS profit_net NUMERIC(18,4) NULL")

    # ------------------------------------------------------------------
    # meta_data JSONB NOT NULL DEFAULT '{}'::jsonb.
    #
    # ADD COLUMN with the default is atomic in Postgres (the constant
    # default is recorded in the catalog without rewriting existing
    # rows). We then assert the DEFAULT clause is set in case a prior
    # migration created the column without one.
    # ------------------------------------------------------------------
    op.execute(
        "ALTER TABLE orders ADD COLUMN IF NOT EXISTS meta_data JSONB NOT NULL DEFAULT '{}'::jsonb"
    )
    op.execute("ALTER TABLE orders ALTER COLUMN meta_data SET DEFAULT '{}'::jsonb")

    # ------------------------------------------------------------------
    # status CHECK — widen to 8 values.
    #
    # The original migration ``0007_orders_and_approvals`` declared the
    # CHECK inline in the ``CREATE TABLE`` (no explicit name), so
    # Postgres auto-named it ``orders_status_check``. We drop BOTH the
    # auto-named and the explicit-named (``orders_status_valid``) forms
    # in case a partial earlier migration ran. The replacement carries
    # the explicit name so future migrations have a stable handle.
    # ------------------------------------------------------------------
    op.execute("ALTER TABLE orders DROP CONSTRAINT IF EXISTS orders_status_check")
    op.execute("ALTER TABLE orders DROP CONSTRAINT IF EXISTS orders_status_valid")
    op.execute(
        "ALTER TABLE orders ADD CONSTRAINT orders_status_valid "
        "CHECK (status IN ("
        "'pending', 'approved_pending_send', 'filled', 'failed', "
        "'rejected', 'expired', 'closed', 'cancelled'))"
    )

    # ------------------------------------------------------------------
    # Indexes. All IF NOT EXISTS — replayable.
    # ------------------------------------------------------------------
    op.execute("CREATE INDEX IF NOT EXISTS idx_orders_project_status ON orders(project_id, status)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_orders_project_symbol ON orders(project_id, symbol)")
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_orders_project_open_time "
        "ON orders(project_id, open_time DESC)"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_orders_mt5_ticket "
        "ON orders(mt5_ticket) WHERE mt5_ticket IS NOT NULL"
    )


# -----------------------------------------------------------------------------
# Downgrade
# -----------------------------------------------------------------------------
def downgrade() -> None:
    # Drop indexes first — they reference columns we may be about to remove.
    op.execute("DROP INDEX IF EXISTS idx_orders_mt5_ticket")
    op.execute("DROP INDEX IF EXISTS idx_orders_project_open_time")
    op.execute("DROP INDEX IF EXISTS idx_orders_project_symbol")
    op.execute("DROP INDEX IF EXISTS idx_orders_project_status")

    # Roll the CHECK back to the prior 6-value set. NOTE: rows with
    # ``status IN ('closed','cancelled')`` will fail this — operators
    # must migrate those rows before downgrading.
    #
    # We re-add the constraint under the ORIGINAL auto-generated name
    # (``orders_status_check``) so a subsequent ``alembic downgrade ...``
    # leaves the table identical to its pre-0013 shape (the original
    # migration in 0007 declared the CHECK inline, which Postgres
    # auto-names ``<table>_<column>_check``).
    op.execute("ALTER TABLE orders DROP CONSTRAINT IF EXISTS orders_status_valid")
    op.execute("ALTER TABLE orders DROP CONSTRAINT IF EXISTS orders_status_check")
    op.execute(
        "ALTER TABLE orders ADD CONSTRAINT orders_status_check "
        "CHECK (status IN ("
        "'pending', 'approved_pending_send', 'filled', 'failed', "
        "'rejected', 'expired'))"
    )

    # Drop the meta_data column.
    op.execute("ALTER TABLE orders DROP COLUMN IF EXISTS meta_data")

    # Drop the 8 new columns (inverse order).
    op.execute("ALTER TABLE orders DROP COLUMN IF EXISTS profit_net")
    op.execute("ALTER TABLE orders DROP COLUMN IF EXISTS profit_gross")
    op.execute("ALTER TABLE orders DROP COLUMN IF EXISTS swap")
    op.execute("ALTER TABLE orders DROP COLUMN IF EXISTS commission")
    op.execute("ALTER TABLE orders DROP COLUMN IF EXISTS close_price")
    op.execute("ALTER TABLE orders DROP COLUMN IF EXISTS close_time")
    op.execute("ALTER TABLE orders DROP COLUMN IF EXISTS open_price")
    op.execute("ALTER TABLE orders DROP COLUMN IF EXISTS open_time")
