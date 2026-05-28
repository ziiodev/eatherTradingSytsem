"""Boot-time recovery for crashed Sleep Phase runs.

Mounted in the FastAPI lifespan. Two responsibilities:

1. Find every ``sleep_runs`` row still in status ``running`` whose
   ``started_at`` is older than ``SLEEP_STALE_RUN_MINUTES`` and mark
   them ``crashed``.
2. For each such run, restore the underlying project's status from
   ``maintenance`` back to ``active`` (best-effort — concurrent
   operator moves win).

Idempotent: safe to call on every boot. A run that's still legitimately
running (a process actually executing) is protected by the
stale-window threshold — keep it generous (≥ 5× expected runtime).
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from aether_api.core.settings import get_settings
from aether_api.repositories.project_repository import ProjectRepository
from aether_api.sleep.repositories import SleepRunRepository

logger = logging.getLogger(__name__)


async def recover_stale_runs(session: AsyncSession) -> int:
    """Mark stale running rows as crashed; restore project status.

    Returns the number of rows transitioned (0 when there was nothing
    to do). Always commits before returning.
    """
    settings = get_settings()
    run_repo = SleepRunRepository(session)
    proj_repo = ProjectRepository(session)

    stale = await run_repo.mark_stale_running_as_crashed(
        stale_minutes=settings.sleep_stale_run_minutes
    )
    if not stale:
        await session.commit()
        return 0

    for run in stale:
        # Best-effort restore. If the project is already in some other
        # state, we don't fight the operator.
        await proj_repo.update_status_if(
            run.user_id,
            run.project_id,
            from_status="maintenance",
            to_status="active",
        )
        logger.warning(
            "sleep.boot_sweep: marked sleep_run %s crashed (project %s)",
            run.id,
            run.project_id,
        )

    await session.commit()
    return len(stale)


__all__ = ["recover_stale_runs"]
