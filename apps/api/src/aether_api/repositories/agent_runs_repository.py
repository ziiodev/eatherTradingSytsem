"""``agent_runs`` data access — tenant-scoped, append-then-finalise.

This is the persistence side of the sandbox engine. The shape is unusual
for the codebase:

* :meth:`record_start` inserts a row with ``status='running'`` BEFORE the
  child is spawned, so the audit trail is durable even if the parent
  crashes mid-run.
* :meth:`record_finish` UPDATEs that same row with the terminal status,
  exit code, captured streams, and resource usage when the engine has a
  result.

We deliberately do NOT expose UPDATE/DELETE primitives beyond
``record_finish`` — the migration GRANTs INSERT/SELECT/UPDATE but no
DELETE, and the app layer keeps to a similarly narrow surface so a
malicious code path can't silently rewrite history.

The read side mirrors the rest of the repository layer (everything goes
through :meth:`_for_user`) so cross-tenant probes return ``None`` and the
router translates to HTTP 404, NOT 403.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, update

from aether_api.models.agent_run import AgentRun
from aether_api.repositories.base import BaseRepository


class AgentRunsRepository(BaseRepository):
    model = AgentRun

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------
    async def list_for_agent(
        self,
        user_id: uuid.UUID,
        agent_id: uuid.UUID,
        *,
        limit: int = 50,
    ) -> list[AgentRun]:
        """Return the caller's most recent runs of ``agent_id``, newest first.

        Cross-tenant agent_id leaks nothing — the ``user_id`` filter makes
        the result identical to "no such agent" for any non-owner.
        """
        stmt = (
            self._for_user(select(AgentRun), user_id)
            .where(AgentRun.agent_id == agent_id)
            .order_by(AgentRun.started_at.desc())
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_for_user(
        self, user_id: uuid.UUID, run_id: uuid.UUID
    ) -> AgentRun | None:
        """Same 404-not-403 contract as the rest of the repos."""
        stmt = self._for_user(select(AgentRun).where(AgentRun.id == run_id), user_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    # ------------------------------------------------------------------
    # Writes — narrow, append-then-finalise.
    # ------------------------------------------------------------------
    async def record_start(
        self,
        *,
        user_id: uuid.UUID,
        agent_id: uuid.UUID,
        project_id: uuid.UUID,
    ) -> AgentRun:
        """Insert ``status='running'`` row and return it.

        The engine flushes immediately so the row is durable BEFORE the
        child is spawned — a parent-side crash between flush and child
        startup leaves an observable "running" row the operator can grep
        for.
        """
        row = AgentRun(
            user_id=user_id,
            agent_id=agent_id,
            project_id=project_id,
            status="running",
        )
        self.session.add(row)
        await self.session.flush()
        await self.session.refresh(row)
        return row

    async def record_finish(
        self,
        run_id: uuid.UUID,
        *,
        status: str,
        exit_code: int | None,
        stdout: str | None,
        stderr: str | None,
        denial_reason: str | None,
        resource_usage: dict[str, Any],
    ) -> None:
        """Finalise the row created by :meth:`record_start`.

        The CHECK constraint ``agent_runs_running_no_ended`` enforces the
        terminal status / ``ended_at`` invariant, so we set both in one
        statement.
        """
        await self.session.execute(
            update(AgentRun)
            .where(AgentRun.id == run_id)
            .values(
                status=status,
                ended_at=datetime.now(tz=UTC).replace(tzinfo=None),
                exit_code=exit_code,
                stdout=stdout,
                stderr=stderr,
                denial_reason=denial_reason,
                resource_usage=resource_usage,
            )
        )
        await self.session.flush()
