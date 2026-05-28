"""End-to-end coverage for /api/projects CRUD + lifecycle.

Scope:

* Auth gating (401 without cookie).
* Cross-tenant denial (404, never 403).
* CSRF on state-changing endpoints.
* CRUD happy paths + validation errors.
* Lifecycle state machine (every allowed edge + a few denied ones).
* Delete preconditions (deletable state + no container_id).

The tests use the shared :func:`app_client` fixture and the
:func:`tests._helpers.seed_user` / :func:`seed_project` helpers.

A note on the auth dance for state-changing requests:

* Cookies are persisted on the httpx client by ``app_client``.
* The CSRF cookie is non-httpOnly so it's readable via
  ``app_client.cookies.get(CSRF_COOKIE)`` — we mirror that into the
  ``X-CSRF-Token`` header on POST/PATCH/DELETE.
"""

from __future__ import annotations

import uuid

import pytest

from aether_api.auth.cookies import ACCESS_COOKIE, CSRF_COOKIE
from aether_api.services.project_lifecycle import (
    VALID_TRANSITIONS,
    can_transition,
)

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
async def _seed_user_and_login(
    client,
    *,
    email: str = "ops@example.com",
    password: str = "correct horse battery staple",
):
    """Insert ``email`` and log them in so subsequent requests are authenticated."""
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
    """Build a ``X-CSRF-Token`` header from the current cookie jar."""
    token = client.cookies.get(CSRF_COOKIE)
    assert token, "csrf cookie missing — did you log in first?"
    return {"X-CSRF-Token": token}


def _project_payload(**overrides):
    body = {
        "name": "Aether-EURUSD-H1",
        "description": "Smoke test project",
        "symbol": "EURUSD",
        "timeframe": "H1",
        "mcp_url": "http://mcp.local:8081",
        "trading_sessions": ["europe", "new_york"],
    }
    body.update(overrides)
    return body


async def _create_project(client, **overrides) -> dict:
    """POST a project and return the parsed JSON body."""
    resp = await client.post(
        "/api/projects",
        json=_project_payload(**overrides),
        headers=_csrf_headers(client),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _force_status(project_id: str, *, status: str) -> None:
    """Bypass the state machine for test scaffolding — flip the DB row directly.

    Used to put a project into a state that the state machine wouldn't let
    us reach via the HTTP surface (e.g. ``maintenance`` from ``inactive``
    is fine, but to test ``error -> stopped`` we want to skip the long
    walk).
    """
    from aether_api.db.session import get_session_maker
    from sqlalchemy import text

    maker = get_session_maker()
    async with maker() as session:
        await session.execute(
            text("UPDATE projects SET status = :s WHERE id = :id"),
            {"s": status, "id": uuid.UUID(project_id)},
        )
        await session.commit()


# ---------------------------------------------------------------------------
# Auth gating
# ---------------------------------------------------------------------------
async def test_list_requires_auth(app_client):
    resp = await app_client.get("/api/projects")
    assert resp.status_code == 401


async def test_create_requires_auth(app_client):
    resp = await app_client.post("/api/projects", json=_project_payload())
    # FastAPI may evaluate the CSRF dependency before resolving ``current_user``
    # (both live on the path-op's dependency list), so an unauthenticated POST
    # may surface as either 401 (no cookie) or 403 (no CSRF token). Either is
    # acceptable from the spec's POV — the important invariant is "no 2xx, no
    # row inserted". We accept both to keep the test robust against dependency
    # ordering changes.
    assert resp.status_code in (401, 403)


# ---------------------------------------------------------------------------
# CSRF
# ---------------------------------------------------------------------------
async def test_create_without_csrf_is_403(app_client):
    await _seed_user_and_login(app_client)
    resp = await app_client.post("/api/projects", json=_project_payload())
    assert resp.status_code == 403


async def test_patch_without_csrf_is_403(app_client):
    await _seed_user_and_login(app_client)
    created = await _create_project(app_client)
    resp = await app_client.patch(
        f"/api/projects/{created['id']}", json={"description": "new"}
    )
    assert resp.status_code == 403


async def test_lifecycle_without_csrf_is_403(app_client):
    await _seed_user_and_login(app_client)
    created = await _create_project(app_client)
    resp = await app_client.post(f"/api/projects/{created['id']}/activate")
    assert resp.status_code == 403


async def test_delete_without_csrf_is_403(app_client):
    await _seed_user_and_login(app_client)
    created = await _create_project(app_client)
    resp = await app_client.delete(f"/api/projects/{created['id']}")
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# CRUD happy paths
# ---------------------------------------------------------------------------
async def test_create_project_returns_inactive_with_defaults(app_client):
    await _seed_user_and_login(app_client)
    body = await _create_project(app_client)

    assert body["status"] == "inactive"
    assert body["symbol"] == "EURUSD"
    assert body["timeframe"] == "H1"
    # Charter defaults applied because the caller did not pass them.
    assert body["risk_per_trade"] == "1.0" or float(body["risk_per_trade"]) == 1.0
    assert float(body["max_daily_dd"]) == 3.0
    assert float(body["max_total_dd"]) == 8.0
    assert float(body["max_exposure"]) == 10.0
    assert body["trading_sessions"] == ["europe", "new_york"]


async def test_list_returns_paginated_payload(app_client):
    await _seed_user_and_login(app_client)
    for i in range(3):
        await _create_project(app_client, name=f"Proj-{i}", symbol="EURUSD")
    resp = await app_client.get("/api/projects")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 3
    assert body["limit"] == 50
    assert body["offset"] == 0
    assert len(body["items"]) == 3
    assert resp.headers["X-Total-Count"] == "3"


async def test_list_filters_by_status(app_client):
    await _seed_user_and_login(app_client)
    a = await _create_project(app_client, name="A", symbol="EURUSD")
    # Move A → active via lifecycle endpoint.
    await app_client.post(
        f"/api/projects/{a['id']}/activate", headers=_csrf_headers(app_client)
    )
    await _create_project(app_client, name="B", symbol="EURUSD")

    resp = await app_client.get("/api/projects?status=active")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["name"] == "A"


async def test_get_project_returns_full_detail(app_client):
    await _seed_user_and_login(app_client)
    created = await _create_project(app_client)
    resp = await app_client.get(f"/api/projects/{created['id']}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == created["id"]
    assert body["mcp_url"] == "http://mcp.local:8081"
    assert body["auditor_params"] == {}


async def test_patch_updates_allowed_fields(app_client):
    await _seed_user_and_login(app_client)
    created = await _create_project(app_client)
    resp = await app_client.patch(
        f"/api/projects/{created['id']}",
        json={"description": "updated", "notes": "n1"},
        headers=_csrf_headers(app_client),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["description"] == "updated"
    assert body["notes"] == "n1"


async def test_patch_with_status_is_400(app_client):
    await _seed_user_and_login(app_client)
    created = await _create_project(app_client)
    resp = await app_client.patch(
        f"/api/projects/{created['id']}",
        json={"status": "active"},
        headers=_csrf_headers(app_client),
    )
    # extra="forbid" → 422 from FastAPI; we accept either 400 or 422 as
    # the "client sent a forbidden field" outcome but never 200.
    assert resp.status_code in (400, 422)


async def test_patch_empty_body_is_validation_error(app_client):
    await _seed_user_and_login(app_client)
    created = await _create_project(app_client)
    resp = await app_client.patch(
        f"/api/projects/{created['id']}",
        json={},
        headers=_csrf_headers(app_client),
    )
    assert resp.status_code in (400, 422)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
async def test_create_rejects_invalid_trading_session(app_client):
    await _seed_user_and_login(app_client)
    resp = await app_client.post(
        "/api/projects",
        json=_project_payload(trading_sessions=["mars"]),
        headers=_csrf_headers(app_client),
    )
    assert resp.status_code in (400, 422)


async def test_create_rejects_invalid_timeframe(app_client):
    await _seed_user_and_login(app_client)
    resp = await app_client.post(
        "/api/projects",
        json=_project_payload(timeframe="H7"),
        headers=_csrf_headers(app_client),
    )
    assert resp.status_code in (400, 422)


async def test_create_rejects_duplicate_name_per_tenant(app_client):
    await _seed_user_and_login(app_client)
    await _create_project(app_client, name="dup")
    resp = await app_client.post(
        "/api/projects",
        json=_project_payload(name="dup"),
        headers=_csrf_headers(app_client),
    )
    assert resp.status_code == 409


async def test_list_status_filter_rejects_garbage(app_client):
    await _seed_user_and_login(app_client)
    resp = await app_client.get("/api/projects?status=banana")
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Cross-tenant denial — return 404, never 403
# ---------------------------------------------------------------------------
async def test_get_cross_tenant_returns_404(app_client):
    # Seed A and create their project.
    await _seed_user_and_login(app_client, email="a@example.com")
    a_project = await _create_project(app_client)
    # Log out A by clearing cookies, then log B in.
    app_client.cookies.clear()
    await _seed_user_and_login(app_client, email="b@example.com")

    resp = await app_client.get(f"/api/projects/{a_project['id']}")
    assert resp.status_code == 404


async def test_patch_cross_tenant_returns_404(app_client):
    await _seed_user_and_login(app_client, email="a@example.com")
    a_project = await _create_project(app_client)
    app_client.cookies.clear()
    await _seed_user_and_login(app_client, email="b@example.com")

    resp = await app_client.patch(
        f"/api/projects/{a_project['id']}",
        json={"description": "x"},
        headers=_csrf_headers(app_client),
    )
    assert resp.status_code == 404


async def test_delete_cross_tenant_returns_404(app_client):
    await _seed_user_and_login(app_client, email="a@example.com")
    a_project = await _create_project(app_client)
    app_client.cookies.clear()
    await _seed_user_and_login(app_client, email="b@example.com")

    resp = await app_client.delete(
        f"/api/projects/{a_project['id']}", headers=_csrf_headers(app_client)
    )
    assert resp.status_code == 404


async def test_lifecycle_cross_tenant_returns_404(app_client):
    await _seed_user_and_login(app_client, email="a@example.com")
    a_project = await _create_project(app_client)
    app_client.cookies.clear()
    await _seed_user_and_login(app_client, email="b@example.com")

    resp = await app_client.post(
        f"/api/projects/{a_project['id']}/activate",
        headers=_csrf_headers(app_client),
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Lifecycle — happy paths
# ---------------------------------------------------------------------------
async def test_activate_from_inactive(app_client):
    await _seed_user_and_login(app_client)
    created = await _create_project(app_client)
    resp = await app_client.post(
        f"/api/projects/{created['id']}/activate",
        headers=_csrf_headers(app_client),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "active"


async def test_pause_after_activate(app_client):
    await _seed_user_and_login(app_client)
    created = await _create_project(app_client)
    await app_client.post(
        f"/api/projects/{created['id']}/activate",
        headers=_csrf_headers(app_client),
    )
    resp = await app_client.post(
        f"/api/projects/{created['id']}/pause",
        headers=_csrf_headers(app_client),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "paused"


async def test_stop_from_active(app_client):
    await _seed_user_and_login(app_client)
    created = await _create_project(app_client)
    await app_client.post(
        f"/api/projects/{created['id']}/activate",
        headers=_csrf_headers(app_client),
    )
    resp = await app_client.post(
        f"/api/projects/{created['id']}/stop",
        headers=_csrf_headers(app_client),
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "stopped"


async def test_mark_error_from_active(app_client):
    await _seed_user_and_login(app_client)
    created = await _create_project(app_client)
    await app_client.post(
        f"/api/projects/{created['id']}/activate",
        headers=_csrf_headers(app_client),
    )
    resp = await app_client.post(
        f"/api/projects/{created['id']}/mark-error",
        headers=_csrf_headers(app_client),
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "error"


async def test_maintenance_from_inactive(app_client):
    await _seed_user_and_login(app_client)
    created = await _create_project(app_client)
    resp = await app_client.post(
        f"/api/projects/{created['id']}/maintenance",
        headers=_csrf_headers(app_client),
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "maintenance"


# ---------------------------------------------------------------------------
# Lifecycle — denied transitions
# ---------------------------------------------------------------------------
async def test_pause_from_inactive_is_409(app_client):
    await _seed_user_and_login(app_client)
    created = await _create_project(app_client)
    resp = await app_client.post(
        f"/api/projects/{created['id']}/pause",
        headers=_csrf_headers(app_client),
    )
    assert resp.status_code == 409


async def test_activate_from_error_is_409(app_client):
    """``error -> active`` is NOT a valid transition per the matrix."""
    await _seed_user_and_login(app_client)
    created = await _create_project(app_client)
    # Force the row to ``error`` so we don't depend on the long path.
    await _force_status(created["id"], status="error")
    resp = await app_client.post(
        f"/api/projects/{created['id']}/activate",
        headers=_csrf_headers(app_client),
    )
    assert resp.status_code == 409
    body = resp.json()
    assert body["detail"]["code"] == "invalid_transition"
    assert body["detail"]["from"] == "error"
    assert body["detail"]["to"] == "active"


async def test_state_machine_matrix_consistency():
    """Sanity: ``can_transition`` matches the published matrix exactly."""
    statuses = ["inactive", "active", "paused", "stopped", "error", "maintenance"]
    for f in statuses:
        for t in statuses:
            expected = t in VALID_TRANSITIONS[f]  # type: ignore[index]
            assert can_transition(f, t) is expected, (f, t)


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------
async def test_delete_inactive_succeeds(app_client):
    await _seed_user_and_login(app_client)
    created = await _create_project(app_client)
    resp = await app_client.delete(
        f"/api/projects/{created['id']}", headers=_csrf_headers(app_client)
    )
    assert resp.status_code == 204

    follow_up = await app_client.get(f"/api/projects/{created['id']}")
    assert follow_up.status_code == 404


async def test_delete_active_is_409(app_client):
    await _seed_user_and_login(app_client)
    created = await _create_project(app_client)
    await app_client.post(
        f"/api/projects/{created['id']}/activate",
        headers=_csrf_headers(app_client),
    )
    resp = await app_client.delete(
        f"/api/projects/{created['id']}", headers=_csrf_headers(app_client)
    )
    assert resp.status_code == 409


async def test_delete_with_container_id_is_409(app_client):
    """Even when in ``stopped``, a live container_id blocks delete."""
    await _seed_user_and_login(app_client)
    created = await _create_project(app_client)
    # active → stopped via lifecycle, then plant a container_id by hand.
    await app_client.post(
        f"/api/projects/{created['id']}/activate",
        headers=_csrf_headers(app_client),
    )
    await app_client.post(
        f"/api/projects/{created['id']}/stop",
        headers=_csrf_headers(app_client),
    )

    from aether_api.db.session import get_session_maker
    from sqlalchemy import text

    maker = get_session_maker()
    async with maker() as session:
        await session.execute(
            text("UPDATE projects SET container_id = 'live123' WHERE id = :id"),
            {"id": uuid.UUID(created["id"])},
        )
        await session.commit()

    resp = await app_client.delete(
        f"/api/projects/{created['id']}", headers=_csrf_headers(app_client)
    )
    assert resp.status_code == 409


# ---------------------------------------------------------------------------
# Auth cookie sanity (catches login-helper drift).
# ---------------------------------------------------------------------------
async def test_login_helper_sets_access_cookie(app_client):
    await _seed_user_and_login(app_client)
    assert app_client.cookies.get(ACCESS_COOKIE) is not None


# ---------------------------------------------------------------------------
# Orchestrator agent slot — added in migration 0010 (charter correction).
# ---------------------------------------------------------------------------
async def _seed_orchestrator_for_user(email: str) -> str:
    """Seed an Orquestador agent owned by ``email`` and return its id."""
    from aether_api.db.session import get_session_maker

    from tests._helpers import seed_agent
    from sqlalchemy import select
    from aether_api.models.user import User

    maker = get_session_maker()
    async with maker() as session:
        owner = (
            await session.execute(select(User).where(User.email == email.lower()))
        ).scalar_one()
        agent = await seed_agent(
            session,
            owner=owner,
            name=f"orc-{email}",
            type="orchestrator",
            logica="def orchestrate(ctx):\n    return None\n",
        )
        await session.commit()
        return str(agent.id)


async def test_create_project_with_orchestrator_agent_id(app_client):
    """POST /api/projects accepts ``orchestrator_agent_id`` and persists it."""
    await _seed_user_and_login(app_client, email="orc-c@example.com")
    orc_id = await _seed_orchestrator_for_user("orc-c@example.com")

    resp = await app_client.post(
        "/api/projects",
        json=_project_payload(orchestrator_agent_id=orc_id),
        headers=_csrf_headers(app_client),
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["orchestrator_agent_id"] == orc_id
    # The matching JSONB params block defaults to {} server-side.
    assert body["orchestrator_params"] == {}


async def test_patch_project_swaps_orchestrator(app_client):
    """PATCH /api/projects/{id} can change the bound Orquestador in place."""
    await _seed_user_and_login(app_client, email="orc-p@example.com")
    orc_one = await _seed_orchestrator_for_user("orc-p@example.com")
    # Seed a second orchestrator and grab its id.
    from aether_api.db.session import get_session_maker
    from aether_api.models.user import User
    from sqlalchemy import select

    from tests._helpers import seed_agent

    maker = get_session_maker()
    async with maker() as session:
        owner = (
            await session.execute(
                select(User).where(User.email == "orc-p@example.com")
            )
        ).scalar_one()
        second = await seed_agent(
            session,
            owner=owner,
            name="orc-second",
            type="orchestrator",
            logica="def orchestrate(ctx):\n    return None\n",
        )
        await session.commit()
        orc_two = str(second.id)

    created = await _create_project(
        app_client, orchestrator_agent_id=orc_one
    )
    assert created["orchestrator_agent_id"] == orc_one

    resp = await app_client.patch(
        f"/api/projects/{created['id']}",
        json={"orchestrator_agent_id": orc_two},
        headers=_csrf_headers(app_client),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["orchestrator_agent_id"] == orc_two


async def test_patch_project_updates_orchestrator_params(app_client):
    """``orchestrator_params`` JSONB is patchable like the other params blocks."""
    await _seed_user_and_login(app_client, email="orc-prm@example.com")
    created = await _create_project(app_client)
    resp = await app_client.patch(
        f"/api/projects/{created['id']}",
        json={"orchestrator_params": {"max_concurrent": 4, "mode": "strict"}},
        headers=_csrf_headers(app_client),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["orchestrator_params"] == {
        "max_concurrent": 4,
        "mode": "strict",
    }


async def test_cross_tenant_orchestrator_id_returns_404_on_get(app_client):
    """Tenant A creates an orchestrator and a project that wires it.
    Tenant B fetches the project by id → 404 (existence is not disclosed)."""
    await _seed_user_and_login(app_client, email="orc-a@example.com")
    orc_id = await _seed_orchestrator_for_user("orc-a@example.com")
    a_project = await _create_project(
        app_client, orchestrator_agent_id=orc_id
    )

    # Swap to tenant B.
    app_client.cookies.clear()
    await _seed_user_and_login(app_client, email="orc-b@example.com")

    resp = await app_client.get(f"/api/projects/{a_project['id']}")
    assert resp.status_code == 404
