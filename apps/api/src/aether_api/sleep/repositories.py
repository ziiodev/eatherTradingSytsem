"""Async repositories for sleep_runs / sleep_reflections / config_versions.

Tenant scoping is enforced at the project level — sleep_runs always
carries the project's ``user_id`` and the queries check that the
project belongs to the caller before the read.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from aether_api.models.config_version import ConfigVersion
from aether_api.models.sleep_reflection import SleepReflection
from aether_api.models.sleep_run import SleepRun


class SleepRunRepository:
    """Reads + writes against ``sleep_runs``."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        *,
        project_id: uuid.UUID,
        user_id: uuid.UUID,
        phase_type: str,
        status: str = "running",
    ) -> SleepRun:
        run = SleepRun(
            pair_id=project_id,
            user_id=user_id,
            phase_type=phase_type,
            status=status,
        )
        self.session.add(run)
        await self.session.flush()
        await self.session.refresh(run)
        return run

    async def finalize(
        self,
        *,
        run_id: uuid.UUID,
        status: str,
        summary: str | None = None,
        error: str | None = None,
    ) -> SleepRun | None:
        stmt = (
            update(SleepRun)
            .where(SleepRun.id == run_id)
            .values(
                status=status,
                summary=summary,
                error=error,
                ended_at=datetime.now(tz=UTC).replace(tzinfo=None),
            )
            .returning(SleepRun.id)
        )
        result = await self.session.execute(stmt)
        if result.scalar_one_or_none() is None:
            return None
        await self.session.flush()
        return await self.get(run_id)

    async def get(self, run_id: uuid.UUID) -> SleepRun | None:
        result = await self.session.execute(
            select(SleepRun).where(SleepRun.id == run_id)
        )
        return result.scalar_one_or_none()

    async def list_for_project(
        self,
        project_id: uuid.UUID,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> list[SleepRun]:
        result = await self.session.execute(
            select(SleepRun)
            .where(SleepRun.pair_id == project_id)
            .order_by(SleepRun.started_at.desc(), SleepRun.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars().all())

    async def mark_stale_running_as_crashed(
        self, *, stale_minutes: int
    ) -> list[SleepRun]:
        """Boot-sweep: rows still 'running' older than ``stale_minutes``.

        Returns the affected rows so the caller can also restore the
        project status + write audit entries.
        """
        cutoff = datetime.now(tz=UTC).replace(tzinfo=None) - timedelta(
            minutes=stale_minutes
        )
        # Find the rows first (so we can return them) THEN UPDATE.
        result = await self.session.execute(
            select(SleepRun)
            .where(SleepRun.status == "running")
            .where(SleepRun.started_at < cutoff)
        )
        rows = list(result.scalars().all())
        if not rows:
            return []
        await self.session.execute(
            update(SleepRun)
            .where(SleepRun.id.in_([row.id for row in rows]))
            .values(
                status="crashed",
                ended_at=datetime.now(tz=UTC).replace(tzinfo=None),
                error="boot sweep: run still 'running' beyond stale window",
            )
        )
        await self.session.flush()
        return rows


class SleepReflectionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def upsert(
        self,
        *,
        sleep_run_id: uuid.UUID,
        agent_type: str,
        reflection_md: str | None,
        suggested_changes: dict[str, Any],
    ) -> SleepReflection:
        # Try insert; on the unique violation update the row in-place. The
        # write surface is small (~3 per run) so an explicit
        # check-then-insert is fine.
        existing_q = await self.session.execute(
            select(SleepReflection)
            .where(SleepReflection.sleep_run_id == sleep_run_id)
            .where(SleepReflection.agent_type == agent_type)
        )
        existing = existing_q.scalar_one_or_none()
        if existing is not None:
            existing.reflection_md = reflection_md
            existing.suggested_changes = suggested_changes
            await self.session.flush()
            return existing

        row = SleepReflection(
            sleep_run_id=sleep_run_id,
            agent_type=agent_type,
            reflection_md=reflection_md,
            suggested_changes=suggested_changes,
        )
        self.session.add(row)
        await self.session.flush()
        await self.session.refresh(row)
        return row

    async def list_for_run(
        self, sleep_run_id: uuid.UUID
    ) -> list[SleepReflection]:
        result = await self.session.execute(
            select(SleepReflection)
            .where(SleepReflection.sleep_run_id == sleep_run_id)
            .order_by(SleepReflection.created_at.asc())
        )
        return list(result.scalars().all())


class ConfigVersionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        *,
        project_id: uuid.UUID,
        snapshot: dict[str, Any],
        risk_class: str,
        status: str,
        sleep_run_id: uuid.UUID | None = None,
        parent_version_id: uuid.UUID | None = None,
    ) -> ConfigVersion:
        row = ConfigVersion(
            pair_id=project_id,
            snapshot=snapshot,
            risk_class=risk_class,
            status=status,
            sleep_run_id=sleep_run_id,
            parent_version_id=parent_version_id,
        )
        self.session.add(row)
        await self.session.flush()
        await self.session.refresh(row)
        return row

    async def get(self, version_id: uuid.UUID) -> ConfigVersion | None:
        result = await self.session.execute(
            select(ConfigVersion).where(ConfigVersion.id == version_id)
        )
        return result.scalar_one_or_none()

    async def latest_applied_for_project(
        self, project_id: uuid.UUID
    ) -> ConfigVersion | None:
        result = await self.session.execute(
            select(ConfigVersion)
            .where(ConfigVersion.pair_id == project_id)
            .where(ConfigVersion.status == "applied")
            .order_by(ConfigVersion.applied_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def list_for_project(
        self,
        project_id: uuid.UUID,
        *,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[ConfigVersion]:
        stmt = (
            select(ConfigVersion)
            .where(ConfigVersion.pair_id == project_id)
            .order_by(ConfigVersion.proposed_at.desc(), ConfigVersion.id.desc())
        )
        if status is not None:
            stmt = stmt.where(ConfigVersion.status == status)
        stmt = stmt.limit(limit).offset(offset)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def list_for_run(
        self, sleep_run_id: uuid.UUID
    ) -> list[ConfigVersion]:
        result = await self.session.execute(
            select(ConfigVersion)
            .where(ConfigVersion.sleep_run_id == sleep_run_id)
            .order_by(ConfigVersion.proposed_at.desc())
        )
        return list(result.scalars().all())

    async def update_status(
        self,
        *,
        version_id: uuid.UUID,
        status: str,
        decided_by: uuid.UUID | None = None,
        applied_at: datetime | None = None,
    ) -> ConfigVersion | None:
        values: dict[str, Any] = {"status": status}
        # ``decided_by`` / ``decided_at`` are stamped for any operator-driven
        # transition (approve / reject / applied via approve / revert). The
        # applier passes ``decided_by`` even when status is ``'applied'`` so
        # the audit trail records "who clicked Approve".
        if decided_by is not None:
            values["decided_by"] = decided_by
            values["decided_at"] = datetime.now(tz=UTC).replace(tzinfo=None)
        if applied_at is not None:
            values["applied_at"] = applied_at
        stmt = (
            update(ConfigVersion)
            .where(ConfigVersion.id == version_id)
            .values(**values)
            .returning(ConfigVersion.id)
        )
        result = await self.session.execute(stmt)
        if result.scalar_one_or_none() is None:
            return None
        await self.session.flush()
        return await self.get(version_id)


__all__ = [
    "ConfigVersionRepository",
    "SleepReflectionRepository",
    "SleepRunRepository",
]
