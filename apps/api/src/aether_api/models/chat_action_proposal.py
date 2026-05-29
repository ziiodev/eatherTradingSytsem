"""``chat_action_proposals`` table — SCHEMA-ONLY in v1.

Mirrors :file:`apps/api/alembic/versions/0014_chat.py` byte-for-byte
semantically. The table is created by the migration but NO writes
happen in the v1 ``project-chat`` change — the dispatcher exposes only
read-only tools. The deferred sibling change ``project-chat-actions``
will populate this table when write-tool approvals land.

The model exists in v1 so:

* application imports and ORM autoload behave consistently;
* the `models/__init__.py` re-exports the symbol future repositories
  / services can rely on without a migration boundary;
* the corresponding placeholder repository
  (:mod:`aether_api.repositories.chat_action_proposal_repository`) has
  something to type against.

Tenant isolation is transitive via ``projects.user_id``.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, ForeignKey, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from aether_api.db.base import Base

#: Closed enum. DB CHECK is the source of truth.
CHAT_ACTION_PROPOSAL_STATUSES: tuple[str, ...] = (
    "pending",
    "approved",
    "rejected",
    "expired",
    "executed",
)


class ChatActionProposal(Base):
    """Operator-approval gate for a write-tool the assistant proposed."""

    __tablename__ = "chat_action_proposals"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )

    message_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("chat_messages.id", ondelete="CASCADE"),
        nullable=False,
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("chat_conversations.id", ondelete="CASCADE"),
        nullable=False,
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )

    tool_name: Mapped[str] = mapped_column(String(80), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        server_default=text("'pending'"),
    )

    decided_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    decided_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    decision_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    executed_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    execution_result: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=text("NOW()"),
    )

    # --- Relations (raise on lazy access — explicit eager only)
    message = relationship("ChatMessage", lazy="raise")
    conversation = relationship("ChatConversation", lazy="raise")
    project = relationship("Project", lazy="raise")

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'approved', 'rejected', 'expired', 'executed')",
            name="chat_action_proposals_status_valid",
        ),
        CheckConstraint(
            "length(tool_name) >= 1",
            name="chat_action_proposals_tool_name_nonempty",
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<ChatActionProposal id={self.id} tool_name={self.tool_name} "
            f"status={self.status}>"
        )


__all__ = ["CHAT_ACTION_PROPOSAL_STATUSES", "ChatActionProposal"]
