"""Boot sweep recovers crashed sleep runs and restores project status."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

pytestmark = pytest.mark.integration


async def _seed_running_run(session, *, started_minutes_ago: int):
    from aether_api.models.sleep_run import SleepRun
    from tests._helpers import seed_project, seed_user

    user = await seed_user(
        session, email=f"sweep-{uuid.uuid4().hex[:8]}@example.com",
        password="correct horse battery staple",
    )
    project = await seed_project(session, owner=user)
    project.status = "maintenance"  # what an in-flight sleep leaves behind
    await session.flush()

    run = SleepRun(
        project_id=project.id,
        user_id=user.id,
        phase_type="micro",
        status="running",
        started_at=datetime.now(tz=timezone.utc).replace(tzinfo=None)
        - timedelta(minutes=started_minutes_ago),
    )
    session.add(run)
    await session.flush()
    await session.commit()
    return user, project, run


async def test_boot_sweep_marks_stale_runs_crashed(app_client) -> None:
    from aether_api.core.settings import get_settings
    from aether_api.db.session import get_session_maker
    from aether_api.repositories.project_repository import ProjectRepository
    from aether_api.sleep.boot_sweep import recover_stale_runs
    from aether_api.sleep.repositories import SleepRunRepository

    get_settings.cache_clear()
    settings = get_settings()
    stale_minutes = settings.sleep_stale_run_minutes

    maker = get_session_maker()
    async with maker() as session:
        user, project, run = await _seed_running_run(
            session, started_minutes_ago=stale_minutes + 5
        )
        run_id = run.id

    async with maker() as session:
        n = await recover_stale_runs(session)
    assert n == 1

    async with maker() as session:
        refreshed = await SleepRunRepository(session).get(run_id)
        assert refreshed is not None
        assert refreshed.status == "crashed"
        assert refreshed.ended_at is not None

        proj = await ProjectRepository(session).get_for_user(user.id, project.id)
        assert proj is not None
        # Status restored to 'active' (from 'maintenance').
        assert proj.status == "active"


async def test_boot_sweep_leaves_fresh_running_rows_alone(app_client) -> None:
    from aether_api.core.settings import get_settings
    from aether_api.db.session import get_session_maker
    from aether_api.sleep.boot_sweep import recover_stale_runs
    from aether_api.sleep.repositories import SleepRunRepository

    get_settings.cache_clear()

    maker = get_session_maker()
    async with maker() as session:
        user, project, run = await _seed_running_run(session, started_minutes_ago=1)
        run_id = run.id

    async with maker() as session:
        n = await recover_stale_runs(session)
    assert n == 0

    async with maker() as session:
        refreshed = await SleepRunRepository(session).get(run_id)
        assert refreshed is not None
        assert refreshed.status == "running"
