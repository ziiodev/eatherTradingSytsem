"""Writer + reader for the ``container_events`` audit table.

The table backs the per-project infraestructura events feed and acts as
the durable log for every lifecycle call the API makes against the
docker-socket-proxy. Append-only at the application layer (no update /
delete helpers are exposed); the canonical security audit lives in
``audit_log`` (see migration 0002).
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from aether_api.models.container_event import ContainerEvent


class ContainerEventsRepository:
    """Append-only writer / reader for the ``container_events`` table."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def record(
        self,
        *,
        project_id: uuid.UUID,
        user_id: uuid.UUID,
        action: str,
        status: str,
        payload: Mapping[str, Any] | None = None,
        error: str | None = None,
    ) -> ContainerEvent:
        """Insert one audit row. Caller owns the transaction.

        ``payload`` callers MUST PII-scrub before passing in. ``error``
        is truncated at 4 KiB to keep one giant Docker error from
        bloating a row indefinitely.
        """
        truncated_error = error[:4096] if error else None
        row = ContainerEvent(
            project_id=project_id,
            user_id=user_id,
            action=action,
            status=status,
            payload=dict(payload) if payload is not None else {},
            error=truncated_error,
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def list_for_project(
        self,
        project_id: uuid.UUID,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> list[ContainerEvent]:
        """Newest-first feed for the project infraestructura panel."""
        stmt = (
            select(ContainerEvent)
            .where(ContainerEvent.project_id == project_id)
            .order_by(ContainerEvent.created_at.desc(), ContainerEvent.id.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def count_for_project(self, project_id: uuid.UUID) -> int:
        stmt = select(func.count(ContainerEvent.id)).where(
            ContainerEvent.project_id == project_id
        )
        result = await self.session.execute(stmt)
        return int(result.scalar() or 0)


__all__ = ["ContainerEventsRepository"]
