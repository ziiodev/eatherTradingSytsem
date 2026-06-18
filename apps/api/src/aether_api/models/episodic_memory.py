"""``episodic_memory`` table — append-only (s, a, r, s') tuples per project.

Mirrors :file:`apps/api/alembic/versions/0011_sleep_learning_loop.py`
byte-for-byte semantically. Episodes are produced by the Worker (one per
closed trade) and consumed by the Orquestador during the Sleep Phase
synthesis step (5a). ``consumed_by_sleep_run_id`` is set when an
episode has been folded into a Q-Table version — repository code MUST
filter on ``consumed_by_sleep_run_id IS NULL`` to find pending episodes.

Multi-tenancy enforced transitively via ``projects.user_id``.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import ForeignKey, Numeric, String, text
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from aether_api.db.base import Base


class EpisodicMemory(Base):
    """One (s, a, r, s') tuple — the atom of Q-Learning history."""

    __tablename__ = "episodic_memory"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )

    pair_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("pairs.id", ondelete="CASCADE"),
        nullable=False,
    )

    state_key: Mapped[str] = mapped_column(String(120), nullable=False)
    action: Mapped[str] = mapped_column(String(60), nullable=False)
    reward: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False)
    next_state_key: Mapped[str | None] = mapped_column(String(120), nullable=True)

    order_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("orders.id", ondelete="SET NULL"),
        nullable=True,
    )

    consumed_by_sleep_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sleep_runs.id", ondelete="SET NULL"),
        nullable=True,
    )

    # The ORM attribute is ``meta_data`` because SQLAlchemy's ``Base``
    # reserves ``metadata``. The DB column stays ``metadata``.
    meta_data: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP, nullable=False, server_default=text("NOW()")
    )

    # --- Relations
    pair = relationship("Pair", lazy="raise")
    order = relationship("Order", lazy="raise")
    sleep_run = relationship("SleepRun", lazy="raise", foreign_keys=[consumed_by_sleep_run_id])

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<EpisodicMemory id={self.id} pair_id={self.pair_id} "
            f"state={self.state_key} action={self.action} reward={self.reward}>"
        )
