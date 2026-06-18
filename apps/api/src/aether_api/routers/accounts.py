"""``/api/accounts`` — tenant-scoped CRUD for broker/exchange accounts.

Grouping layer of the accounts-pairs hierarchy ``Exchange → Account →
Pair → Agents``. The Account OWNS the broker-credential block that used
to live on the old ``projects`` table; every pair under it inherits the
credentials (there is no per-pair override).

Also exposes the nested pair collection under an account:

* ``GET  /api/accounts/{account_id}/pairs``  — list pairs under the account.
* ``POST /api/accounts/{account_id}/pairs``  — create a pair under the account.

Invariants (every endpoint):

* ``current_user`` runs on every request — 401 if no session.
* Tenant filter (``user_id = current_user.id``) lives in the repository.
* Cross-tenant denial returns 404, NEVER 403 (no existence leak). This
  includes the nested pair routes (a foreign account → 404).
* All state-changing endpoints declare ``Depends(csrf_dependency)``.
* Charter MFA gate: a non-MFA user cannot create/patch an account with
  ``account_type='real'`` → 409 ``MFA_REQUIRED_FOR_REAL_ACCOUNT``.
* Hard-delete is blocked by the DB ``ON DELETE RESTRICT`` from pairs;
  the IntegrityError is mapped to 409.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from aether_api.db.session import get_session
from aether_api.models.user import User
from aether_api.repositories.account_repository import AccountRepository
from aether_api.repositories.exchange_repository import ExchangeRepository
from aether_api.repositories.pair_repository import PairRepository
from aether_api.routers.pairs import (
    PairCreate,
    PairDetail,
    PairListResponse,
    _create_payload,
    _to_detail,
    _to_summary,
)
from aether_api.tenancy.middleware import csrf_dependency, current_user

router = APIRouter(prefix="/api/accounts", tags=["accounts"])

# Charter MFA gate — real (non-demo) accounts require the owner to have
# MFA enabled. Only the literal string "real" flips the gate on.
_REAL_ACCOUNT_TYPE: str = "real"


def _require_mfa_for_real_account(
    *, requested_account_type: str | None, user: User
) -> None:
    if requested_account_type == _REAL_ACCOUNT_TYPE and not user.mfa_enabled:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "MFA_REQUIRED_FOR_REAL_ACCOUNT"},
        )


# ---------------------------------------------------------------------------
# DTOs
# ---------------------------------------------------------------------------
class AccountDetail(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    exchange_id: uuid.UUID
    name: str
    description: str | None = None

    # Broker-credential block (lives on the account, inherited by pairs).
    account_login: str | None = None
    account_server: str | None = None
    broker_name: str | None = None
    account_credential_ref: str | None = None
    account_currency: str | None = None
    account_leverage: int | None = None
    account_type: str | None = None

    meta_data: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime | None = None
    updated_at: datetime | None = None


class AccountCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    exchange_id: uuid.UUID
    name: str = Field(min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=2000)

    account_login: str | None = Field(default=None, max_length=50)
    account_server: str | None = Field(default=None, max_length=100)
    broker_name: str | None = Field(default=None, max_length=80)
    account_credential_ref: str | None = Field(default=None, max_length=255)
    account_currency: str | None = Field(default=None, max_length=10)
    account_leverage: int | None = Field(default=None, ge=1, le=10_000)
    account_type: str | None = Field(default=None, max_length=20)

    meta_data: dict[str, Any] = Field(default_factory=dict)


class AccountPatch(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    exchange_id: uuid.UUID | None = None
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=2000)

    account_login: str | None = Field(default=None, max_length=50)
    account_server: str | None = Field(default=None, max_length=100)
    broker_name: str | None = Field(default=None, max_length=80)
    account_credential_ref: str | None = Field(default=None, max_length=255)
    account_currency: str | None = Field(default=None, max_length=10)
    account_leverage: int | None = Field(default=None, ge=1, le=10_000)
    account_type: str | None = Field(default=None, max_length=20)

    meta_data: dict[str, Any] | None = None


class AccountListResponse(BaseModel):
    items: list[AccountDetail]
    total: int
    limit: int
    offset: int


# Pair create under a nested account — same shape as PairCreate but the
# account is taken from the path, so the body must NOT carry account_id.
class NestedPairCreate(PairCreate):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    account_id: uuid.UUID | None = None  # ignored — taken from the path


# ---------------------------------------------------------------------------
# Account CRUD
# ---------------------------------------------------------------------------
@router.get("", response_model=AccountListResponse)
async def list_accounts(
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    response: Response,
    exchange_id: Annotated[uuid.UUID | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=10_000)] = 0,
) -> AccountListResponse:
    repo = AccountRepository(session)
    rows = await repo.list_for_user(
        user.id, exchange_id=exchange_id, limit=limit, offset=offset
    )
    total = await repo.count_for_user(user.id, exchange_id=exchange_id)
    response.headers["X-Total-Count"] = str(total)
    return AccountListResponse(
        items=[AccountDetail.model_validate(r, from_attributes=True) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{account_id}", response_model=AccountDetail)
async def get_account(
    account_id: uuid.UUID,
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AccountDetail:
    repo = AccountRepository(session)
    row = await repo.get_for_user(user.id, account_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="account not found"
        )
    return AccountDetail.model_validate(row, from_attributes=True)


@router.post(
    "",
    response_model=AccountDetail,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(csrf_dependency)],
)
async def create_account(
    body: AccountCreate,
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AccountDetail:
    _require_mfa_for_real_account(
        requested_account_type=body.account_type, user=user
    )

    # Tenancy gate: the owning exchange must belong to the caller — a
    # foreign exchange → 404 (no existence leak), never a raw FK error.
    if await ExchangeRepository(session).get_for_user(user.id, body.exchange_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="exchange not found"
        )

    repo = AccountRepository(session)
    fields = body.model_dump(exclude={"exchange_id"})
    row = await repo.create(user.id, exchange_id=body.exchange_id, **fields)
    await session.commit()
    await session.refresh(row)
    return AccountDetail.model_validate(row, from_attributes=True)


@router.patch(
    "/{account_id}",
    response_model=AccountDetail,
    dependencies=[Depends(csrf_dependency)],
)
async def patch_account(
    account_id: uuid.UUID,
    body: AccountPatch,
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AccountDetail:
    updates = body.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(
            status_code=400, detail="PATCH body must include at least one field"
        )

    if "account_type" in updates:
        _require_mfa_for_real_account(
            requested_account_type=updates["account_type"], user=user
        )

    repo = AccountRepository(session)
    try:
        updated = await repo.update_fields(user.id, account_id, updates)
    except PermissionError:
        # Reparenting to a foreign exchange — 404, no existence leak.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="exchange not found"
        ) from None
    if updated is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="account not found"
        )
    await session.commit()
    await session.refresh(updated)
    return AccountDetail.model_validate(updated, from_attributes=True)


@router.delete(
    "/{account_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(csrf_dependency)],
)
async def delete_account(
    account_id: uuid.UUID,
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    repo = AccountRepository(session)
    existing = await repo.get_for_user(user.id, account_id)
    if existing is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="account not found"
        )
    try:
        await repo.delete(user.id, account_id)
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="account still has pairs; delete them first",
        ) from None
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Nested pair collection under an account
# ---------------------------------------------------------------------------
async def _ensure_account_owned(
    session: AsyncSession, user: User, account_id: uuid.UUID
) -> None:
    """Raise 404 if ``account_id`` is not owned by ``user`` (no leak)."""
    if await AccountRepository(session).get_for_user(user.id, account_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="account not found"
        )


@router.get("/{account_id}/pairs", response_model=PairListResponse)
async def list_account_pairs(
    account_id: uuid.UUID,
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    response: Response,
    status_filter: Annotated[str | None, Query(alias="status", max_length=20)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=10_000)] = 0,
) -> PairListResponse:
    from aether_api.models.pair import PAIR_STATUSES

    await _ensure_account_owned(session, user, account_id)
    if status_filter is not None and status_filter not in PAIR_STATUSES:
        raise HTTPException(status_code=400, detail="invalid status filter")

    repo = PairRepository(session)
    rows = await repo.list_for_account(
        user.id, account_id, status=status_filter, limit=limit, offset=offset
    )
    total = await repo.count_for_account(user.id, account_id, status=status_filter)
    response.headers["X-Total-Count"] = str(total)
    return PairListResponse(
        items=[_to_summary(r) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post(
    "/{account_id}/pairs",
    response_model=PairDetail,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(csrf_dependency)],
)
async def create_account_pair(
    account_id: uuid.UUID,
    body: NestedPairCreate,
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> PairDetail:
    await _ensure_account_owned(session, user, account_id)

    repo = PairRepository(session)
    if await repo.name_taken_for_user(user.id, body.name):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="pair name already in use",
        )

    fields = _create_payload(body)
    # The account is authoritative from the path — override whatever the
    # body carried (NestedPairCreate makes it optional / ignored).
    fields["account_id"] = account_id
    pair = await repo.create(user.id, **fields)
    await session.commit()
    await session.refresh(pair)
    return _to_detail(pair)
