"""``q_tables`` data access — tenant-scoped via JOIN through ``projects``.

The table itself does NOT carry ``user_id`` — tenancy is enforced
transitively via ``projects.user_id``. Every method takes ``user_id`` as
a required argument and JOINs against ``projects`` so cross-tenant
queries return ``None`` / ``[]`` (router maps that to 404).

Naming:

* ``learning_rate`` in the public API maps to the model's ``alpha_normal``
  column (the canonical "alpha" for ordinary episodes per the design
  doc). ``alpha_special`` retains its DB default — the learning helpers
  in ``learning/`` may set it explicitly if needed.
* ``discount_factor`` maps to ``gamma``.
* ``metadata`` is folded into ``table_data`` only when the model expects
  it inline. Phase 1 left QTable without a separate metadata column, so
  callers passing ``metadata`` will see it stashed under the
  ``"__meta__"`` key inside ``table_data`` (a defensive default; the
  Sleep Phase helpers will normally pass an empty dict).
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import select

from aether_api.models.project import Project
from aether_api.models.q_table import QTable
from aether_api.repositories.base import BaseRepository


class QTableRepository(BaseRepository):
    model = QTable

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    async def _user_owns_project(
        self, user_id: uuid.UUID, project_id: uuid.UUID
    ) -> bool:
        """Return True iff ``project_id`` is owned by ``user_id``.

        Used by write paths to refuse cross-tenant inserts loudly
        (``PermissionError``) rather than silently writing then leaking
        on read. Reads use the JOIN form below which simply returns
        nothing for non-owners.
        """
        stmt = select(Project.id).where(
            Project.id == project_id, Project.user_id == user_id
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none() is not None

    # ------------------------------------------------------------------
    # Reads — all JOIN through ``projects`` to enforce tenancy.
    # ------------------------------------------------------------------
    async def list_versions(
        self,
        *,
        user_id: uuid.UUID,
        project_id: uuid.UUID,
        limit: int = 50,
        offset: int = 0,
    ) -> list[QTable]:
        """Return Q-Table versions for ``project_id``, newest first.

        Cross-tenant ``project_id`` returns ``[]`` — the JOIN with
        ``projects.user_id`` strips everything the caller doesn't own.
        """
        stmt = (
            select(QTable)
            .join(Project, Project.id == QTable.project_id)
            .where(Project.user_id == user_id)
            .where(QTable.project_id == project_id)
            .order_by(QTable.version.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_version(
        self,
        *,
        user_id: uuid.UUID,
        project_id: uuid.UUID,
        version: int,
    ) -> QTable | None:
        """Return a specific version IFF the caller owns the project."""
        stmt = (
            select(QTable)
            .join(Project, Project.id == QTable.project_id)
            .where(Project.user_id == user_id)
            .where(QTable.project_id == project_id)
            .where(QTable.version == version)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_latest(
        self, *, user_id: uuid.UUID, project_id: uuid.UUID
    ) -> QTable | None:
        """Return the highest-version row for ``project_id``, or None."""
        stmt = (
            select(QTable)
            .join(Project, Project.id == QTable.project_id)
            .where(Project.user_id == user_id)
            .where(QTable.project_id == project_id)
            .order_by(QTable.version.desc())
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    # ------------------------------------------------------------------
    # Writes — pre-check ownership before INSERT.
    # ------------------------------------------------------------------
    async def insert_version(
        self,
        *,
        user_id: uuid.UUID,
        project_id: uuid.UUID,
        version: int,
        table_data: dict[str, Any],
        learning_rate: Decimal | float,
        discount_factor: Decimal | float,
        metadata: dict[str, Any] | None = None,
        sleep_run_id: uuid.UUID | None = None,
        episode_count: int = 0,
    ) -> QTable:
        """Append a new Q-Table version for ``project_id``.

        Refuses to write if ``user_id`` does not own ``project_id``
        (``PermissionError``). This is a defence-in-depth check on top
        of the FK constraint — the FK alone would let a malicious caller
        write a row that no read query of theirs would ever return,
        which is wasted state and a privacy issue.

        ``learning_rate`` -> ``alpha_normal``; ``discount_factor`` -> ``gamma``.
        Metadata is stashed inside ``table_data`` under ``"__meta__"`` if
        supplied — the Phase 1 model has no dedicated column.
        """
        if not await self._user_owns_project(user_id, project_id):
            from aether_api.learning.audit import log_cross_tenant_attempt

            await log_cross_tenant_attempt(
                actor_user_id=user_id,
                target_project_id=project_id,
                table_name="q_tables",
                operation="insert_version",
            )
            raise PermissionError(
                f"user {user_id} does not own project {project_id}"
            )

        payload: dict[str, Any] = dict(table_data)
        if metadata:
            payload.setdefault("__meta__", {}).update(metadata)

        row = QTable(
            project_id=project_id,
            version=version,
            table_data=payload,
            alpha_normal=Decimal(str(learning_rate)),
            gamma=Decimal(str(discount_factor)),
            episode_count=episode_count,
            created_by_sleep_run_id=sleep_run_id,
        )
        self.session.add(row)
        await self.session.flush()
        await self.session.refresh(row)
        return row
