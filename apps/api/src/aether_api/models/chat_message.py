"""``chat_messages`` table — append-only conversation turn history.

Mirrors :file:`apps/api/alembic/versions/0014_chat.py` byte-for-byte
semantically. Tenant isolation is transitive via the parent
``chat_conversations`` row's ``project_id`` → ``projects.user_id`` chain
(see ``multi-tenancy-delta`` for the JOIN shape every repository MUST
use).

``action_proposal`` is ALWAYS NULL in v1 — the dispatcher is read-only.
The deferred ``project-chat-actions`` sibling change writes to it.
``meta_data`` carries Anthropic extended-thinking blocks and any other
per-turn structured extras.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, ForeignKey, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from aether_api.db.base import Base

#: Closed enum of message roles. DB CHECK is the source of truth.
CHAT_MESSAGE_ROLES: tuple[str, ...] = ("user", "assistant", "system", "tool")


class ChatMessage(Base):
    """One turn in a chat conversation."""

    __tablename__ = "chat_messages"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("chat_conversations.id", ondelete="CASCADE"),
        nullable=False,
    )

    role: Mapped[str] = mapped_column(String(20), nullable=False)

    content: Mapped[str] = mapped_column(Text, nullable=False)

    tool_calls: Mapped[list[dict[str, Any]] | None] = mapped_column(
        JSONB, nullable=True
    )
    tool_results: Mapped[list[dict[str, Any]] | None] = mapped_column(
        JSONB, nullable=True
    )

    tokens_in: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tokens_out: Mapped[int | None] = mapped_column(Integer, nullable=True)

    model: Mapped[str | None] = mapped_column(String(50), nullable=True)
    stop_reason: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # Forward-compat slot. ALWAYS NULL in v1.
    action_proposal: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True
    )

    meta_data: Mapped[dict[str, Any]] = mapped_column(
        "meta_data",
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
    )

    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=text("NOW()"),
    )

    # --- Relations
    conversation = relationship(
        "ChatConversation", back_populates="messages", lazy="raise"
    )

    __table_args__ = (
        CheckConstraint(
            "role IN ('user', 'assistant', 'system', 'tool')",
            name="chat_messages_role_valid",
        ),
        CheckConstraint(
            "tokens_in IS NULL OR tokens_in >= 0",
            name="chat_messages_tokens_in_nonneg",
        ),
        CheckConstraint(
            "tokens_out IS NULL OR tokens_out >= 0",
            name="chat_messages_tokens_out_nonneg",
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<ChatMessage id={self.id} conversation_id={self.conversation_id} "
            f"role={self.role}>"
        )


__all__ = ["CHAT_MESSAGE_ROLES", "ChatMessage"]
