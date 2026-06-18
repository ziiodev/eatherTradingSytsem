"""``q_tables`` table — per-project versioned Q-value snapshots.

Mirrors :file:`apps/api/alembic/versions/0011_sleep_learning_loop.py`
byte-for-byte semantically. Each Sleep Phase run that produces a
Q-update appends a new row with ``version = max(version) + 1`` for the
project; old versions are kept (append-only) to make Sleep Phase reverts
trivial.

Multi-tenancy is enforced at the repository layer by JOINing through
``projects.user_id``. This table does NOT carry ``user_id`` itself —
that would denormalise and create drift risk against ``projects``.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import CheckConstraint, ForeignKey, Integer, Numeric, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from aether_api.db.base import Base


class QTable(Base):
    """One versioned Q-value snapshot for a project."""

    __tablename__ = "q_tables"

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

    version: Mapped[int] = mapped_column(Integer, nullable=False)

    # JSONB mapping of ``state_key (str) → action (str) → q_value (float)``.
    # The ORM attribute is named ``table_data`` to avoid colliding with
    # SQLAlchemy's ``Table`` reserved name; the DB column stays ``table``.
    table_data: Mapped[dict[str, Any]] = mapped_column("table", JSONB, nullable=False)

    alpha_normal: Mapped[Decimal] = mapped_column(
        Numeric(4, 3), nullable=False, server_default=text("0.150")
    )
    alpha_special: Mapped[Decimal] = mapped_column(
        Numeric(4, 3), nullable=False, server_default=text("0.350")
    )
    gamma: Mapped[Decimal] = mapped_column(
        Numeric(4, 3), nullable=False, server_default=text("0.920")
    )

    episode_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))

    created_by_sleep_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sleep_runs.id", ondelete="SET NULL"),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP, nullable=False, server_default=text("NOW()")
    )

    # --- Relations
    pair = relationship("Pair", lazy="raise")
    sleep_run = relationship("SleepRun", lazy="raise")

    __table_args__ = (
        UniqueConstraint("pair_id", "version", name="uq_q_tables_pair_version"),
        CheckConstraint("version >= 1", name="q_tables_version_positive"),
        CheckConstraint(
            "alpha_normal >= 0 AND alpha_normal <= 1 AND alpha_special >= 0 AND alpha_special <= 1",
            name="q_tables_alpha_range",
        ),
        CheckConstraint(
            "gamma >= 0 AND gamma <= 1",
            name="q_tables_gamma_range",
        ),
        CheckConstraint("episode_count >= 0", name="q_tables_episode_count_nonneg"),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<QTable id={self.id} pair_id={self.pair_id} "
            f"version={self.version} episodes={self.episode_count}>"
        )
