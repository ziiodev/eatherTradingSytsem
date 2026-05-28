"""0007_orders_and_approvals — live-trading order book + audit + approvals.

Adds three tables introduced by the ``mt5-integration`` change:

* ``orders``         — every order the system has placed (or attempted to
  place) against MT5. Holds the broker ticket once filled. SL is
  ``NOT NULL`` because the CHARTER mandates a stop-loss on every order;
  refusing it at the DB layer prevents a buggy code path from inserting a
  zero-risk row at all.
* ``order_log``      — append-only audit trail. Rows are written in two
  phases: ``status='pending'`` BEFORE the MCP call and updated to one of
  ``filled|failed|blocked`` AFTER. The pre-call write means we always
  have a forensic record even if the API process dies between the call
  and the response.
* ``order_approvals``— large-order / out-of-band approval workflow. Any
  order the RiskEnforcer flags as "needs approval" lands here first; the
  Worker / admin operator decides.

Tenant scoping:

* All three tables carry ``project_id`` + ``user_id``, both RESTRICT FKs
  so a tenant or project that has any order history cannot be
  hard-deleted. The application surfaces that as a 409.
* Composite index ``(project_id, created_at DESC)`` on every table —
  drives the ``/operativa`` panel's lists.

Reversibility: ``downgrade()`` drops the three tables in dependency
order (``order_log`` references ``orders``).

Revision ID: 0007_orders_and_approvals
Revises: 0006_container_events
Create Date: 2026-05-28
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

# Alembic identifiers.
revision: str = "0007_orders_and_approvals"
down_revision: str | Sequence[str] | None = "0006_container_events"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# -----------------------------------------------------------------------------
# Upgrade
# -----------------------------------------------------------------------------
def upgrade() -> None:
    # --- orders ---------------------------------------------------------
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS orders (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),

            -- Tenant scope. RESTRICT both: an order is forensic evidence;
            -- you cannot hard-delete the project or user that owns it.
            project_id      UUID NOT NULL
                              REFERENCES projects(id) ON DELETE RESTRICT,
            user_id         UUID NOT NULL
                              REFERENCES users(id) ON DELETE RESTRICT,

            -- Optional: which Worker agent decided this order. NULL when
            -- a human operator placed it directly. ON DELETE SET NULL so
            -- archiving an agent does not destroy the order trail.
            agent_id        UUID
                              REFERENCES agents(id) ON DELETE SET NULL,

            symbol          VARCHAR(20) NOT NULL,
            side            VARCHAR(10) NOT NULL
                              CHECK (side IN ('buy','sell')),
            volume          DECIMAL(10,4) NOT NULL CHECK (volume > 0),

            -- CHARTER: SL is mandatory on every order. NOT NULL is the
            -- belt to the application-layer guard (RiskEnforcer +
            -- mt5_place_order schema). Three layers; one is enough but
            -- defence in depth survives one of them being skipped.
            sl              DECIMAL(15,5) NOT NULL,
            tp              DECIMAL(15,5),

            -- Broker-assigned ticket. NULL until the order is filled.
            mt5_ticket      BIGINT,

            -- Lifecycle:
            --   pending                 — accepted by the API, not yet sent.
            --   approved_pending_send   — approval gate cleared; about to ship.
            --   filled                  — broker confirmed.
            --   failed                  — broker rejected (see order_log).
            --   rejected                — RiskEnforcer / ApprovalGate refused.
            --   expired                 — approval was not granted in time.
            status          VARCHAR(20) NOT NULL
                              CHECK (status IN (
                                'pending',
                                'approved_pending_send',
                                'filled',
                                'failed',
                                'rejected',
                                'expired'
                              )),

            comment         VARCHAR(255),
            magic           INTEGER,

            created_at      TIMESTAMP DEFAULT NOW(),
            filled_at       TIMESTAMP
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_orders_project_created "
        "ON orders(project_id, created_at DESC)"
    )

    # --- order_log ------------------------------------------------------
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS order_log (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),

            -- Two-phase write: order_log is created in phase 1 with
            -- status='pending' and order_id == NULL when the order
            -- itself has not been inserted yet (e.g. approval-pending
            -- path). When the order row exists we link via CASCADE so
            -- order deletion (currently not permitted) would not orphan
            -- log rows.
            order_id        UUID
                              REFERENCES orders(id) ON DELETE CASCADE,

            project_id      UUID NOT NULL
                              REFERENCES projects(id) ON DELETE RESTRICT,
            user_id         UUID NOT NULL
                              REFERENCES users(id) ON DELETE RESTRICT,

            -- e.g. 'place_order', 'modify_order', 'close_order',
            -- 'risk_check', 'approval_request'. Free-form VARCHAR(50);
            -- mirroring container_events' philosophy.
            action          VARCHAR(50) NOT NULL,

            -- Inbound payload (the RiskEnforcer + ApprovalGate decision
            -- inputs) and outbound result (MT5's order_send response or
            -- the typed error). RiskCheckResult lives in its own column
            -- so we can index / query by individual reasons later.
            payload_in      JSONB,
            payload_out     JSONB,
            risk_check      JSONB,

            -- Lifecycle of the LOG row itself (not the order).
            --   pending — written in phase 1 (before MCP call).
            --   filled  — phase 2 update after a successful broker fill.
            --   failed  — phase 2 update after a broker rejection.
            --   blocked — RiskEnforcer / ApprovalGate refused; phase 1
            --             is the only write that ever lands.
            status          VARCHAR(20) NOT NULL
                              CHECK (status IN (
                                'pending',
                                'filled',
                                'failed',
                                'blocked'
                              )),
            error           TEXT,

            created_at      TIMESTAMP DEFAULT NOW()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_order_log_project_created "
        "ON order_log(project_id, created_at DESC)"
    )

    # --- order_approvals -----------------------------------------------
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS order_approvals (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),

            project_id      UUID NOT NULL
                              REFERENCES projects(id) ON DELETE RESTRICT,
            user_id         UUID NOT NULL
                              REFERENCES users(id) ON DELETE RESTRICT,
            agent_id        UUID
                              REFERENCES agents(id) ON DELETE SET NULL,

            -- The full order payload pending decision. JSONB so the UI
            -- can render whatever fields the next iteration adds without
            -- a migration.
            payload         JSONB NOT NULL,

            status          VARCHAR(20) NOT NULL
                              CHECK (status IN (
                                'pending', 'approved', 'rejected', 'expired'
                              )),

            requested_at    TIMESTAMP DEFAULT NOW(),
            decided_at      TIMESTAMP,
            decided_by      UUID REFERENCES users(id),
            expires_at      TIMESTAMP NOT NULL
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_order_approvals_project_created "
        "ON order_approvals(project_id, requested_at DESC)"
    )


# -----------------------------------------------------------------------------
# Downgrade
# -----------------------------------------------------------------------------
def downgrade() -> None:
    # Drop child tables / indexes before the parent (orders).
    op.execute("DROP INDEX IF EXISTS idx_order_approvals_project_created")
    op.execute("DROP TABLE IF EXISTS order_approvals")

    op.execute("DROP INDEX IF EXISTS idx_order_log_project_created")
    op.execute("DROP TABLE IF EXISTS order_log")

    op.execute("DROP INDEX IF EXISTS idx_orders_project_created")
    op.execute("DROP TABLE IF EXISTS orders")
