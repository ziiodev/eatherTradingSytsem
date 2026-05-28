"""Integration test for the FastAPI lifespan warm-caches hook (Phase 4).

The lifespan hook (in :mod:`aether_api.main`) must:

1. Enumerate every project whose ``status IN ('active', 'paused')``.
2. Warm the in-process :class:`LearningCache` for each one.
3. For every project whose warm raised, flip its ``status`` to
   ``maintenance`` and log WARN.
4. Reach READY regardless of warm failures — recovery is best-effort.

This test spins TWO projects in the migrated DB:

* one healthy → must end up in the cache, status preserved;
* one whose Q-Table fetch is monkey-patched to raise → must NOT end up
  in the cache and must have ``status = 'maintenance'`` after startup.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

pytestmark = pytest.mark.integration


async def _seed_two_projects() -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """Seed user + 2 active projects. Returns (user_id, healthy_pid, failing_pid)."""
    from aether_api.db.session import get_session_maker

    from tests._helpers import seed_project, seed_user

    maker = get_session_maker()
    async with maker() as session:
        user = await seed_user(
            session,
            email=f"lifespan-warm-{uuid.uuid4().hex[:8]}@example.com",
            password="correct horse battery staple",
        )
        proj_healthy = await seed_project(
            session, owner=user, name=f"healthy-{uuid.uuid4().hex[:8]}"
        )
        proj_failing = await seed_project(
            session, owner=user, name=f"failing-{uuid.uuid4().hex[:8]}"
        )
        await session.commit()
        return user.id, proj_healthy.id, proj_failing.id


async def test_lifespan_warm_healthy_and_failing_projects(
    migrated_db: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One project warms cleanly, the other gets flipped to maintenance.

    We do NOT use the ``app_client`` fixture because that boots the
    lifespan BEFORE we get a chance to seed projects. Instead we seed
    first, install the failure monkeypatch, then boot the lifespan
    manually via asgi-lifespan.
    """
    import httpx
    from aether_api.core.settings import get_settings
    from aether_api.db.session import get_session_maker
    from aether_api.main import create_app
    from aether_api.repositories.project_repository import ProjectRepository
    from aether_api.repositories.q_table_repository import QTableRepository
    from asgi_lifespan import LifespanManager

    user_id, healthy_pid, failing_pid = await _seed_two_projects()

    # Inject the failure BEFORE the lifespan runs.
    real_get_latest = QTableRepository.get_latest

    async def selective_get_latest(
        self: Any, *, user_id: uuid.UUID, project_id: uuid.UUID
    ) -> Any:
        if project_id == failing_pid:
            raise RuntimeError(
                f"injected boot-time failure for project {project_id}"
            )
        return await real_get_latest(
            self, user_id=user_id, project_id=project_id
        )

    monkeypatch.setattr(QTableRepository, "get_latest", selective_get_latest)

    get_settings.cache_clear()
    app = create_app()

    # Drive lifespan + a trivial request through the app.
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            health = await client.get("/healthz")
            assert health.status_code == 200, health.text

        # ------------ Cache assertions ----------------------------
        cache = app.state.learning_cache
        # Healthy project: cache populated.
        healthy_entry = cache.get(user_id, healthy_pid)
        assert (
            healthy_entry is not None
        ), "healthy project must be warmed into the cache"
        # Failing project: cache NOT populated.
        failing_entry = cache.get(user_id, failing_pid)
        assert (
            failing_entry is None
        ), "failing project must NOT be in the cache"

        # ------------ DB assertions -------------------------------
        maker = get_session_maker()
        async with maker() as session:
            repo = ProjectRepository(session)
            healthy_proj = await repo.get_for_user(user_id, healthy_pid)
            failing_proj = await repo.get_for_user(user_id, failing_pid)
            assert healthy_proj is not None
            assert failing_proj is not None
            # Healthy status preserved.
            assert healthy_proj.status == "active"
            # Failing project flipped.
            assert failing_proj.status == "maintenance"
