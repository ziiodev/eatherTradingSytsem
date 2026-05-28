"""``audit_log`` writer + reader.

The application role can INSERT and SELECT on this table; UPDATE/DELETE
are revoked by migration 0002 (see :file:`apps/api/alembic/versions/0002_audit_log.py`).
This repository mirrors that grant set: it exposes :meth:`record` for the
single INSERT path and :meth:`list_for_user` for the dashboard read path,
and deliberately offers no update / delete helpers.

Callers are expected to PII-scrub ``before`` / ``after`` mappings before
passing them in. We do NOT re-scrub here because the same fields will be
redacted at the log layer via :func:`aether_api.core.pii.scrub_mapping`
when they later flow into Sentry / structured logs.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Any

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aether_api.core.settings import get_settings
from aether_api.models.audit_log import AuditLog


def _client_ip(request: Request | None) -> str | None:
    """Mirror the helper in :mod:`aether_api.routers.me` — honour XFF then peer."""
    if request is None:
        return None
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",", 1)[0].strip()
    if request.client is None:
        return None
    return request.client.host


class AuditRepository:
    """Append-only writer for the ``audit_log`` table."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def record(
        self,
        *,
        user_id: uuid.UUID | None,
        action: str,
        target_type: str,
        target_id: uuid.UUID | None = None,
        before: Mapping[str, Any] | None = None,
        after: Mapping[str, Any] | None = None,
        request: Request | None = None,
    ) -> AuditLog | None:
        """Insert one audit row. Returns ``None`` when audit writes are off.

        Gated by :attr:`Settings.audit_log_enabled` so a bootstrap
        environment behaves identically to today's runtime. Once enabled,
        the row is added to the SQLAlchemy session but NOT committed — the
        caller owns the transaction (a successful business operation and
        its audit row commit together; a failure rolls both back).
        """
        if not get_settings().audit_log_enabled:
            return None

        row = AuditLog(
            user_id=user_id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            before_state=dict(before) if before is not None else None,
            after_state=dict(after) if after is not None else None,
            ip_address=_client_ip(request),
            user_agent=request.headers.get("user-agent") if request is not None else None,
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def list_for_user(
        self,
        user_id: uuid.UUID,
        *,
        limit: int = 20,
        offset: int = 0,
    ) -> list[AuditLog]:
        """Return the caller's audit rows, newest-first.

        Offset pagination is fine here — the table is small per-user
        (audit volume is bounded by the user's own action rate), and the
        dashboard only ever reads the first few pages. If that assumption
        breaks, swap for a keyset cursor.
        """
        stmt = (
            select(AuditLog)
            .where(AuditLog.user_id == user_id)
            .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def count_for_user(self, user_id: uuid.UUID) -> int:
        """Return the row count for the caller's audit history."""
        from sqlalchemy import func

        stmt = select(func.count(AuditLog.id)).where(AuditLog.user_id == user_id)
        result = await self.session.execute(stmt)
        return int(result.scalar() or 0)
