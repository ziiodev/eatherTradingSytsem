"""``chat_conversations`` data access — tenant-scoped via projects JOIN.

Every read and write filters by ``projects.user_id`` so a caller can
NEVER see (or mutate) a conversation that doesn't belong to their
tenant. ``chat_conversations`` carries a denormalised ``user_id``
column but the repository deliberately uses the projects JOIN as the
authoritative tenant predicate — never trust the denormalised column
for authorization (see ``multi-tenancy-delta`` for the rationale).

Cross-tenant attempts return ``None`` / empty / 0 rows. Existence is
non-disclosing: a caller asking for a conversation they do not own
gets the same shape as a caller asking for a conversation that does
not exist.

The ``increment_tokens`` method is the running-counter primitive used
by the chat service after every assistant turn. It performs an atomic
``UPDATE ... SET tokens_in_total = tokens_in_total + :delta`` so two
concurrent SSE generators on the same conversation never lose a token
write to a read-modify-write race.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import func, select, update

from aether_api.models.chat_conversation import ChatConversation
from aether_api.models.pair import Pair
from aether_api.repositories.base import BaseRepository


class ChatConversationRepository(BaseRepository):
    model = ChatConversation

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    async def _user_owns_project(
        self, user_id: uuid.UUID, project_id: uuid.UUID
    ) -> bool:
        stmt = select(Pair.id).where(
            Pair.id == project_id, Pair.user_id == user_id
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none() is not None

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------
    async def create(
        self,
        *,
        user_id: uuid.UUID,
        project_id: uuid.UUID,
        title: str | None = None,
        model_override: str | None = None,
    ) -> ChatConversation:
        """Create a new conversation under ``project_id``.

        Refuses cross-tenant creation early — never persists a row whose
        owner the read path would refuse to disclose. ``model_override``
        is stored in ``meta_data`` so it can be threaded into the chat
        service without an additional column.
        """
        if not await self._user_owns_project(user_id, project_id):
            raise PermissionError(
                f"user {user_id} does not own project {project_id}"
            )

        meta: dict[str, object] = {}
        if model_override is not None:
            meta["model_override"] = model_override

        row = ChatConversation(
            pair_id=project_id,
            user_id=user_id,
            # When the caller passes ``None`` we let the DB default kick
            # in (server_default '(sin título)') by simply omitting the
            # field; SQLAlchemy will skip it during INSERT.
            **({"title": title} if title is not None else {}),
            meta_data=meta,
        )
        self.session.add(row)
        await self.session.flush()
        await self.session.refresh(row)
        return row

    async def archive(
        self,
        *,
        user_id: uuid.UUID,
        conversation_id: uuid.UUID,
    ) -> ChatConversation | None:
        """Soft-delete (sets ``archived_at = NOW()``).

        Cross-tenant calls return ``None`` (the UPDATE matches no rows).
        Calling on an already-archived conversation is idempotent — we
        do NOT refuse, we re-stamp ``archived_at`` (cheap and
        operationally helpful for the dashboard).
        """
        stmt = (
            update(ChatConversation)
            .where(ChatConversation.id == conversation_id)
            .where(
                ChatConversation.pair_id.in_(
                    select(Pair.id).where(Pair.user_id == user_id)
                )
            )
            .values(archived_at=func.now(), updated_at=func.now())
            .returning(ChatConversation)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def rename(
        self,
        *,
        user_id: uuid.UUID,
        conversation_id: uuid.UUID,
        title: str,
    ) -> ChatConversation | None:
        """Rename a conversation.

        Empty titles are refused by the DB ``length(title) >= 1`` CHECK
        — we let the constraint surface that error rather than
        pre-validating here (the router owns input shape).
        """
        stmt = (
            update(ChatConversation)
            .where(ChatConversation.id == conversation_id)
            .where(
                ChatConversation.pair_id.in_(
                    select(Pair.id).where(Pair.user_id == user_id)
                )
            )
            .values(title=title, updated_at=func.now())
            .returning(ChatConversation)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def increment_tokens(
        self,
        *,
        user_id: uuid.UUID,
        conversation_id: uuid.UUID,
        tokens_in_delta: int,
        usd_delta: Decimal | float,
    ) -> ChatConversation | None:
        """Atomically add ``tokens_in_delta`` / ``usd_delta`` to the rollups.

        Uses ``column = column + :delta`` so concurrent assistant turns
        do not lose updates to a read-modify-write race. The tenant
        predicate is enforced by the same subquery the other writes use:
        a cross-tenant call matches zero rows and returns ``None``.

        Negative deltas are accepted by the SQL but the CHECK
        constraints (``tokens_in_total >= 0``, ``usd_estimated_total >= 0``)
        will reject any UPDATE that would push the rollup negative.
        Callers that need to reset should issue a separate assignment.
        """
        stmt = (
            update(ChatConversation)
            .where(ChatConversation.id == conversation_id)
            .where(
                ChatConversation.pair_id.in_(
                    select(Pair.id).where(Pair.user_id == user_id)
                )
            )
            .values(
                tokens_in_total=ChatConversation.tokens_in_total + tokens_in_delta,
                usd_estimated_total=(
                    ChatConversation.usd_estimated_total
                    + Decimal(str(usd_delta))
                ),
                updated_at=func.now(),
            )
            .returning(ChatConversation)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------
    async def get(
        self,
        *,
        user_id: uuid.UUID,
        conversation_id: uuid.UUID,
    ) -> ChatConversation | None:
        """Return the conversation IFF the caller owns its project."""
        stmt = (
            select(ChatConversation)
            .join(Pair, Pair.id == ChatConversation.pair_id)
            .where(Pair.user_id == user_id)
            .where(ChatConversation.id == conversation_id)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_for_project(
        self,
        *,
        user_id: uuid.UUID,
        project_id: uuid.UUID,
        archived: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[ChatConversation], int]:
        """Return (rows, total_count) for the project's conversations.

        ``archived=False`` (default) filters to ``archived_at IS NULL``,
        matching the partial index. ``archived=True`` returns the
        archived ones (useful for an operator "trash" view).
        Cross-tenant ``project_id`` returns ``([], 0)`` — the projects
        JOIN strips it.
        """
        base = (
            select(ChatConversation)
            .join(Pair, Pair.id == ChatConversation.pair_id)
            .where(Pair.user_id == user_id)
            .where(ChatConversation.pair_id == project_id)
        )
        if archived:
            base = base.where(ChatConversation.archived_at.is_not(None))
        else:
            base = base.where(ChatConversation.archived_at.is_(None))

        rows_stmt = (
            base.order_by(
                ChatConversation.created_at.desc(),
                ChatConversation.id.desc(),
            )
            .limit(limit)
            .offset(offset)
        )
        rows_result = await self.session.execute(rows_stmt)
        rows = list(rows_result.scalars().all())

        count_stmt = (
            select(func.count(ChatConversation.id))
            .join(Pair, Pair.id == ChatConversation.pair_id)
            .where(Pair.user_id == user_id)
            .where(ChatConversation.pair_id == project_id)
        )
        if archived:
            count_stmt = count_stmt.where(ChatConversation.archived_at.is_not(None))
        else:
            count_stmt = count_stmt.where(ChatConversation.archived_at.is_(None))

        total_result = await self.session.execute(count_stmt)
        total = int(total_result.scalar_one() or 0)
        return rows, total
