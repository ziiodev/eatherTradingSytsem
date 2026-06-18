"""``semantic_memory`` table — long-term "lessons learned" rules.

Mirrors :file:`apps/api/alembic/versions/0011_sleep_learning_loop.py`
byte-for-byte semantically. A rule is NEVER hard-deleted; the
Orquestador either flips ``active = false`` or appends a new rule and
points ``superseded_by`` at the previous one. The repository layer
treats ``active = true`` as the working set.

Multi-tenancy enforced transitively via ``projects.user_id``.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, ForeignKey, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from aether_api.db.base import Base


class SemanticMemory(Base):
    """One semantic rule for a project (markdown body + structured payload)."""

    __tablename__ = "semantic_memory"

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

    rule_type: Mapped[str] = mapped_column(String(40), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    # Self-FK lineage: the new rule points at the old one it replaces.
    # SET NULL on hard-delete so the supersession chain doesn't cascade.
    superseded_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("semantic_memory.id", ondelete="SET NULL"),
        nullable=True,
    )

    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("TRUE"))

    created_by_sleep_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sleep_runs.id", ondelete="SET NULL"),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP, nullable=False, server_default=text("NOW()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP, nullable=False, server_default=text("NOW()")
    )

    # --- Relations
    pair = relationship("Pair", lazy="raise")
    sleep_run = relationship("SleepRun", lazy="raise")
    parent_rule = relationship(
        "SemanticMemory",
        remote_side="SemanticMemory.id",
        lazy="raise",
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<SemanticMemory id={self.id} pair_id={self.pair_id} "
            f"rule_type={self.rule_type} active={self.active}>"
        )
