"""``chat_messages`` data access — tenant-scoped transitively.

Every read and write filters through ``chat_conversations →
projects.user_id`` so a caller cannot see or append to a conversation
they do not own. Cross-tenant attempts return ``None`` / empty / 0
rows (or raise ``PermissionError`` on inserts — refusing to persist a
row the caller could never read back is the correct shape).

``mark_aborted_stale`` is the sweeper primitive — used by the
chat-service background task to mark orphaned assistant rows (writer
disconnected mid-stream) as ``stop_reason='aborted'`` so the UI does
not show them as in-flight forever.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import and_, func, select, update

from aether_api.models.chat_conversation import ChatConversation
from aether_api.models.chat_message import ChatMessage
from aether_api.models.project import Project
from aether_api.repositories.base import BaseRepository


class ChatMessageRepository(BaseRepository):
    model = ChatMessage

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    async def _user_owns_conversation(
        self, user_id: uuid.UUID, conversation_id: uuid.UUID
    ) -> bool:
        stmt = (
            select(ChatConversation.id)
            .join(Project, Project.id == ChatConversation.project_id)
            .where(Project.user_id == user_id)
            .where(ChatConversation.id == conversation_id)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none() is not None

    # ------------------------------------------------------------------
    # Writes — three roles get a dedicated insert so the call site reads
    # like prose and so the repository can reject obviously-malformed
    # payloads (e.g. tool_results on a user turn).
    # ------------------------------------------------------------------
    async def insert_user(
        self,
        *,
        user_id: uuid.UUID,
        conversation_id: uuid.UUID,
        content: str,
    ) -> ChatMessage:
        """Append a ``role='user'`` turn."""
        if not await self._user_owns_conversation(user_id, conversation_id):
            raise PermissionError(
                f"user {user_id} does not own conversation {conversation_id}"
            )
        row = ChatMessage(
            conversation_id=conversation_id,
            role="user",
            content=content,
        )
        self.session.add(row)
        await self.session.flush()
        await self.session.refresh(row)
        return row

    async def insert_assistant(
        self,
        *,
        user_id: uuid.UUID,
        conversation_id: uuid.UUID,
        content: str,
        tool_calls: list[dict[str, Any]] | None = None,
        tokens_in: int | None = None,
        tokens_out: int | None = None,
        model: str | None = None,
        stop_reason: str | None = None,
        meta_data: dict[str, Any] | None = None,
    ) -> ChatMessage:
        """Append a ``role='assistant'`` turn.

        ``tool_calls`` carries the Anthropic-format tool_use blocks the
        assistant emitted. ``meta_data`` carries extended-thinking
        blocks and any other per-turn structured extras.
        """
        if not await self._user_owns_conversation(user_id, conversation_id):
            raise PermissionError(
                f"user {user_id} does not own conversation {conversation_id}"
            )
        row = ChatMessage(
            conversation_id=conversation_id,
            role="assistant",
            content=content,
            tool_calls=tool_calls,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            model=model,
            stop_reason=stop_reason,
            meta_data=meta_data if meta_data is not None else {},
        )
        self.session.add(row)
        await self.session.flush()
        await self.session.refresh(row)
        return row

    async def insert_tool(
        self,
        *,
        user_id: uuid.UUID,
        conversation_id: uuid.UUID,
        tool_results: list[dict[str, Any]],
    ) -> ChatMessage:
        """Append a ``role='tool'`` turn carrying tool_result blocks.

        ``content`` is intentionally empty for tool turns — the
        structured payload lives in ``tool_results``. We store an empty
        string (not NULL) so the NOT NULL constraint passes.
        """
        if not await self._user_owns_conversation(user_id, conversation_id):
            raise PermissionError(
                f"user {user_id} does not own conversation {conversation_id}"
            )
        row = ChatMessage(
            conversation_id=conversation_id,
            role="tool",
            content="",
            tool_results=tool_results,
        )
        self.session.add(row)
        await self.session.flush()
        await self.session.refresh(row)
        return row

    async def mark_aborted_stale(
        self,
        *,
        older_than: datetime,
    ) -> int:
        """Stamp every stale in-flight assistant row as ``stop_reason='aborted'``.

        Sweeper-only — no tenant predicate. The sweeper runs as a
        backend background task; it MUST NOT take user_id input. The
        predicate is precise: assistant rows whose ``stop_reason`` is
        still NULL AND whose ``created_at`` is older than ``older_than``
        (a wall-clock cutoff). Returns the number of rows updated.

        Idempotent: a second call with the same cutoff finds nothing to
        rewrite (the first one stamped them all).
        """
        stmt = (
            update(ChatMessage)
            .where(
                and_(
                    ChatMessage.role == "assistant",
                    ChatMessage.stop_reason.is_(None),
                    ChatMessage.created_at < older_than,
                )
            )
            .values(stop_reason="aborted")
            .returning(ChatMessage.id)
        )
        result = await self.session.execute(stmt)
        return len(result.all())

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------
    async def list_for_conversation(
        self,
        *,
        user_id: uuid.UUID,
        conversation_id: uuid.UUID,
        limit: int = 200,
        offset: int = 0,
    ) -> tuple[list[ChatMessage], int]:
        """Return (rows, total_count) for the conversation, chronologically.

        Order is ``created_at ASC`` so the UI can render the transcript
        top-down without re-sorting. Cross-tenant ``conversation_id``
        returns ``([], 0)`` — the projects JOIN strips it.
        """
        rows_stmt = (
            select(ChatMessage)
            .join(
                ChatConversation, ChatConversation.id == ChatMessage.conversation_id
            )
            .join(Project, Project.id == ChatConversation.project_id)
            .where(Project.user_id == user_id)
            .where(ChatMessage.conversation_id == conversation_id)
            .order_by(ChatMessage.created_at.asc(), ChatMessage.id.asc())
            .limit(limit)
            .offset(offset)
        )
        rows_result = await self.session.execute(rows_stmt)
        rows = list(rows_result.scalars().all())

        count_stmt = (
            select(func.count(ChatMessage.id))
            .join(
                ChatConversation, ChatConversation.id == ChatMessage.conversation_id
            )
            .join(Project, Project.id == ChatConversation.project_id)
            .where(Project.user_id == user_id)
            .where(ChatMessage.conversation_id == conversation_id)
        )
        total_result = await self.session.execute(count_stmt)
        total = int(total_result.scalar_one() or 0)
        return rows, total
