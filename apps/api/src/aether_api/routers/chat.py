"""``/api/projects/{project_id}/chat/*`` — operator↔Claude SSE chat surface.

Six endpoints make up the v1 (read-only-dispatch) surface:

* ``GET    /conversations``                  — paginated list (archived
  filter, default unarchived). Returns ``{items, total}``.
* ``POST   /conversations``                  — create a new conversation.
* ``GET    /conversations/{conv_id}``        — single conversation +
  optional last N messages.
* ``PATCH  /conversations/{conv_id}``        — rename + soft-archive.
* ``GET    /conversations/{conv_id}/messages`` — paginated message history.
* ``POST   /conversations/{conv_id}/messages`` — STREAM new turn (SSE).

Multi-tenancy gates (see ``sdd/project-chat/spec/multi-tenancy-delta``):

* Every endpoint authenticates via :func:`current_user` and 401s on no
  session.
* Every endpoint resolves the project through
  :class:`ProjectRepository.get_for_user`; cross-tenant → 404 (never
  403).
* Every conversation-scoped endpoint resolves the conversation through
  :class:`ChatConversationRepository.get`; cross-tenant or
  archived-but-not-owned → 404.
* Cross-tenant write attempts emit an audit row
  (``action='chat.cross_tenant_write_denied'``) so a hostile crawler is
  visible to ops even though the response body says 404.

Streaming endpoint concurrency model:

* :func:`chat_turn_lock` calls ``pg_try_advisory_xact_lock`` keyed off
  ``hashtext(conv_id::text)``. The lock is held until the transaction
  ends; we keep the transaction open across the SSE generator so a
  concurrent POST on the same conversation gets 409
  ``CHAT_TURN_IN_PROGRESS`` immediately.
* :func:`chat_budget_check` reads ``tokens_in_total`` once before
  dispatch; ≥ 500_000 → 409 ``CHAT_BUDGET_EXCEEDED``.
* If ``settings.anthropic_api_key`` is unset → 500
  ``CHAT_NOT_CONFIGURED``.

Headers set on the SSE response:

* ``Content-Type: text/event-stream; charset=utf-8``
* ``Cache-Control: no-cache, no-transform``
* ``X-Accel-Buffering: no`` — disables nginx response buffering so SSE
  frames flush to the client.
* ``Connection: keep-alive``
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from aether_api.core.settings import get_settings
from aether_api.db.session import get_session, get_session_maker
from aether_api.models.user import User
from aether_api.repositories.audit_repository import AuditRepository
from aether_api.repositories.chat_conversation_repository import (
    ChatConversationRepository,
)
from aether_api.repositories.chat_message_repository import ChatMessageRepository
from aether_api.repositories.project_repository import ProjectRepository
from aether_api.routers.chat_dependencies import (
    ChatNotConfiguredError,
    chat_budget_check,
    chat_turn_lock,
)
from aether_api.services.chat.anthropic_client import MODEL_WHITELIST
from aether_api.services.chat.context import ChatDispatchContext
from aether_api.services.chat.stream import generate_sse_events
from aether_api.tenancy.middleware import csrf_dependency, current_user

router = APIRouter(prefix="/api/projects", tags=["chat"])


# ---------------------------------------------------------------------------
# DTOs
# ---------------------------------------------------------------------------
class ChatConversationOut(BaseModel):
    """Slim conversation envelope returned by all CRUD endpoints."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    title: str
    created_at: datetime | None = None
    updated_at: datetime | None = None
    archived_at: datetime | None = None
    tokens_in_total: int = 0
    usd_estimated_total: Decimal = Decimal("0")
    meta_data: dict[str, Any] = Field(default_factory=dict)


class ChatConversationList(BaseModel):
    items: list[ChatConversationOut]
    total: int


class ChatMessageOut(BaseModel):
    """One conversation turn in the wire format."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    conversation_id: uuid.UUID
    role: str
    content: str
    tool_calls: list[dict[str, Any]] | None = None
    tool_results: list[dict[str, Any]] | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    model: str | None = None
    stop_reason: str | None = None
    created_at: datetime | None = None


class ChatMessageList(BaseModel):
    items: list[ChatMessageOut]
    total: int


class ConversationDetail(BaseModel):
    """GET-by-id payload: the conversation plus the tail of its messages."""

    conversation: ChatConversationOut
    messages: list[ChatMessageOut]


class ChatConversationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str | None = Field(default=None, min_length=1, max_length=200)
    model_override: str | None = Field(default=None, max_length=80)

    @field_validator("model_override")
    @classmethod
    def _model_in_whitelist(cls, v: str | None) -> str | None:
        if v is None:
            return None
        if v not in MODEL_WHITELIST:
            raise ValueError(
                f"model_override {v!r} is not in the chat whitelist "
                f"({sorted(MODEL_WHITELIST)!r})"
            )
        return v


class ChatConversationPatch(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str | None = Field(default=None, min_length=1, max_length=200)
    archived: bool | None = None


class ChatMessagePost(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Long bound — the model API itself will reject genuinely abusive
    # payloads; the per-turn budget check is the load-bearing guard.
    content: str = Field(min_length=1, max_length=64_000)


# ---------------------------------------------------------------------------
# Tenant gate helpers
# ---------------------------------------------------------------------------
async def _ensure_project_owned(
    session: AsyncSession, user: User, project_id: uuid.UUID
) -> None:
    """404 if ``project_id`` does not belong to ``user``. No existence leak."""
    repo = ProjectRepository(session)
    project = await repo.get_for_user(user.id, project_id)
    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="project not found"
        )


async def _ensure_conversation_owned(
    session: AsyncSession,
    user: User,
    project_id: uuid.UUID,
    conversation_id: uuid.UUID,
) -> Any:
    """Resolve a conversation owned by ``user`` AND attached to ``project_id``.

    Returns the row; raises 404 on any miss. The pair lookup defends
    against an attacker who guesses a conversation_id from one tenant
    and POSTs it under a different project they own (the conversation
    might still be theirs, but it would not belong to the project they
    addressed).
    """
    repo = ChatConversationRepository(session)
    conv = await repo.get(user_id=user.id, conversation_id=conversation_id)
    if conv is None or conv.project_id != project_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="conversation not found"
        )
    return conv


async def _audit_cross_tenant(
    session: AsyncSession,
    *,
    actor_user_id: uuid.UUID,
    action: str,
    target_id: uuid.UUID,
) -> None:
    """Record a structured audit row for a denied cross-tenant write attempt.

    Best-effort: a failure here MUST NOT escalate to the caller — the
    primary 404 response is already in flight by the time the audit
    write is attempted.
    """
    try:
        audit = AuditRepository(session)
        await audit.record(
            user_id=actor_user_id,
            action=action,
            target_type="chat_conversation",
            target_id=target_id,
            before={"reason": "cross_tenant_write_denied"},
        )
        await session.commit()
    except Exception:  # noqa: BLE001 — never break the 404 path
        await session.rollback()


# ---------------------------------------------------------------------------
# Read endpoints
# ---------------------------------------------------------------------------
@router.get(
    "/{project_id}/chat/conversations",
    response_model=ChatConversationList,
)
async def list_conversations(
    project_id: uuid.UUID,
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    archived: Annotated[bool, Query()] = False,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=10_000)] = 0,
) -> ChatConversationList:
    """List the project's conversations. ``archived=False`` is the default."""
    await _ensure_project_owned(session, user, project_id)
    repo = ChatConversationRepository(session)
    rows, total = await repo.list_for_project(
        user_id=user.id,
        project_id=project_id,
        archived=archived,
        limit=limit,
        offset=offset,
    )
    return ChatConversationList(
        items=[ChatConversationOut.model_validate(r) for r in rows],
        total=total,
    )


@router.post(
    "/{project_id}/chat/conversations",
    response_model=ChatConversationOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(csrf_dependency)],
)
async def create_conversation(
    project_id: uuid.UUID,
    body: ChatConversationCreate,
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ChatConversationOut:
    """Create a new conversation under ``project_id``.

    ``title`` defaults to ``"(sin título)"`` via the DB server_default
    when the caller omits it. ``model_override`` is whitelisted at the
    DTO layer and stored in ``meta_data`` for later turns to read.
    """
    await _ensure_project_owned(session, user, project_id)
    repo = ChatConversationRepository(session)
    conv = await repo.create(
        user_id=user.id,
        project_id=project_id,
        title=body.title,
        model_override=body.model_override,
    )
    await session.commit()
    await session.refresh(conv)
    return ChatConversationOut.model_validate(conv)


@router.get(
    "/{project_id}/chat/conversations/{conversation_id}",
    response_model=ConversationDetail,
)
async def get_conversation(
    project_id: uuid.UUID,
    conversation_id: uuid.UUID,
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    last: Annotated[int, Query(ge=0, le=200)] = 50,
) -> ConversationDetail:
    """Return the conversation header plus the last ``last`` messages."""
    await _ensure_project_owned(session, user, project_id)
    conv = await _ensure_conversation_owned(session, user, project_id, conversation_id)

    msg_repo = ChatMessageRepository(session)
    rows, _total = await msg_repo.list_for_conversation(
        user_id=user.id,
        conversation_id=conversation_id,
        limit=last,
    )
    return ConversationDetail(
        conversation=ChatConversationOut.model_validate(conv),
        messages=[ChatMessageOut.model_validate(r) for r in rows],
    )


@router.patch(
    "/{project_id}/chat/conversations/{conversation_id}",
    response_model=ChatConversationOut,
    dependencies=[Depends(csrf_dependency)],
)
async def patch_conversation(
    project_id: uuid.UUID,
    conversation_id: uuid.UUID,
    body: ChatConversationPatch,
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ChatConversationOut:
    """Rename and/or soft-archive a conversation.

    Body keys are independent:

    * ``title`` (non-empty string) — renames the conversation.
    * ``archived=true`` — stamps ``archived_at = NOW()``. ``archived=false``
      is currently a no-op (the v1 spec does not include unarchive).

    Sending an empty body raises 400.
    """
    if body.title is None and body.archived is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="patch body must include at least one of title|archived",
        )

    # Resolve first to drive 404-on-cross-tenant and to give us a row
    # for the cross-tenant audit emission if needed.
    project_repo = ProjectRepository(session)
    project = await project_repo.get_for_user(user.id, project_id)
    if project is None:
        # Cross-tenant project — audit the write attempt.
        await _audit_cross_tenant(
            session,
            actor_user_id=user.id,
            action="chat.cross_tenant_write_denied",
            target_id=conversation_id,
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="project not found"
        )

    repo = ChatConversationRepository(session)
    existing = await repo.get(user_id=user.id, conversation_id=conversation_id)
    if existing is None or existing.project_id != project_id:
        await _audit_cross_tenant(
            session,
            actor_user_id=user.id,
            action="chat.cross_tenant_write_denied",
            target_id=conversation_id,
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="conversation not found"
        )

    updated: Any = existing
    if body.title is not None:
        updated = await repo.rename(
            user_id=user.id,
            conversation_id=conversation_id,
            title=body.title,
        )
        if updated is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="conversation not found",
            )
    if body.archived is True:
        updated = await repo.archive(
            user_id=user.id, conversation_id=conversation_id
        )
        if updated is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="conversation not found",
            )

    await session.commit()
    await session.refresh(updated)
    return ChatConversationOut.model_validate(updated)


@router.get(
    "/{project_id}/chat/conversations/{conversation_id}/messages",
    response_model=ChatMessageList,
)
async def list_messages(
    project_id: uuid.UUID,
    conversation_id: uuid.UUID,
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    offset: Annotated[int, Query(ge=0, le=10_000)] = 0,
) -> ChatMessageList:
    """Return the conversation's messages, chronologically ascending."""
    await _ensure_project_owned(session, user, project_id)
    await _ensure_conversation_owned(session, user, project_id, conversation_id)

    repo = ChatMessageRepository(session)
    rows, total = await repo.list_for_conversation(
        user_id=user.id,
        conversation_id=conversation_id,
        limit=limit,
        offset=offset,
    )
    return ChatMessageList(
        items=[ChatMessageOut.model_validate(r) for r in rows],
        total=total,
    )


# ---------------------------------------------------------------------------
# Streaming endpoint
# ---------------------------------------------------------------------------
def _build_anthropic_client() -> Any:
    """Construct the upstream Anthropic client from settings.

    Pulled into a module-level helper so tests can monkeypatch this
    symbol with a fake without going through the SDK. Returns ``None``
    when the API key is unset — the caller raises
    :class:`ChatNotConfiguredError` in that branch.
    """
    settings = get_settings()
    if settings.anthropic_api_key is None:
        return None
    try:
        import anthropic
    except ImportError:  # pragma: no cover — anthropic is a hard dep
        return None
    return anthropic.Anthropic(
        api_key=settings.anthropic_api_key.get_secret_value()
    )


_SSE_HEADERS: dict[str, str] = {
    # X-Accel-Buffering disables nginx response buffering — without it
    # SSE frames would queue until the proxy's flush threshold and the
    # UI would see token bursts rather than a typewriter stream.
    "X-Accel-Buffering": "no",
    "Cache-Control": "no-cache, no-transform",
    "Connection": "keep-alive",
}


@router.post(
    "/{project_id}/chat/conversations/{conversation_id}/messages",
    dependencies=[Depends(csrf_dependency)],
)
async def post_message(
    project_id: uuid.UUID,
    conversation_id: uuid.UUID,
    body: ChatMessagePost,
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> StreamingResponse:
    """Stream one assistant turn as Server-Sent Events.

    The pipeline:

    1. 401 if no session (handled by :func:`current_user`).
    2. 404 if the project or conversation does not belong to the caller.
       Cross-tenant write attempts emit a ``chat.cross_tenant_write_denied``
       audit row.
    3. 500 ``CHAT_NOT_CONFIGURED`` if the Anthropic API key is missing.
    4. 409 ``CHAT_TURN_IN_PROGRESS`` if another turn already holds the
       conversation's advisory lock.
    5. 409 ``CHAT_BUDGET_EXCEEDED`` if cumulative tokens ≥ 500k.
    6. Stream ``token`` / ``tool_use`` / ``tool_result`` / ``turn_done`` /
       ``error`` SSE frames from :func:`generate_sse_events`.
    """
    # Tenant gates first — never let the lock + budget queries reveal
    # existence of a cross-tenant conversation.
    project_repo = ProjectRepository(session)
    project = await project_repo.get_for_user(user.id, project_id)
    if project is None:
        await _audit_cross_tenant(
            session,
            actor_user_id=user.id,
            action="chat.cross_tenant_write_denied",
            target_id=conversation_id,
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="project not found"
        )

    conv_repo = ChatConversationRepository(session)
    conv = await conv_repo.get(user_id=user.id, conversation_id=conversation_id)
    if conv is None or conv.project_id != project_id:
        await _audit_cross_tenant(
            session,
            actor_user_id=user.id,
            action="chat.cross_tenant_write_denied",
            target_id=conversation_id,
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="conversation not found"
        )

    # Config gate — before we burn any state on the lock.
    settings = get_settings()
    if settings.anthropic_api_key is None:
        raise ChatNotConfiguredError()

    # Concurrency guard — fail fast if another turn is in flight. The
    # lock is transaction-scoped; the SSE generator opens its own
    # short-lived sessions for persistence so we keep this single
    # session+transaction alive for the duration of the stream.
    await chat_turn_lock(session, conversation_id)

    # Budget guard — single SELECT against the rollup.
    await chat_budget_check(
        session, user_id=user.id, conversation_id=conversation_id
    )

    # Resolve model_override from the conversation's meta_data if it
    # was pinned at create time. Falls back to None → DEFAULT_MODEL.
    model_override: str | None = None
    if isinstance(conv.meta_data, dict):
        candidate = conv.meta_data.get("model_override")
        if isinstance(candidate, str):
            model_override = candidate

    # Build the client + context. The client may be a fake injected by
    # tests via ``app.dependency_overrides`` on
    # :func:`_build_anthropic_client` or by monkeypatching the symbol.
    llm_client = _build_anthropic_client()
    if llm_client is None:
        # Defensive — settings said the key was set, but the SDK
        # refused to construct. Surface the same structured error.
        raise ChatNotConfiguredError()

    ctx = ChatDispatchContext(
        user_id=user.id,
        project_id=project_id,
        conversation_id=conversation_id,
        db_session_factory=get_session_maker(),
        llm_client=llm_client,
        meta={"model_override": model_override} if model_override else {},
    )

    async def _frames() -> AsyncIterator[bytes]:
        try:
            async for frame in generate_sse_events(
                ctx,
                user_message=body.content,
                model_override=model_override,
            ):
                yield frame.encode("utf-8")
        finally:
            # End the transaction the lock is bound to. ``commit`` is
            # safe even though the SSE generator opened/closed its own
            # sessions for persistence — this session only ever ran the
            # lock SELECT and the budget SELECT.
            try:
                await session.commit()
            except Exception:  # noqa: BLE001 — best effort
                await session.rollback()

    return StreamingResponse(
        _frames(),
        media_type="text/event-stream; charset=utf-8",
        headers=_SSE_HEADERS,
    )


__all__ = ["router"]
