"""``skills`` data access — tenant-scoped.

Mirrors the shape of :mod:`aether_api.repositories.agent_repository` so
the cognitive overhead of switching between the two is zero. All queries
go through :meth:`aether_api.repositories.base.BaseRepository._for_user`
so the tenant primitive is auditable in one place.

Execution of ``code`` is OUT OF SCOPE — see the future
``agent-execution-sandbox`` change. This module persists, lists, patches,
archives, deletes. Nothing more.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from aether_api.models.skill import SkillDefinition
from aether_api.repositories.base import BaseRepository


class SkillReferencedError(Exception):
    """Raised when a hard delete is blocked by an inbound FK.

    Today only ``agent_skills`` references ``skills(id)`` with
    ``ON DELETE RESTRICT``. The router maps this exception to HTTP 409 so
    the operator can see why the delete was refused.
    """

    def __init__(self, skill_id: uuid.UUID) -> None:
        super().__init__(
            f"skill {skill_id} is referenced by one or more agent bindings"
        )
        self.skill_id = skill_id


class SkillRepository(BaseRepository):
    model = SkillDefinition

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------
    async def list_for_user(
        self,
        user_id: uuid.UUID,
        *,
        type: str | None = None,  # noqa: A002 — matches API param
        is_active: bool | None = None,
    ) -> list[SkillDefinition]:
        stmt = self._for_user(select(SkillDefinition), user_id)
        if type is not None:
            stmt = stmt.where(SkillDefinition.type == type)
        if is_active is not None:
            stmt = stmt.where(SkillDefinition.is_active == is_active)
        stmt = stmt.order_by(
            SkillDefinition.updated_at.desc().nulls_last(), SkillDefinition.name.asc()
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_for_user(
        self, user_id: uuid.UUID, skill_id: uuid.UUID
    ) -> SkillDefinition | None:
        """Return the skill IFF it belongs to ``user_id``, else ``None``.

        Same 404-not-403 contract as agents/projects: callers translate a
        ``None`` here into HTTP 404 so cross-tenant probes cannot
        distinguish "exists, not yours" from "does not exist".
        """
        stmt = self._for_user(
            select(SkillDefinition).where(SkillDefinition.id == skill_id), user_id
        )
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
        type: str,  # noqa: A002 — matches column name
        code: str,
        runtime: str = "markdown",
        description: str | None = None,
        input_signature: dict[str, Any] | None = None,
        output_signature: dict[str, Any] | None = None,
    ) -> SkillDefinition:
        skill = SkillDefinition(
            user_id=user_id,
            name=name,
            type=type,
            code=code,
            runtime=runtime,
            description=description,
            input_signature=input_signature or {},
            output_signature=output_signature or {},
        )
        self.session.add(skill)
        await self.session.flush()
        await self.session.refresh(skill)
        return skill

    async def patch(
        self,
        skill: SkillDefinition,
        *,
        changes: dict[str, Any],
        bump_version: bool,
    ) -> SkillDefinition:
        """Apply ``changes`` to ``skill`` in-place. The caller has already
        done any cross-field validation; this method only writes.

        ``bump_version=True`` increments :attr:`SkillDefinition.version`
        by 1 — callers pass True iff ``code`` actually changed (string
        equality check at the router boundary), per spec.
        """
        for field, value in changes.items():
            setattr(skill, field, value)
        if bump_version:
            skill.version = (skill.version or 1) + 1
        await self.session.flush()
        await self.session.refresh(skill)
        return skill

    async def archive(self, skill: SkillDefinition) -> SkillDefinition:
        """Soft-archive: set ``is_active = False``. Idempotent."""
        if skill.is_active:
            skill.is_active = False
            await self.session.flush()
            await self.session.refresh(skill)
        return skill

    async def delete(self, skill: SkillDefinition) -> None:
        """Hard delete. Raises :class:`SkillReferencedError` if any
        ``agent_skills`` row still references this skill — that table
        uses ``ON DELETE RESTRICT`` so the database is the source of
        truth; we just translate the IntegrityError into a typed
        exception the router maps to HTTP 409.
        """
        try:
            await self.session.execute(
                delete(SkillDefinition).where(SkillDefinition.id == skill.id)
            )
            await self.session.flush()
        except IntegrityError as exc:
            await self.session.rollback()
            raise SkillReferencedError(skill.id) from exc
