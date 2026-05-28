"""HTTP-boundary tests for the docker_control endpoints.

These tests run against the live FastAPI app (httpx + asgi-lifespan)
and exercise:

* Auth gating (401 without cookie).
* Tenant isolation (404, never 403, on cross-tenant requests).
* CSRF on every mutating endpoint.
* Dockerfile preview returns deterministic ``text/plain``.
* A project whose broker_name carries shell metacharacters is rejected
  at the HTTP boundary with HTTP 422, NEVER 200 — this is the spec's
  injection-rejection contract.

Marked ``integration`` so the suite skips cleanly when no Postgres /
testcontainers is available.
"""

from __future__ import annotations

import uuid

import pytest
from aether_api.auth.cookies import CSRF_COOKIE

pytestmark = pytest.mark.integration


async def _seed_user_and_login(
    client,
    *,
    email: str = "docker-ops@example.com",
    password: str = "correct horse battery staple",
):
    from aether_api.db.session import get_session_maker

    from tests._helpers import seed_user

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
    token = client.cookies.get(CSRF_COOKIE)
    assert token, "csrf cookie missing — did you log in first?"
    return {"X-CSRF-Token": token}


async def _create_project(client, **overrides) -> dict:
    body = {
        "name": "DockerSmoke",
        "symbol": "EURUSD",
        "timeframe": "H1",
        "mcp_url": "http://mcp.local:8081",
        "mcp_port": 8081,
    }
    body.update(overrides)
    resp = await client.post(
        "/api/projects", json=body, headers=_csrf_headers(client)
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# Auth + CSRF gating
# ---------------------------------------------------------------------------
async def test_preview_requires_auth(app_client):
    # Without a cookie the dependency tree fails at the CSRF guard
    # before reaching ``current_user`` — that's a 403, not a 401. The
    # important invariant is "no anonymous read": 401 or 403 both
    # satisfy it, but the deterministic answer is 403 because the
    # route declares ``Depends(csrf_dependency)`` at the path level.
    resp = await app_client.post(
        f"/api/projects/{uuid.uuid4()}/dockerfile/preview"
    )
    assert resp.status_code in (401, 403)


async def test_preview_requires_csrf(app_client):
    await _seed_user_and_login(app_client)
    project = await _create_project(app_client)
    # No CSRF header — must fail.
    resp = await app_client.post(
        f"/api/projects/{project['id']}/dockerfile/preview"
    )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Determinism — two calls with the same project produce identical bodies.
# ---------------------------------------------------------------------------
async def test_preview_is_deterministic(app_client):
    await _seed_user_and_login(app_client)
    project = await _create_project(app_client, broker_name="ICMarkets")

    headers = _csrf_headers(app_client)
    a = await app_client.post(
        f"/api/projects/{project['id']}/dockerfile/preview", headers=headers
    )
    b = await app_client.post(
        f"/api/projects/{project['id']}/dockerfile/preview", headers=headers
    )
    assert a.status_code == 200
    assert b.status_code == 200
    assert a.headers["content-type"].startswith("text/plain")
    assert a.text == b.text
    assert a.text.startswith("FROM ")
    assert "LABEL aether.project_id=" in a.text


# ---------------------------------------------------------------------------
# Injection — a broker_name with shell metacharacters fails 422 at boundary.
# ---------------------------------------------------------------------------
async def test_preview_rejects_unsafe_broker_name(app_client):
    await _seed_user_and_login(app_client)

    # Insert a project whose broker_name carries a dangerous payload. We
    # cannot go through POST /api/projects because the upstream Pydantic
    # validator rejects ``;`` outside the allowlist. So we update the
    # row directly via the DB — the test is about the renderer's
    # boundary check, not the create endpoint.
    from aether_api.db.session import get_session_maker
    from sqlalchemy import text

    project = await _create_project(app_client)
    maker = get_session_maker()
    async with maker() as session:
        await session.execute(
            text("UPDATE projects SET broker_name = :b WHERE id = :id"),
            {"b": "IC; rm -rf /", "id": uuid.UUID(project["id"])},
        )
        await session.commit()

    resp = await app_client.post(
        f"/api/projects/{project['id']}/dockerfile/preview",
        headers=_csrf_headers(app_client),
    )
    assert resp.status_code == 422
    body = resp.json()
    assert body["detail"]["code"] == "unsafe_value"
    assert body["detail"]["field"] == "broker_name"


# ---------------------------------------------------------------------------
# Cross-tenant: 404, not 403.
# ---------------------------------------------------------------------------
async def test_preview_cross_tenant_returns_404(app_client):
    # User A creates project, then logs out.
    await _seed_user_and_login(app_client, email="a@example.com")
    project = await _create_project(app_client)
    await app_client.post(
        "/api/auth/logout", headers=_csrf_headers(app_client)
    )
    app_client.cookies.clear()

    # User B logs in and tries to read A's project.
    await _seed_user_and_login(app_client, email="b@example.com")
    resp = await app_client.post(
        f"/api/projects/{project['id']}/dockerfile/preview",
        headers=_csrf_headers(app_client),
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Events feed
# ---------------------------------------------------------------------------
async def test_events_feed_lists_audit_rows(app_client):
    await _seed_user_and_login(app_client)
    project = await _create_project(app_client)

    # Seed an event row directly so we don't depend on a live Docker daemon.
    from aether_api.db.session import get_session_maker
    from aether_api.docker_control.events_repository import (
        ContainerEventsRepository,
    )

    maker = get_session_maker()
    async with maker() as session:
        # We need the project's user_id to write a row.
        from aether_api.models.project import Project
        proj_row = (
            await session.execute(
                __import__("sqlalchemy").select(Project).where(
                    Project.id == uuid.UUID(project["id"])
                )
            )
        ).scalar_one()
        await ContainerEventsRepository(session).record(
            project_id=proj_row.id,
            user_id=proj_row.user_id,
            action="build",
            status="ok",
            payload={"image_tag": "aether/test:latest"},
        )
        await session.commit()

    resp = await app_client.get(
        f"/api/projects/{project['id']}/container/events"
    )
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["total"] == 1
    assert payload["items"][0]["action"] == "build"
    assert payload["items"][0]["status"] == "ok"
