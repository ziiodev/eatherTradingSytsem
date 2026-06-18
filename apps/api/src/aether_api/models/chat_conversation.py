"""``chat_conversations`` table — operator↔assistant thread per project.

Mirrors :file:`apps/api/alembic/versions/0014_chat.py` byte-for-byte
semantically (column types, nullability, defaults, CHECK constraints).
Any divergence is a bug — fix the model OR the migration, never patch
around it in the service.

Tenant isolation is transitive via ``projects.user_id`` (see
``multi-tenancy-delta`` spec for project-chat). The denormalised
``user_id`` column exists to drive the partial index
``(user_id, created_at DESC) WHERE archived_at IS NULL`` without
forcing every list query to JOIN projects; the repositories STILL
enforce the tenant check via the projects JOIN — never trust the
denormalised column for authorization.

``tokens_in_total`` and ``usd_estimated_total`` are running counters
maintained by the chat service. The repository's ``increment_tokens``
helper performs the atomic ``+ :delta`` UPDATE.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import CheckConstraint, ForeignKey, Integer, Numeric, String, text
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from aether_api.db.base import Base


class ChatConversation(Base):
    """One operator↔assistant conversation scoped to a single project."""

    __tablename__ = "chat_conversations"

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
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )

    title: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
        server_default=text("'(sin título)'"),
    )

    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=text("NOW()"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=text("NOW()"),
    )
    archived_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=True,
    )

    meta_data: Mapped[dict[str, Any]] = mapped_column(
        "meta_data",
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
    )

    tokens_in_total: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("0"),
    )
    usd_estimated_total: Mapped[Decimal] = mapped_column(
        Numeric(12, 6),
        nullable=False,
        server_default=text("0"),
    )

    # --- Relations
    pair = relationship("Pair", lazy="raise")
    user = relationship("User", lazy="raise")
    messages = relationship(
        "ChatMessage",
        back_populates="conversation",
        lazy="raise",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        CheckConstraint("length(title) >= 1", name="chat_conversations_title_nonempty"),
        CheckConstraint(
            "tokens_in_total >= 0", name="chat_conversations_tokens_nonneg"
        ),
        CheckConstraint(
            "usd_estimated_total >= 0", name="chat_conversations_usd_nonneg"
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<ChatConversation id={self.id} pair_id={self.pair_id} "
            f"title={self.title!r}>"
        )
