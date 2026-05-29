"""Per-turn concurrency + budget guards for the chat router.

Two responsibilities, kept narrow on purpose:

* :func:`chat_turn_lock` — acquire a Postgres transaction-scoped advisory
  lock keyed off ``hashtext(conversation_id::text)``. Non-blocking
  (``pg_try_advisory_xact_lock``); if already held, raise a structured
  409 with ``code = CHAT_TURN_IN_PROGRESS``. The lock is released
  automatically when the transaction ends — callers therefore MUST own
  a session whose transaction outlives the dispatch (we use
  ``session.begin()`` in the router to keep the lock alive for the
  whole SSE generator).
* :func:`chat_budget_check` — fast SELECT against
  ``chat_conversations.tokens_in_total`` for the active tenant. If the
  running counter is at-or-above :data:`CHAT_TOKEN_BUDGET_HARD_CAP`,
  raise 409 with ``code = CHAT_BUDGET_EXCEEDED``. Cross-tenant probes
  return zero rows from the JOIN and are treated as "no budget pressure"
  — the 404 surface on the conversation itself stops them earlier.

Both helpers raise :class:`fastapi.HTTPException` directly so the router
can call them inline without a try/except dance.
"""

from __future__ import annotations

import uuid
from typing import Final

from fastapi import HTTPException, status
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from aether_api.models.chat_conversation import ChatConversation
from aether_api.models.project import Project

#: Hard cap on cumulative tokens per conversation. Once
#: ``chat_conversations.tokens_in_total`` reaches this value the next
#: turn is refused with ``CHAT_BUDGET_EXCEEDED``. The user must archive
#: the conversation and start a new one — that is the v1 escape hatch.
CHAT_TOKEN_BUDGET_HARD_CAP: Final[int] = 500_000


class ChatTurnInProgressError(HTTPException):
    """409 — another assistant turn is already running for this conversation."""

    def __init__(self) -> None:
        super().__init__(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "CHAT_TURN_IN_PROGRESS",
                "message": (
                    "Otra interacción está en curso para esta conversación."
                ),
            },
        )


class ChatBudgetExceededError(HTTPException):
    """409 — the conversation's token budget is exhausted."""

    def __init__(self) -> None:
        super().__init__(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "CHAT_BUDGET_EXCEEDED",
                "message": (
                    "El presupuesto de tokens de esta conversación se ha agotado."
                ),
            },
        )


class ChatNotConfiguredError(HTTPException):
    """500 — the surface is enabled but the upstream API key is missing."""

    def __init__(self) -> None:
        super().__init__(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "code": "CHAT_NOT_CONFIGURED",
                "message": "Falta ANTHROPIC_API_KEY.",
            },
        )


async def chat_turn_lock(
    session: AsyncSession,
    conversation_id: uuid.UUID,
) -> None:
    """Try to acquire the conversation's advisory transaction lock.

    Uses ``pg_try_advisory_xact_lock(hashtext(:conv::text))`` so the
    call returns immediately when the lock is held by another
    transaction (no blocking on the connection pool). The lock is
    released automatically when the surrounding transaction commits or
    rolls back — the caller MUST wrap the dispatch in a single
    transaction or the next turn will see the lock as free even while
    the previous turn is still streaming.

    On failure raises :class:`ChatTurnInProgressError` (409).
    """
    # hashtext returns an int4 — Postgres' advisory lock takes a bigint
    # so the implicit widening is fine. We pass the conversation_id
    # straight as ``text`` so the same call works across UUID
    # serializations.
    stmt = text(
        "SELECT pg_try_advisory_xact_lock(hashtext(:conv_id_text)) AS acquired"
    ).bindparams(conv_id_text=str(conversation_id))
    result = await session.execute(stmt)
    acquired = bool(result.scalar_one())
    if not acquired:
        raise ChatTurnInProgressError()


async def chat_budget_check(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    conversation_id: uuid.UUID,
) -> int:
    """Refuse the turn if the conversation's running token total is at the cap.

    Returns the current ``tokens_in_total`` so the caller can echo it
    into structured logs / the SSE turn_done payload. Cross-tenant or
    missing rows return 0 — the budget gate only fires on conversations
    the caller owns and that have crossed the cap. The router's earlier
    GET-by-id check already 404s the cross-tenant case.

    On at-or-above the cap raises :class:`ChatBudgetExceededError` (409).
    """
    stmt = (
        select(ChatConversation.tokens_in_total)
        .join(Project, Project.id == ChatConversation.project_id)
        .where(Project.user_id == user_id)
        .where(ChatConversation.id == conversation_id)
    )
    result = await session.execute(stmt)
    current = result.scalar_one_or_none()
    if current is None:
        return 0
    if int(current) >= CHAT_TOKEN_BUDGET_HARD_CAP:
        raise ChatBudgetExceededError()
    return int(current)


__all__ = [
    "CHAT_TOKEN_BUDGET_HARD_CAP",
    "ChatBudgetExceededError",
    "ChatNotConfiguredError",
    "ChatTurnInProgressError",
    "chat_budget_check",
    "chat_turn_lock",
]
