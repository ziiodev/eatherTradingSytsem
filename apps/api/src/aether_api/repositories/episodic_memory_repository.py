"""``episodic_memory`` data access — tenant-scoped via JOIN through ``projects``.

Episodes are append-only (s, a, r, s') tuples produced by the Worker per
closed trade and consumed by the Orquestador during Sleep Phase
synthesis. The table carries ``project_id`` only — tenancy is enforced
transitively via ``projects.user_id``.

The structured extras the Worker emits (``result``, ``worker_reasoning``,
``q_value_before``/``after``, ``is_special``, attached trade) all live
under the ``meta_data`` JSONB column. The first-class columns are kept
narrow on purpose — they're the ones the Sleep Phase queries index on.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select

from aether_api.models.episodic_memory import EpisodicMemory
from aether_api.models.project import Project
from aether_api.repositories.base import BaseRepository


class EpisodicMemoryRepository(BaseRepository):
    model = EpisodicMemory

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    async def _user_owns_project(
        self, user_id: uuid.UUID, project_id: uuid.UUID
    ) -> bool:
        stmt = select(Project.id).where(
            Project.id == project_id, Project.user_id == user_id
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none() is not None

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------
    async def insert(
        self,
        *,
        user_id: uuid.UUID,
        project_id: uuid.UUID,
        trade_id: uuid.UUID | None,
        state: dict[str, Any],
        state_key: str,
        action: str,
        reward: Decimal | float,
        result: str | None,
        worker_reasoning: str | None,
        q_value_before: Decimal | float | None,
        q_value_after: Decimal | float | None,
        is_special: bool,
        sleep_run_id: uuid.UUID | None = None,
        next_state_key: str | None = None,
    ) -> EpisodicMemory:
        """Append one episode for ``project_id``.

        ``user_id`` MUST match the project owner; we reject early with a
        ``PermissionError`` rather than silently writing a row no read
        path of the caller's would ever return (avoids leaking storage
        to a malicious caller).
        """
        if not await self._user_owns_project(user_id, project_id):
            raise PermissionError(
                f"user {user_id} does not own project {project_id}"
            )

        meta: dict[str, Any] = {
            "state": state,
            "result": result,
            "worker_reasoning": worker_reasoning,
            "is_special": is_special,
        }
        if q_value_before is not None:
            meta["q_value_before"] = str(Decimal(str(q_value_before)))
        if q_value_after is not None:
            meta["q_value_after"] = str(Decimal(str(q_value_after)))

        row = EpisodicMemory(
            project_id=project_id,
            state_key=state_key,
            action=action,
            reward=Decimal(str(reward)),
            next_state_key=next_state_key,
            order_id=trade_id,
            consumed_by_sleep_run_id=sleep_run_id,
            meta_data=meta,
        )
        self.session.add(row)
        await self.session.flush()
        await self.session.refresh(row)
        return row

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------
    async def list_by_project(
        self,
        *,
        user_id: uuid.UUID,
        project_id: uuid.UUID,
        since: datetime | None,
        until: datetime | None,
        state_key: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[EpisodicMemory]:
        """Return episodes for ``project_id`` within the time window."""
        stmt = (
            select(EpisodicMemory)
            .join(Project, Project.id == EpisodicMemory.project_id)
            .where(Project.user_id == user_id)
            .where(EpisodicMemory.project_id == project_id)
        )
        if since is not None:
            stmt = stmt.where(EpisodicMemory.created_at >= since)
        if until is not None:
            stmt = stmt.where(EpisodicMemory.created_at < until)
        if state_key is not None:
            stmt = stmt.where(EpisodicMemory.state_key == state_key)
        stmt = (
            stmt.order_by(EpisodicMemory.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def top_k_states(
        self,
        *,
        user_id: uuid.UUID,
        project_id: uuid.UUID,
        k: int,
    ) -> list[tuple[str, int]]:
        """Return the ``k`` most frequent ``state_key`` values for the project.

        Result is ``[(state_key, count), ...]`` ordered by count DESC,
        state_key ASC for deterministic ties. Cross-tenant ``project_id``
        returns an empty list — the JOIN with projects strips it.
        """
        stmt = (
            select(EpisodicMemory.state_key, func.count().label("freq"))
            .join(Project, Project.id == EpisodicMemory.project_id)
            .where(Project.user_id == user_id)
            .where(EpisodicMemory.project_id == project_id)
            .group_by(EpisodicMemory.state_key)
            .order_by(func.count().desc(), EpisodicMemory.state_key.asc())
            .limit(k)
        )
        result = await self.session.execute(stmt)
        return [(row[0], int(row[1])) for row in result.all()]
