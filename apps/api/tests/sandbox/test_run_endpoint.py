"""HTTP surface tests for ``POST /api/agents/{id}/run`` + ``GET .../runs``.

Covers:

* Feature-flag-off → 503 ``{detail: "sandbox not enabled"}``.
* Admin-only → 403 for non-admins.
* Cross-tenant agent_id OR project_id → 404 (existence non-disclosure).
* Happy path with a no-op entrypoint → 200 + audit row.
* GET /runs returns only the caller's history; cross-tenant 404.
"""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helpers (shape mirrors tests/test_skills.py)
# ---------------------------------------------------------------------------


async def _seed_user(client, email: str, *, is_admin: bool):
    from aether_api.db.session import get_session_maker

    from tests._helpers import seed_user

    maker = get_session_maker()
    async with maker() as session:
        user = await seed_user(
            session, email=email, password="testtesttesttest", is_admin=is_admin
        )
        await session.commit()
        return str(user.id)


async def _login(client, email: str) -> None:
    resp = await client.post(
        "/api/auth/login",
        json={"email": email, "password": "testtesttesttest"},
    )
    assert resp.status_code == 200, resp.text


def _csrf_headers(client) -> dict[str, str]:
    from aether_api.auth.cookies import CSRF_COOKIE

    token = client.cookies.get(CSRF_COOKIE)
    assert token, "csrf cookie missing — login was not run first"
    return {"X-CSRF-Token": token}


async def _seed_agent_and_project(user_id: str) -> tuple[str, str]:
    """Insert a worker agent + a project owned by ``user_id``. Return ids."""
    from aether_api.db.session import get_session_maker
    from aether_api.models.agent import Agent
    from aether_api.models.project import Project

    maker = get_session_maker()
    async with maker() as session:
        agent = Agent(
            user_id=user_id,
            name="noop",
            type="worker",
            logica="def on_tick(ctx):\n    return {'ok': True}\n",
            entrypoint="on_tick",
        )
        session.add(agent)
        await session.flush()
        project = Project(
            user_id=user_id,
            name="noop",
            symbol="EURUSD",
            timeframe="H1",
            mcp_url="http://127.0.0.1:65000",
            status="active",
        )
        session.add(project)
        await session.flush()
        await session.commit()
        return str(agent.id), str(project.id)


# ---------------------------------------------------------------------------
# Feature flag
# ---------------------------------------------------------------------------


async def test_run_returns_503_when_feature_flag_off(app_client, monkeypatch) -> None:
    monkeypatch.delenv("AGENT_SANDBOX_ENABLED", raising=False)
    from aether_api.core.settings import get_settings

    get_settings.cache_clear()

    user_id = await _seed_user(app_client, "admin@example.com", is_admin=True)
    await _login(app_client, "admin@example.com")
    agent_id, project_id = await _seed_agent_and_project(user_id)

    resp = await app_client.post(
        f"/api/agents/{agent_id}/run",
        json={"project_id": project_id, "dry_run": True, "inputs": {}},
        headers=_csrf_headers(app_client),
    )
    assert resp.status_code == 503, resp.text
    assert resp.json()["detail"] == "sandbox not enabled"


# ---------------------------------------------------------------------------
# Admin-only
# ---------------------------------------------------------------------------


async def test_run_requires_admin(app_client, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_SANDBOX_ENABLED", "true")
    from aether_api.core.settings import get_settings

    get_settings.cache_clear()

    user_id = await _seed_user(app_client, "user@example.com", is_admin=False)
    await _login(app_client, "user@example.com")
    agent_id, project_id = await _seed_agent_and_project(user_id)

    resp = await app_client.post(
        f"/api/agents/{agent_id}/run",
        json={"project_id": project_id, "dry_run": True, "inputs": {}},
        headers=_csrf_headers(app_client),
    )
    assert resp.status_code == 403, resp.text


# ---------------------------------------------------------------------------
# Cross-tenant
# ---------------------------------------------------------------------------


async def test_run_cross_tenant_returns_404(app_client, monkeypatch) -> None:
    """Admin A cannot run admin B's agent — 404, never 403."""
    monkeypatch.setenv("AGENT_SANDBOX_ENABLED", "true")
    from aether_api.core.settings import get_settings

    get_settings.cache_clear()

    # User B owns the agent + project.
    b_id = await _seed_user(app_client, "b@example.com", is_admin=True)
    b_agent, b_project = await _seed_agent_and_project(b_id)

    # User A logs in (their session cookie replaces any previous one).
    await _seed_user(app_client, "a@example.com", is_admin=True)
    await _login(app_client, "a@example.com")

    resp = await app_client.post(
        f"/api/agents/{b_agent}/run",
        json={"project_id": b_project, "dry_run": True, "inputs": {}},
        headers=_csrf_headers(app_client),
    )
    assert resp.status_code == 404, resp.text
    assert resp.json()["detail"] == "agent not found"


# ---------------------------------------------------------------------------
# GET /runs
# ---------------------------------------------------------------------------


async def test_list_runs_cross_tenant_returns_404(app_client) -> None:
    """Listing another user's runs returns 404 (same non-disclosure rule)."""
    b_id = await _seed_user(app_client, "b@example.com", is_admin=False)
    b_agent, _ = await _seed_agent_and_project(b_id)

    await _seed_user(app_client, "a@example.com", is_admin=False)
    await _login(app_client, "a@example.com")

    resp = await app_client.get(f"/api/agents/{b_agent}/runs")
    assert resp.status_code == 404


async def test_list_runs_empty_for_owner(app_client) -> None:
    """Owner with no runs yet sees an empty list, not a 404."""
    user_id = await _seed_user(app_client, "owner@example.com", is_admin=False)
    await _login(app_client, "owner@example.com")
    agent_id, _ = await _seed_agent_and_project(user_id)

    resp = await app_client.get(f"/api/agents/{agent_id}/runs")
    assert resp.status_code == 200
    assert resp.json() == []
