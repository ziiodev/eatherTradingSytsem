"""``/api/exchanges`` — tenant-scoped CRUD for trading venues.

Top of the accounts-pairs hierarchy ``Exchange → Account → Pair → Agents``.

Invariants (every endpoint):

* ``current_user`` dependency runs on every request — 401 if no session.
* Tenant filter (``user_id = current_user.id``) lives in the repository.
* Cross-tenant denial returns 404, NEVER 403 (existence is not disclosed).
* All state-changing endpoints declare ``Depends(csrf_dependency)``.
* ``code`` is unique per tenant — duplicate → 409.
* Hard-delete is blocked by the DB ``ON DELETE RESTRICT`` from accounts;
  the IntegrityError is mapped to 409.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from aether_api.db.session import get_session
from aether_api.models.exchange import EXCHANGE_KINDS
from aether_api.models.user import User
from aether_api.repositories.exchange_repository import ExchangeRepository
from aether_api.tenancy.middleware import csrf_dependency, current_user

router = APIRouter(prefix="/api/exchanges", tags=["exchanges"])


# ---------------------------------------------------------------------------
# DTOs
# ---------------------------------------------------------------------------
class ExchangeDetail(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    code: str
    kind: str
    meta_data: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ExchangeCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=100)
    code: str = Field(min_length=1, max_length=40)
    kind: str = Field(default="broker", max_length=20)
    meta_data: dict[str, Any] = Field(default_factory=dict)

    @field_validator("kind")
    @classmethod
    def _kind_valid(cls, v: str) -> str:
        if v not in EXCHANGE_KINDS:
            raise ValueError(f"kind must be one of {list(EXCHANGE_KINDS)}")
        return v


class ExchangePatch(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str | None = Field(default=None, min_length=1, max_length=100)
    code: str | None = Field(default=None, min_length=1, max_length=40)
    kind: str | None = Field(default=None, max_length=20)
    meta_data: dict[str, Any] | None = None

    @field_validator("kind")
    @classmethod
    def _kind_valid(cls, v: str | None) -> str | None:
        if v is None:
            return None
        if v not in EXCHANGE_KINDS:
            raise ValueError(f"kind must be one of {list(EXCHANGE_KINDS)}")
        return v


class ExchangeListResponse(BaseModel):
    items: list[ExchangeDetail]
    total: int
    limit: int
    offset: int


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@router.get("", response_model=ExchangeListResponse)
async def list_exchanges(
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    response: Response,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=10_000)] = 0,
) -> ExchangeListResponse:
    repo = ExchangeRepository(session)
    rows = await repo.list_for_user(user.id, limit=limit, offset=offset)
    total = await repo.count_for_user(user.id)
    response.headers["X-Total-Count"] = str(total)
    return ExchangeListResponse(
        items=[ExchangeDetail.model_validate(r, from_attributes=True) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{exchange_id}", response_model=ExchangeDetail)
async def get_exchange(
    exchange_id: uuid.UUID,
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ExchangeDetail:
    repo = ExchangeRepository(session)
    row = await repo.get_for_user(user.id, exchange_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="exchange not found"
        )
    return ExchangeDetail.model_validate(row, from_attributes=True)


@router.post(
    "",
    response_model=ExchangeDetail,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(csrf_dependency)],
)
async def create_exchange(
    body: ExchangeCreate,
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ExchangeDetail:
    repo = ExchangeRepository(session)
    if await repo.code_taken_for_user(user.id, body.code):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="exchange code already in use",
        )
    row = await repo.create(user.id, **body.model_dump())
    await session.commit()
    await session.refresh(row)
    return ExchangeDetail.model_validate(row, from_attributes=True)


@router.patch(
    "/{exchange_id}",
    response_model=ExchangeDetail,
    dependencies=[Depends(csrf_dependency)],
)
async def patch_exchange(
    exchange_id: uuid.UUID,
    body: ExchangePatch,
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ExchangeDetail:
    updates = body.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(status_code=400, detail="PATCH body must include at least one field")

    repo = ExchangeRepository(session)
    new_code = updates.get("code")
    if new_code is not None and await repo.code_taken_for_user(
        user.id, new_code, exclude_id=exchange_id
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="exchange code already in use",
        )

    updated = await repo.update_fields(user.id, exchange_id, updates)
    if updated is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="exchange not found"
        )
    await session.commit()
    await session.refresh(updated)
    return ExchangeDetail.model_validate(updated, from_attributes=True)


@router.delete(
    "/{exchange_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(csrf_dependency)],
)
async def delete_exchange(
    exchange_id: uuid.UUID,
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    repo = ExchangeRepository(session)
    existing = await repo.get_for_user(user.id, exchange_id)
    if existing is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="exchange not found"
        )
    try:
        await repo.delete(user.id, exchange_id)
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="exchange still has accounts; delete them first",
        ) from None
    return Response(status_code=status.HTTP_204_NO_CONTENT)
