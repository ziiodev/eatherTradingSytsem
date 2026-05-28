"""``orders``, ``order_log``, ``order_approvals`` — live trading data model.

Mirrors the ``0007_orders_and_approvals`` migration byte-for-byte
semantically. See the migration docstring for the rationale around the
RESTRICT FKs, the mandatory ``sl`` column, and the two-phase
``order_log`` writes.

CHARTER invariants reinforced here:

* ``sl`` is ``NOT NULL`` on :class:`Order` (DB level) AND a positive
  Decimal in the API request DTOs AND a positive Decimal at the
  RiskEnforcer / wrapper boundary. Three layers.
* Every column required by the wire contract is present; nothing here
  is "virtual" — what the model shows is exactly what the DB stores.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from aether_api.db.base import Base

#: Allowed values for ``orders.status``. App-level membership check; the
#: DB CHECK constraint named ``orders_status_valid`` is the source of
#: truth (see migration).
ORDER_STATUSES: tuple[str, ...] = (
    "pending",
    "approved_pending_send",
    "filled",
    "failed",
    "rejected",
    "expired",
)

ORDER_SIDES: tuple[str, ...] = ("buy", "sell")

#: Allowed values for ``order_log.status``. ``filled`` and ``failed``
#: line up with their ``orders.status`` siblings; ``blocked`` is the
#: terminal state of an order the RiskEnforcer / ApprovalGate refused
#: (no ``orders`` row gets created for those).
ORDER_LOG_STATUSES: tuple[str, ...] = ("pending", "filled", "failed", "blocked")

ORDER_APPROVAL_STATUSES: tuple[str, ...] = (
    "pending",
    "approved",
    "rejected",
    "expired",
)


class Order(Base):
    """One order the system has placed (or attempted to place)."""

    __tablename__ = "orders"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="RESTRICT"),
        nullable=False,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    agent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agents.id", ondelete="SET NULL"),
        nullable=True,
    )

    symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    side: Mapped[str] = mapped_column(String(10), nullable=False)
    volume: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)

    sl: Mapped[Decimal] = mapped_column(Numeric(15, 5), nullable=False)
    tp: Mapped[Decimal | None] = mapped_column(Numeric(15, 5), nullable=True)

    mt5_ticket: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    status: Mapped[str] = mapped_column(String(20), nullable=False)
    comment: Mapped[str | None] = mapped_column(String(255), nullable=True)
    magic: Mapped[int | None] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime | None] = mapped_column(TIMESTAMP, server_default=text("NOW()"))
    filled_at: Mapped[datetime | None] = mapped_column(TIMESTAMP, nullable=True)

    # --- Relations
    project = relationship("Project", lazy="raise")
    user = relationship("User", lazy="raise")
    agent = relationship("Agent", lazy="raise")

    __table_args__ = (
        CheckConstraint("side IN ('buy','sell')", name="orders_side_valid"),
        CheckConstraint("volume > 0", name="orders_volume_positive"),
        CheckConstraint(
            "status IN ('pending','approved_pending_send','filled','failed',"
            "'rejected','expired')",
            name="orders_status_valid",
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Order id={self.id} symbol={self.symbol} side={self.side} status={self.status}>"


class OrderLog(Base):
    """Append-only audit row for every order action (two-phase write).

    Phase 1 (BEFORE the MCP call): insert with ``status='pending'`` and
    the inputs (payload_in, risk_check). This means a process crash
    between phase 1 and phase 2 leaves an unambiguous forensic record.

    Phase 2 (AFTER the MCP call): update to ``filled|failed`` with the
    MT5 response in ``payload_out`` and the ticket on the linked ``orders``
    row.

    Approval-gate / risk-blocked path: phase 1 is the only write — the
    row stays at ``status='blocked'``.
    """

    __tablename__ = "order_log"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    order_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("orders.id", ondelete="CASCADE"),
        nullable=True,
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="RESTRICT"),
        nullable=False,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )

    action: Mapped[str] = mapped_column(String(50), nullable=False)
    payload_in: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    payload_out: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    risk_check: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    status: Mapped[str] = mapped_column(String(20), nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime | None] = mapped_column(TIMESTAMP, server_default=text("NOW()"))

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','filled','failed','blocked')",
            name="order_log_status_valid",
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<OrderLog id={self.id} action={self.action} status={self.status}>"


class OrderApproval(Base):
    """A pending / decided large-order approval request."""

    __tablename__ = "order_approvals"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="RESTRICT"),
        nullable=False,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    agent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agents.id", ondelete="SET NULL"),
        nullable=True,
    )

    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)

    requested_at: Mapped[datetime | None] = mapped_column(TIMESTAMP, server_default=text("NOW()"))
    decided_at: Mapped[datetime | None] = mapped_column(TIMESTAMP, nullable=True)
    decided_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=True,
    )
    expires_at: Mapped[datetime] = mapped_column(TIMESTAMP, nullable=False)

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','approved','rejected','expired')",
            name="order_approvals_status_valid",
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<OrderApproval id={self.id} status={self.status}>"


__all__ = [
    "ORDER_APPROVAL_STATUSES",
    "ORDER_LOG_STATUSES",
    "ORDER_SIDES",
    "ORDER_STATUSES",
    "Order",
    "OrderApproval",
    "OrderLog",
]
