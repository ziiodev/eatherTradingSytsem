"""``/api/projects/{id}/q-tables`` + ``episodic-memory`` + ``semantic-memory``.

Read-only surface for the four sleep-learning-loop tables. Writes
happen exclusively through the Sleep Phase orchestrator and the
Worker ctx proxies — NEVER through HTTP.

Endpoints (all GET, all auth-gated):

* ``GET /api/projects/{project_id}/q-tables``                  — list versions (paginated).
* ``GET /api/projects/{project_id}/q-tables/{version}``        — single Q-Table version.
* ``GET /api/projects/{project_id}/episodic-memory``           — paginated episodes
                                                                  (since/until/state_key).
* ``GET /api/projects/{project_id}/semantic-memory``           — active rules
                                                                  (rule_type filter).

Invariants enforced here (every endpoint):

* ``current_user`` dependency runs on every request — 401 if no session.
* Tenant filter (``user_id = current_user.id``) lives in the repository,
  never in the router. Repositories JOIN through ``projects.user_id``.
* Cross-tenant denial returns 404, NEVER 403 (existence non-disclosure
  per ``specs/multi-tenancy`` and the sleep-learning-loop delta).
* No POST/PUT/PATCH/DELETE — by design. Writes happen via the
  orchestrator transaction or the sandboxed Worker context.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from aether_api.db.session import get_session
from aether_api.models.user import User
from aether_api.repositories.episodic_memory_repository import (
    EpisodicMemoryRepository,
)
from aether_api.repositories.project_repository import ProjectRepository
from aether_api.repositories.q_table_repository import QTableRepository
from aether_api.repositories.semantic_memory_repository import (
    SemanticMemoryRepository,
)
from aether_api.tenancy.middleware import current_user

router = APIRouter(prefix="/api/projects", tags=["learning"])


# ---------------------------------------------------------------------------
# DTOs — Q-Tables
# ---------------------------------------------------------------------------
class QTableResponse(BaseModel):
    """Full Q-Table version, including the ``table_data`` JSONB."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    version: int
    table_data: dict[str, Any] = Field(default_factory=dict)
    alpha_normal: Decimal
    alpha_special: Decimal
    gamma: Decimal
    episode_count: int
    created_by_sleep_run_id: uuid.UUID | None = None
    created_at: datetime | None = None


class QTableListItem(BaseModel):
    """Slim Q-Table entry for list responses — omits the heavy ``table_data``."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    version: int
    alpha_normal: Decimal
    alpha_special: Decimal
    gamma: Decimal
    episode_count: int
    created_by_sleep_run_id: uuid.UUID | None = None
    created_at: datetime | None = None


class QTableListResponse(BaseModel):
    """Paginated Q-Table list — newest version first."""

    items: list[QTableListItem]
    total: int


# ---------------------------------------------------------------------------
# DTOs — Episodic Memory
# ---------------------------------------------------------------------------
class EpisodicMemoryResponse(BaseModel):
    """One (s, a, r, s') episode."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    state_key: str
    action: str
    reward: Decimal
    next_state_key: str | None = None
    order_id: uuid.UUID | None = None
    consumed_by_sleep_run_id: uuid.UUID | None = None
    meta_data: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime | None = None


class EpisodicMemoryListResponse(BaseModel):
    """Paginated episode list — newest first within the (since, until) window."""

    items: list[EpisodicMemoryResponse]
    total: int


# ---------------------------------------------------------------------------
# DTOs — Semantic Memory
# ---------------------------------------------------------------------------
class SemanticMemoryResponse(BaseModel):
    """One semantic rule (active by default)."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    rule_type: str
    body: str
    payload: dict[str, Any] = Field(default_factory=dict)
    superseded_by: uuid.UUID | None = None
    active: bool
    created_by_sleep_run_id: uuid.UUID | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class SemanticMemoryListResponse(BaseModel):
    """Active rule list — ordered newest-first."""

    items: list[SemanticMemoryResponse]
    total: int


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
async def _assert_owns_project(
    session: AsyncSession, user: User, project_id: uuid.UUID
) -> None:
    """Raise 404 if ``project_id`` is not owned by ``user``.

    Existence non-disclosure: a 403 here would confirm the row exists for
    another tenant. Per ``multi-tenancy-delta`` (#2068) every cross-tenant
    learning read MUST resolve to 404.
    """
    repo = ProjectRepository(session)
    project = await repo.get_for_user(user.id, project_id)
    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="project not found"
        )


# Default episodic-memory window — last 7 days when the caller omits both
# ``since`` and ``until``. The pagination spec defaults to 100 items, so a
# 7-day window keeps the typical response bounded for active projects.
_EPISODIC_DEFAULT_WINDOW = timedelta(days=7)


def _resolve_episodic_window(
    since: datetime | None, until: datetime | None
) -> tuple[datetime, datetime]:
    """Normalize the (since, until) pair into naive UTC datetimes.

    The DB columns are ``TIMESTAMP`` (no timezone), so we strip tzinfo
    after converting to UTC — comparisons stay correct because every
    write also lands in UTC.
    """
    now = datetime.now(tz=UTC).replace(tzinfo=None)
    if until is None:
        until_naive = now
    elif until.tzinfo is not None:
        until_naive = until.astimezone(UTC).replace(tzinfo=None)
    else:
        until_naive = until
    if since is None:
        since_naive = now - _EPISODIC_DEFAULT_WINDOW
    elif since.tzinfo is not None:
        since_naive = since.astimezone(UTC).replace(tzinfo=None)
    else:
        since_naive = since
    return since_naive, until_naive


# ---------------------------------------------------------------------------
# Q-Tables
# ---------------------------------------------------------------------------
@router.get(
    "/{project_id}/q-tables",
    response_model=QTableListResponse,
)
async def list_q_tables(
    project_id: uuid.UUID,
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0, le=10_000)] = 0,
) -> QTableListResponse:
    """List Q-Table versions for ``project_id`` (newest version first).

    404 if the project does not belong to the caller (existence
    non-disclosure). Cross-tenant calls never see a 403.

    ``total`` reflects ALL versions for the project (not the page size).
    """
    await _assert_owns_project(session, user, project_id)

    q_repo = QTableRepository(session)
    rows = await q_repo.list_versions(
        user_id=user.id,
        project_id=project_id,
        limit=limit,
        offset=offset,
    )
    # Count: cheapest correct path is to ask for the highest-version row
    # and reuse its ``version`` field — versions are dense, starting at 1.
    # If the project has zero versions, ``latest`` is None → total 0.
    latest = await q_repo.get_latest(user_id=user.id, project_id=project_id)
    total = int(latest.version) if latest is not None else 0
    return QTableListResponse(
        items=[QTableListItem.model_validate(r) for r in rows],
        total=total,
    )


@router.get(
    "/{project_id}/q-tables/{version}",
    response_model=QTableResponse,
)
async def get_q_table_version(
    project_id: uuid.UUID,
    version: int,
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> QTableResponse:
    """Return a single Q-Table version (full ``table_data`` JSONB included).

    404 if the version does not exist OR the project is cross-tenant —
    we don't differentiate, by design.
    """
    if version < 1:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="q-table not found"
        )

    q_repo = QTableRepository(session)
    row = await q_repo.get_version(
        user_id=user.id,
        project_id=project_id,
        version=version,
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="q-table not found"
        )
    return QTableResponse.model_validate(row)


# ---------------------------------------------------------------------------
# Episodic memory
# ---------------------------------------------------------------------------
@router.get(
    "/{project_id}/episodic-memory",
    response_model=EpisodicMemoryListResponse,
)
async def list_episodic_memory(
    project_id: uuid.UUID,
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    since: Annotated[datetime | None, Query()] = None,
    until: Annotated[datetime | None, Query()] = None,
    state_key: Annotated[str | None, Query(max_length=120)] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
) -> EpisodicMemoryListResponse:
    """Return episodes for ``project_id`` filtered by time window + state_key.

    Defaults:
      * ``since`` = ``now - 7 days``
      * ``until`` = ``now``

    404 on cross-tenant. ``total`` reflects the page length (we don't
    issue a second COUNT query — pagination is offset/limit anyway).
    """
    await _assert_owns_project(session, user, project_id)

    since_naive, until_naive = _resolve_episodic_window(since, until)

    ep_repo = EpisodicMemoryRepository(session)
    rows = await ep_repo.list_by_project(
        user_id=user.id,
        project_id=project_id,
        since=since_naive,
        until=until_naive,
        state_key=state_key,
        limit=limit,
        offset=offset,
    )
    return EpisodicMemoryListResponse(
        items=[EpisodicMemoryResponse.model_validate(r) for r in rows],
        total=len(rows),
    )


# ---------------------------------------------------------------------------
# Semantic memory
# ---------------------------------------------------------------------------
@router.get(
    "/{project_id}/semantic-memory",
    response_model=SemanticMemoryListResponse,
)
async def list_semantic_memory(
    project_id: uuid.UUID,
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    rule_type: Annotated[str | None, Query(max_length=40)] = None,
    active: Annotated[bool, Query()] = True,
) -> SemanticMemoryListResponse:
    """Return semantic rules for ``project_id``.

    ``active`` defaults to True. The repository surface (``list_active``)
    only exposes active rows — passing ``active=false`` returns an empty
    list rather than the supersession history (the long-running history
    surface is intentionally out of scope for Phase 9).

    404 on cross-tenant.
    """
    await _assert_owns_project(session, user, project_id)

    # ``active`` query arg accepted for forward-compat with a future history
    # endpoint; we only serve active rules in Phase 9 to keep the repo
    # contract narrow. A ``False`` value yields an empty list.
    if active is False:
        return SemanticMemoryListResponse(items=[], total=0)

    sm_repo = SemanticMemoryRepository(session)
    rows = await sm_repo.list_active(
        user_id=user.id,
        project_id=project_id,
        rule_type=rule_type,
    )
    return SemanticMemoryListResponse(
        items=[SemanticMemoryResponse.model_validate(r) for r in rows],
        total=len(rows),
    )


__all__ = [
    "EpisodicMemoryListResponse",
    "EpisodicMemoryResponse",
    "QTableListItem",
    "QTableListResponse",
    "QTableResponse",
    "SemanticMemoryListResponse",
    "SemanticMemoryResponse",
    "router",
]
