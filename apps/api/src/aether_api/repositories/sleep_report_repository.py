"""``sleep_reports`` data access — tenancy via ``sleep_runs.project_id`` → ``projects.user_id``.

There is exactly one ``sleep_reports`` row per ``sleep_runs.id`` (UNIQUE
FK, enforced at the DB level). Every method goes through a two-hop JOIN
(``sleep_reports`` → ``sleep_runs`` → ``projects``) so a cross-tenant
caller sees ``None`` / refused writes — the router maps that to a 404
to preserve the canonical existence-disclosure rule.

Field mapping for ``insert``:

* ``summary``               -> ``summary_md`` (operator markdown digest)
* ``auditor_metrics``       -> ``payload["auditor_metrics"]``
* ``worker_insights``       -> ``payload["worker_insights"]``
* ``improvements_applied``  -> ``payload["improvements_applied"]``
* ``q_table_before``        -> ``payload["q_table_before"]``
* ``q_table_after``         -> ``payload["q_table_after"]``
* ``overall_score``         -> ``payload["overall_score"]``
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select

from aether_api.models.project import Project
from aether_api.models.sleep_report import SleepReport
from aether_api.models.sleep_run import SleepRun
from aether_api.repositories.base import BaseRepository


class SleepReportRepository(BaseRepository):
    model = SleepReport

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    async def _user_owns_sleep_run(
        self, user_id: uuid.UUID, sleep_run_id: uuid.UUID
    ) -> bool:
        """Return True iff ``sleep_run_id`` belongs to a project owned by ``user_id``.

        Uses the explicit ``sleep_runs.user_id`` column when available
        (Phase 1 of sleep-phase stamped it), falling back to the
        ``projects.user_id`` JOIN — both paths agree by construction.
        We use the JOIN form to keep the invariant exclusively on
        ``projects``.
        """
        stmt = (
            select(SleepRun.id)
            .join(Project, Project.id == SleepRun.project_id)
            .where(SleepRun.id == sleep_run_id)
            .where(Project.user_id == user_id)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none() is not None

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------
    async def get_by_run_id(
        self, *, user_id: uuid.UUID, sleep_run_id: uuid.UUID
    ) -> SleepReport | None:
        """Return the report for ``sleep_run_id`` IFF the caller owns it."""
        stmt = (
            select(SleepReport)
            .join(SleepRun, SleepRun.id == SleepReport.sleep_run_id)
            .join(Project, Project.id == SleepRun.project_id)
            .where(Project.user_id == user_id)
            .where(SleepReport.sleep_run_id == sleep_run_id)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------
    async def insert(
        self,
        *,
        user_id: uuid.UUID,
        sleep_run_id: uuid.UUID,
        summary: str | None,
        auditor_metrics: dict[str, Any],
        worker_insights: dict[str, Any],
        improvements_applied: list[Any],
        q_table_before: dict[str, Any],
        q_table_after: dict[str, Any],
        overall_score: float,
    ) -> SleepReport:
        """Insert the (1:1) report row for ``sleep_run_id``.

        Refuses cross-tenant writes early with ``PermissionError`` so
        we never store a row whose ownership doesn't match the
        invariant.
        """
        if not await self._user_owns_sleep_run(user_id, sleep_run_id):
            raise PermissionError(
                f"user {user_id} does not own sleep_run {sleep_run_id}"
            )

        payload: dict[str, Any] = {
            "auditor_metrics": auditor_metrics,
            "worker_insights": worker_insights,
            "improvements_applied": improvements_applied,
            "q_table_before": q_table_before,
            "q_table_after": q_table_after,
            "overall_score": overall_score,
        }
        row = SleepReport(
            sleep_run_id=sleep_run_id,
            payload=payload,
            summary_md=summary,
        )
        self.session.add(row)
        await self.session.flush()
        await self.session.refresh(row)
        return row
