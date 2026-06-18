"""APScheduler glue — Micro / Profundo per-project jobs.

The scheduler is built lazily in the FastAPI lifespan and ONLY started
when ``settings.sleep_scheduler_enabled`` is True. When the flag is
False (v1 default) we still expose the manual trigger endpoint via
:mod:`aether_api.sleep.routes` so operators can drive the workflow
without the recurring schedule.

Storage: SQLAlchemy jobstore on the same Postgres instance. Restarts
recover scheduled jobs without re-registration, but on every boot we
re-walk the projects table and (re-)register any missing jobs — that's
the path that picks up newly-created projects.

Crítico is NOT scheduled here. The Auditor publishes an in-process
signal that a dedicated trigger endpoint
(:func:`run_sleep_phase` with ``phase_type='critico'``) consumes.
Keeping it event-driven matches the charter's "activated automatically
by the Auditor" wording.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Iterable

from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore  # type: ignore[import-untyped]
from apscheduler.schedulers.asyncio import AsyncIOScheduler  # type: ignore[import-untyped]
from apscheduler.triggers.cron import CronTrigger  # type: ignore[import-untyped]
from apscheduler.triggers.interval import IntervalTrigger  # type: ignore[import-untyped]
from sqlalchemy.ext.asyncio import AsyncSession

from aether_api.core.settings import get_settings
from aether_api.db.session import get_session_maker
from aether_api.models.pair import Pair
from aether_api.sleep.orchestrator import run_sleep_phase

logger = logging.getLogger(__name__)


def _build_scheduler() -> AsyncIOScheduler:
    """Build an APScheduler bound to the same DB as the API."""
    settings = get_settings()
    # APScheduler wants a sync SQLAlchemy URL. Translate the async URL
    # back to psycopg form so its jobstore can use a sync driver.
    db_url = str(settings.database_url)
    if db_url.startswith("postgresql+asyncpg://"):
        sync_url = "postgresql+psycopg://" + db_url[len("postgresql+asyncpg://") :]
    else:
        sync_url = db_url
    jobstore = SQLAlchemyJobStore(url=sync_url, tablename="apscheduler_jobs")
    return AsyncIOScheduler(jobstores={"default": jobstore})


async def _run_scheduled_phase(
    project_id: str, user_id: str, phase_type: str
) -> None:
    """Job entrypoint — owns a fresh AsyncSession for this run."""
    session_maker = get_session_maker()
    async with session_maker() as session:
        try:
            await run_sleep_phase(
                session,
                project_id=uuid.UUID(project_id),
                user_id=uuid.UUID(user_id),
                phase_type=phase_type,
            )
        except Exception:
            logger.exception(
                "sleep.scheduler: scheduled %s for project %s raised",
                phase_type,
                project_id,
            )


async def _active_pairs(session: AsyncSession) -> Iterable[Pair]:
    from sqlalchemy import select

    result = await session.execute(
        select(Pair).where(Pair.status.in_(["active", "maintenance"]))
    )
    return list(result.scalars().all())


def _job_id(pair_id: uuid.UUID, phase_type: str) -> str:
    return f"sleep:{phase_type}:{pair_id}"


async def register_jobs_for_active_projects(
    scheduler: AsyncIOScheduler, session: AsyncSession
) -> int:
    """Walk projects and register Micro + Profundo jobs for each.

    Returns the number of jobs added (idempotent — replaces existing
    ones, so it can run on every boot).
    """
    settings = get_settings()
    n = 0
    for pair in await _active_pairs(session):
        # Micro — IntervalTrigger.
        micro_id = _job_id(pair.id, "micro")
        scheduler.add_job(
            _run_scheduled_phase,
            trigger=IntervalTrigger(hours=settings.sleep_micro_default_hours),
            id=micro_id,
            args=[str(pair.id), str(pair.user_id), "micro"],
            replace_existing=True,
            misfire_grace_time=60 * 30,  # 30 min — a missed Micro is fine to catch up.
        )
        n += 1
        # Profundo — CronTrigger (default 00:00 UTC).
        profundo_id = _job_id(pair.id, "profundo")
        scheduler.add_job(
            _run_scheduled_phase,
            trigger=CronTrigger.from_crontab(
                settings.sleep_profundo_cron, timezone="UTC"
            ),
            id=profundo_id,
            args=[str(pair.id), str(pair.user_id), "profundo"],
            replace_existing=True,
            misfire_grace_time=60 * 60,  # 1h — Profundo runs in a wide window.
        )
        n += 1
    return n


async def start_scheduler(scheduler: AsyncIOScheduler) -> None:
    """Start the scheduler + register jobs.

    Idempotent — calling twice is harmless because ``replace_existing``
    upserts the rows.
    """
    settings = get_settings()
    if not settings.sleep_scheduler_enabled:
        logger.info(
            "sleep.scheduler: disabled (AETHER_SLEEP_SCHEDULER_ENABLED=false)"
        )
        return

    scheduler.start(paused=False)
    session_maker = get_session_maker()
    async with session_maker() as session:
        n = await register_jobs_for_active_projects(scheduler, session)
    logger.info(
        "sleep.scheduler: started; registered %d project jobs", n
    )


async def shutdown_scheduler(scheduler: AsyncIOScheduler) -> None:
    """Stop the scheduler. Tolerates "already shut down".

    APScheduler raises on shutdown of an unstarted scheduler; the whole
    point of this call is "make sure it's not running", so swallowing
    is correct.
    """
    import contextlib

    with contextlib.suppress(Exception):
        scheduler.shutdown(wait=False)


__all__ = [
    "_build_scheduler",
    "register_jobs_for_active_projects",
    "shutdown_scheduler",
    "start_scheduler",
]
