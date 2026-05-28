"""``/api/me/audit-log`` — server-paginated audit history (per caller).

The dashboard page (``/configuracion/audit-log``) renders this through
the shadcn Table. The endpoint deliberately lives under ``/api/me`` so
it inherits the same auth contract every self-service route uses:
``Depends(current_user)``, no CSRF (it's a read), user-scoped filter.

A future ``admin`` view could expose an unfiltered list at
``/api/admin/audit-log`` — kept out of scope here to avoid widening the
attack surface before there's a UI need.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from aether_api.db.session import get_session
from aether_api.models.user import User
from aether_api.repositories.audit_repository import AuditRepository
from aether_api.tenancy.middleware import current_user

router = APIRouter(prefix="/api/me", tags=["audit-log"])

# Page size cap on GET /audit-log. Matches the design doc — small enough
# that an admin scrolling won't hammer the DB.
_AUDIT_DEFAULT_LIMIT = 20
_AUDIT_MAX_LIMIT = 100


class AuditLogItem(BaseModel):
    id: uuid.UUID
    action: str
    target_type: str
    target_id: uuid.UUID | None
    before_state: dict[str, Any] | None
    after_state: dict[str, Any] | None
    ip_address: str | None
    user_agent: str | None
    created_at: datetime | None


class AuditLogPage(BaseModel):
    items: list[AuditLogItem]
    total: int
    limit: int
    offset: int


@router.get(
    "/audit-log",
    response_model=AuditLogPage,
    status_code=status.HTTP_200_OK,
)
async def list_audit_log(
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, le=_AUDIT_MAX_LIMIT)] = _AUDIT_DEFAULT_LIMIT,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> AuditLogPage:
    """Return the caller's audit history (newest-first, offset paginated)."""
    repo = AuditRepository(session)
    rows = await repo.list_for_user(user.id, limit=limit, offset=offset)
    total = await repo.count_for_user(user.id)
    return AuditLogPage(
        items=[
            AuditLogItem(
                id=row.id,
                action=row.action,
                target_type=row.target_type,
                target_id=row.target_id,
                before_state=row.before_state,
                after_state=row.after_state,
                ip_address=str(row.ip_address) if row.ip_address is not None else None,
                user_agent=row.user_agent,
                created_at=row.created_at,
            )
            for row in rows
        ],
        total=total,
        limit=limit,
        offset=offset,
    )
