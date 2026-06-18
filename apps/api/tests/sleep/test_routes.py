"""End-to-end HTTP coverage for the Sleep Phase routes."""

from __future__ import annotations

import os
import uuid
from decimal import Decimal

import pytest

pytestmark = pytest.mark.integration


# Force sandbox flag ON for the trigger test (orchestrator otherwise short-fails).
os.environ.setdefault("AGENT_SANDBOX_ENABLED", "true")


async def _login(client, *, email: str | None = None):
    from aether_api.core.settings import get_settings
    from aether_api.db.session import get_session_maker
    from tests._helpers import seed_user

    get_settings.cache_clear()
    email = email or f"sleep-{uuid.uuid4().hex[:8]}@example.com"
    password = "correct horse battery staple"
    maker = get_session_maker()
    async with maker() as session:
        user = await seed_user(session, email=email, password=password)
        await session.commit()
    resp = await client.post(
        "/api/auth/login", json={"email": email, "password": password}
    )
    assert resp.status_code == 200, resp.text
    return user


def _csrf_headers(client) -> dict[str, str]:
    from aether_api.auth.cookies import CSRF_COOKIE

    token = client.cookies.get(CSRF_COOKIE)
    assert token
    return {"X-CSRF-Token": token}


async def _seed_project_and_agents(user):
    from aether_api.db.session import get_session_maker
    from tests._helpers import seed_agent, seed_project

    maker = get_session_maker()
    async with maker() as session:
        project = await seed_project(session, owner=user)
        project.status = "active"
        project.worker_params = {"sma_window": 30}
        w = await seed_agent(session, owner=user, name="w", type="worker")
        i = await seed_agent(session, owner=user, name="i", type="investigator")
        a = await seed_agent(session, owner=user, name="a", type="auditor")
        project.worker_agent_id = w.id
        project.investigator_agent_id = i.id
        project.auditor_agent_id = a.id
        await session.commit()
        await session.refresh(project)
    return project


async def test_list_runs_requires_auth(app_client) -> None:
    resp = await app_client.get(f"/api/pairs/{uuid.uuid4()}/sleep/runs")
    assert resp.status_code == 401


async def test_list_runs_cross_tenant_is_404(app_client) -> None:
    user_a = await _login(app_client, email="a@example.com")
    _ = await _seed_project_and_agents(user_a)

    # Log out, log in as user B and try to read user A's project runs.
    app_client.cookies.clear()
    user_b = await _login(app_client, email="b@example.com")
    proj_a = await _seed_project_and_agents(user_a)
    resp = await app_client.get(f"/api/pairs/{proj_a.id}/sleep/runs")
    # 404 because B does not own the project; we never reveal 403.
    assert resp.status_code == 404
    assert user_b is not None  # silence unused


async def test_trigger_unknown_phase_returns_400(app_client) -> None:
    from aether_api.core.settings import get_settings

    os.environ["AGENT_SANDBOX_ENABLED"] = "true"
    get_settings.cache_clear()

    user = await _login(app_client)
    project = await _seed_project_and_agents(user)
    resp = await app_client.post(
        f"/api/pairs/{project.id}/sleep/trigger",
        json={"phase_type": "siesta"},
        headers=_csrf_headers(app_client),
    )
    assert resp.status_code == 400


async def test_config_versions_approve_requires_csrf(app_client) -> None:
    from aether_api.db.session import get_session_maker
    from aether_api.sleep.repositories import ConfigVersionRepository
    from tests._helpers import seed_project

    user = await _login(app_client)
    maker = get_session_maker()
    async with maker() as session:
        proj = await seed_project(session, owner=user)
        cv = await ConfigVersionRepository(session).create(
            project_id=proj.id,
            snapshot={"notes": "x"},
            risk_class="bajo",
            status="pending",
        )
        await session.commit()
        cv_id = cv.id

    # No CSRF header → 403.
    resp = await app_client.post(f"/api/config-versions/{cv_id}/approve")
    assert resp.status_code == 403


async def test_config_versions_approve_happy_path(app_client) -> None:
    from aether_api.db.session import get_session_maker
    from aether_api.sleep.repositories import ConfigVersionRepository
    from tests._helpers import seed_project

    user = await _login(app_client)
    maker = get_session_maker()
    async with maker() as session:
        proj = await seed_project(session, owner=user)
        proj.notes = "old"
        await session.flush()
        cv = await ConfigVersionRepository(session).create(
            project_id=proj.id,
            snapshot={"notes": "approved-snapshot"},
            risk_class="bajo",
            status="pending",
        )
        await session.commit()
        cv_id = cv.id

    resp = await app_client.post(
        f"/api/config-versions/{cv_id}/approve",
        headers=_csrf_headers(app_client),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "applied"
    assert body["snapshot"]["notes"] == "approved-snapshot"


async def test_revert_window_enforced(app_client) -> None:
    from datetime import datetime, timedelta, timezone

    from aether_api.core.settings import get_settings
    from aether_api.db.session import get_session_maker
    from aether_api.models.config_version import ConfigVersion
    from sqlalchemy import update
    from tests._helpers import seed_project

    get_settings.cache_clear()
    settings = get_settings()
    window = settings.sleep_revert_window_hours

    user = await _login(app_client)
    maker = get_session_maker()
    async with maker() as session:
        proj = await seed_project(session, owner=user)
        # Insert an applied row stamped well outside the revert window.
        very_old = datetime.now(tz=timezone.utc).replace(tzinfo=None) - timedelta(
            hours=window + 5
        )
        cv = ConfigVersion(
            pair_id=proj.id,
            snapshot={"notes": "old"},
            risk_class="bajo",
            status="applied",
            applied_at=very_old,
        )
        session.add(cv)
        await session.flush()
        # Force the parent FK to itself (silly but legal — exercises the window).
        await session.execute(
            update(ConfigVersion).where(ConfigVersion.id == cv.id).values(
                parent_version_id=cv.id
            )
        )
        await session.commit()
        cv_id = cv.id

    resp = await app_client.post(
        f"/api/config-versions/{cv_id}/revert",
        headers=_csrf_headers(app_client),
    )
    assert resp.status_code == 409
    assert "revert window" in resp.json()["detail"]


async def test_trigger_returns_200_with_failed_when_no_agents(app_client) -> None:
    from aether_api.core.settings import get_settings
    from aether_api.db.session import get_session_maker
    from tests._helpers import seed_project

    os.environ["AGENT_SANDBOX_ENABLED"] = "true"
    get_settings.cache_clear()

    user = await _login(app_client)
    maker = get_session_maker()
    async with maker() as session:
        proj = await seed_project(session, owner=user)
        proj.status = "active"
        proj.risk_per_trade = Decimal("1.0")
        await session.commit()
        pid = proj.id

    resp = await app_client.post(
        f"/api/pairs/{pid}/sleep/trigger",
        json={"phase_type": "micro"},
        headers=_csrf_headers(app_client),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # No agents assigned → orchestrator returns failed with descriptive error.
    assert body["status"] == "failed"
    assert "no agents" in (body.get("error") or "")
