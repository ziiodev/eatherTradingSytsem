"""``eas`` data access — tenant-scoped.

Mirrors the shape of :mod:`aether_api.repositories.skill_repository` so the
cognitive overhead of switching between the two is zero. Every query goes
through :meth:`aether_api.repositories.base.BaseRepository._for_user` so the
tenant primitive is auditable in one place — cross-tenant rows are invisible
(the router translates a ``None`` from :meth:`get_for_user` into HTTP 404,
never 403).

Soft-archive (``is_active = False``) is the only "delete" — EAs are never
hard-deleted here, matching the charter audit-trail posture for user-scoped
artifacts.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select

from aether_api.models.ea import EA
from aether_api.repositories.base import BaseRepository

#: Default empty-but-valid graph envelope (mirrors the DDL default). Used when a
#: create payload omits ``graph`` entirely so the row is never NULL-bodied.
EMPTY_GRAPH: dict[str, Any] = {"nodes": [], "edges": []}


class EaRepository(BaseRepository):
    model = EA

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------
    async def list_for_user(
        self,
        user_id: uuid.UUID,
        *,
        is_active: bool | None = None,
    ) -> list[EA]:
        stmt = self._for_user(select(EA), user_id)
        if is_active is not None:
            stmt = stmt.where(EA.is_active == is_active)
        stmt = stmt.order_by(EA.updated_at.desc().nulls_last(), EA.name.asc())
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_for_user(self, user_id: uuid.UUID, ea_id: uuid.UUID) -> EA | None:
        """Return the EA IFF it belongs to ``user_id``, else ``None``.

        Same 404-not-403 contract as skills/agents/pairs: callers translate a
        ``None`` here into HTTP 404 so cross-tenant probes cannot distinguish
        "exists, not yours" from "does not exist".
        """
        stmt = self._for_user(select(EA).where(EA.id == ea_id), user_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------
    async def create(
        self,
        *,
        user_id: uuid.UUID,
        name: str,
        description: str | None = None,
        graph: dict[str, Any] | None = None,
    ) -> EA:
        ea = EA(
            user_id=user_id,
            name=name,
            description=description,
            graph=graph if graph is not None else dict(EMPTY_GRAPH),
        )
        self.session.add(ea)
        await self.session.flush()
        await self.session.refresh(ea)
        return ea

    async def patch(
        self,
        ea: EA,
        *,
        changes: dict[str, Any],
        bump_version: bool,
    ) -> EA:
        """Apply ``changes`` to ``ea`` in-place. The caller has already done any
        cross-field validation; this method only writes.

        ``bump_version=True`` increments :attr:`EA.version` by 1 — callers pass
        True iff ``graph`` actually changed, per the skills-domain convention
        (the graph is the executable body, the analogue of ``skills.code``).
        """
        for field, value in changes.items():
            setattr(ea, field, value)
        if bump_version:
            ea.version = (ea.version or 1) + 1
        await self.session.flush()
        await self.session.refresh(ea)
        return ea

    async def archive(self, ea: EA) -> EA:
        """Soft-archive: set ``is_active = False``. Idempotent."""
        if ea.is_active:
            ea.is_active = False
            await self.session.flush()
            await self.session.refresh(ea)
        return ea
