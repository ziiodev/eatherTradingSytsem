"""``semantic_memory`` data access — tenant-scoped via JOIN through ``projects``.

Semantic memory rules are NEVER hard-deleted. The Orquestador either
flips ``active = false`` or inserts a new rule and points ``superseded_by``
back at the previous one. ``list_active`` is the working set: rows where
``active = true``, optionally filtered by ``rule_type``.

Mapping of the design's named parameters into Phase 1's columns:

* ``title``       -> ``payload["title"]``
* ``content``     -> ``body`` (the Text column)
* ``confidence``  -> ``payload["confidence"]``
* ``source``      -> ``payload["source"]``

This keeps the model surface narrow while still preserving everything the
dashboard renders.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select, update

from aether_api.models.project import Project
from aether_api.models.semantic_memory import SemanticMemory
from aether_api.repositories.base import BaseRepository


class SemanticMemoryRepository(BaseRepository):
    model = SemanticMemory

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
    # Reads
    # ------------------------------------------------------------------
    async def list_active(
        self,
        *,
        user_id: uuid.UUID,
        project_id: uuid.UUID,
        rule_type: str | None = None,
    ) -> list[SemanticMemory]:
        """Return active rules for ``project_id``, optionally filtered by type."""
        stmt = (
            select(SemanticMemory)
            .join(Project, Project.id == SemanticMemory.project_id)
            .where(Project.user_id == user_id)
            .where(SemanticMemory.project_id == project_id)
            .where(SemanticMemory.active.is_(True))
        )
        if rule_type is not None:
            stmt = stmt.where(SemanticMemory.rule_type == rule_type)
        stmt = stmt.order_by(SemanticMemory.created_at.desc())
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------
    async def insert(
        self,
        *,
        user_id: uuid.UUID,
        project_id: uuid.UUID,
        rule_type: str,
        title: str,
        content: str,
        confidence: float,
        source: str,
        sleep_run_id: uuid.UUID | None = None,
    ) -> SemanticMemory:
        """Append a new semantic rule. Refuses cross-tenant writes."""
        if not await self._user_owns_project(user_id, project_id):
            from aether_api.learning.audit import log_cross_tenant_attempt

            await log_cross_tenant_attempt(
                actor_user_id=user_id,
                target_project_id=project_id,
                table_name="semantic_memory",
                operation="insert",
            )
            raise PermissionError(
                f"user {user_id} does not own project {project_id}"
            )

        row = SemanticMemory(
            project_id=project_id,
            rule_type=rule_type,
            body=content,
            payload={
                "title": title,
                "confidence": confidence,
                "source": source,
            },
            active=True,
            created_by_sleep_run_id=sleep_run_id,
        )
        self.session.add(row)
        await self.session.flush()
        await self.session.refresh(row)
        return row

    async def supersede(
        self,
        *,
        user_id: uuid.UUID,
        project_id: uuid.UUID,
        rule_id: uuid.UUID,
        new_rule_id: uuid.UUID,
    ) -> None:
        """Deactivate ``rule_id`` and link it to ``new_rule_id``.

        Uses a tenant-scoped UPDATE: the predicate JOINs through
        ``projects.user_id``, so a cross-tenant caller cannot flip a
        row they don't own — the UPDATE silently matches zero rows.
        This matches the canonical 404-not-403 contract: the caller
        learns nothing about the rule's existence.
        """
        # Sub-select of projects owned by user — the UPDATE constrains
        # to rules whose project_id is in that set.
        owned_projects = select(Project.id).where(Project.user_id == user_id)

        stmt = (
            update(SemanticMemory)
            .where(SemanticMemory.id == rule_id)
            .where(SemanticMemory.project_id == project_id)
            .where(SemanticMemory.project_id.in_(owned_projects))
            .values(active=False, superseded_by=new_rule_id)
        )
        await self.session.execute(stmt)
        await self.session.flush()
