"""Cross-tenant isolation gate.

This is THE test the multi-tenancy spec is built to satisfy:

* User A creates a project and an agent.
* User B logs in.
* User B's listing endpoints MUST NOT include A's rows.
* User B's GET-by-id endpoints MUST return 404 (NOT 403) for A's rows.

A 403 here would be a sev-1 finding — it confirms the resource exists.
"""

from __future__ import annotations

import pytest

# Release-gate: a failure here is a sev-1 (cross-tenant leak) and a release
# blocker. CI runs `pytest -m release_gate` against a real Postgres before
# the migrations job hands off success. The marker MUST stay on this module.
pytestmark = [pytest.mark.integration, pytest.mark.release_gate]


async def _seed_two_tenants(client):
    """Seed user A (with one project + one agent) and user B (clean).

    Returns ``(user_a_id, project_id, agent_id, user_b_email, user_b_pw)``.
    """
    from aether_api.db.session import get_session_maker

    from tests._helpers import seed_agent, seed_project, seed_user

    maker = get_session_maker()
    async with maker() as session:
        user_a = await seed_user(session, email="a@example.com", password="apasspasspass")
        user_b = await seed_user(session, email="b@example.com", password="bpasspasspass")
        agent_a = await seed_agent(session, owner=user_a, name="a-worker")
        project_a = await seed_project(session, owner=user_a, name="a-project")
        await session.commit()
        return user_a.id, project_a.id, agent_a.id, user_b.email, "bpasspasspass"


async def _login_b(client, email: str, pw: str):
    resp = await client.post("/api/auth/login", json={"email": email, "password": pw})
    assert resp.status_code == 200, resp.text


async def test_list_projects_does_not_leak_other_tenant(app_client):
    _ua, _pa, _aa, b_email, b_pw = await _seed_two_tenants(app_client)
    await _login_b(app_client, b_email, b_pw)
    resp = await app_client.get("/api/pairs")
    assert resp.status_code == 200, resp.text
    # projects-crud changed list shape from `[]` to a paginated envelope
    # `{items, total, limit, offset}`. Assert the cross-tenant invariant on the
    # `items` array and the `total` count — those are what enforce no leak.
    body = resp.json()
    assert body["items"] == []  # user B owns nothing
    assert body["total"] == 0


async def test_get_other_tenant_project_returns_404(app_client):
    _ua, project_a, _aa, b_email, b_pw = await _seed_two_tenants(app_client)
    await _login_b(app_client, b_email, b_pw)
    resp = await app_client.get(f"/api/pairs/{project_a}")
    # MUST be 404, not 403 — existence is NOT disclosed.
    assert resp.status_code == 404


async def test_get_other_tenant_agent_returns_404(app_client):
    _ua, _pa, agent_a, b_email, b_pw = await _seed_two_tenants(app_client)
    await _login_b(app_client, b_email, b_pw)
    resp = await app_client.get(f"/api/agents/{agent_a}")
    assert resp.status_code == 404


async def test_list_agents_does_not_leak_other_tenant(app_client):
    _ua, _pa, _aa, b_email, b_pw = await _seed_two_tenants(app_client)
    await _login_b(app_client, b_email, b_pw)
    resp = await app_client.get("/api/agents")
    assert resp.status_code == 200
    assert resp.json() == []
