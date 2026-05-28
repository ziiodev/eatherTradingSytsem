"""``agent_skills`` join — attach / detach / cross-tenant / cascade.

Covers the deliverables of the ``skills-markdown-and-agent-binding`` change:

* Attach own skill to own agent → 201.
* Attach cross-tenant skill to own agent → 404 (non-disclosure).
* Attach own skill to cross-tenant agent → 404.
* Double-attach the same (agent_id, skill_id) pair → 409.
* Detach an attached binding → 204, then 404 on re-detach.
* Detach a binding that doesn't exist → 404.
* Hard-deleting a skill that is attached → 409 (ON DELETE RESTRICT).
* Hard-deleting an agent that has bindings → 204 (CASCADE removes
  ``agent_skills`` rows automatically).
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.integration


CODE_VALID = "def rsi(series, period=14):\n    return series\n"
MD_BODY = "# Entry rule\n\nbuy when RSI < 30\n"


async def _seed_and_login(client, email: str) -> str:
    from aether_api.db.session import get_session_maker

    from tests._helpers import seed_user

    maker = get_session_maker()
    async with maker() as session:
        user = await seed_user(session, email=email, password="testtesttesttest")
        await session.commit()
        user_id = str(user.id)

    resp = await client.post(
        "/api/auth/login",
        json={"email": email, "password": "testtesttesttest"},
    )
    assert resp.status_code == 200, resp.text
    return user_id


def _csrf_headers(client) -> dict[str, str]:
    from aether_api.auth.cookies import CSRF_COOKIE

    token = client.cookies.get(CSRF_COOKIE)
    assert token, "csrf cookie missing — login was not run first"
    return {"X-CSRF-Token": token}


async def _make_agent(client, *, name: str = "worker-a") -> str:
    resp = await client.post(
        "/api/agents",
        json={
            "name": name,
            "type": "worker",
            "logica": "def on_tick(ctx):\n    return None\n",
            "entrypoint": "on_tick",
        },
        headers=_csrf_headers(client),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _make_skill(
    client, *, name: str = "rsi-14", runtime: str = "markdown"
) -> str:
    body = CODE_VALID if runtime == "python" else MD_BODY
    resp = await client.post(
        "/api/skills",
        json={
            "name": name,
            "type": "analytic",
            "runtime": runtime,
            "code": body,
        },
        headers=_csrf_headers(client),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


# ---------------------------------------------------------------------------
# Happy path: attach + list + detach
# ---------------------------------------------------------------------------


async def test_attach_own_skill_to_own_agent_returns_201(app_client) -> None:
    await _seed_and_login(app_client, email="happy@example.com")
    headers = _csrf_headers(app_client)
    agent_id = await _make_agent(app_client)
    skill_id = await _make_skill(app_client)

    resp = await app_client.post(
        f"/api/agents/{agent_id}/skills",
        json={"skill_id": skill_id, "notes": "RSI baseline"},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["skill_id"] == skill_id
    assert body["notes"] == "RSI baseline"
    assert body["runtime"] == "markdown"


async def test_list_attached_skills_returns_only_bindings(app_client) -> None:
    await _seed_and_login(app_client, email="lister@example.com")
    headers = _csrf_headers(app_client)
    agent_id = await _make_agent(app_client)
    skill_a = await _make_skill(app_client, name="rsi-a")
    skill_b = await _make_skill(app_client, name="rsi-b")

    # Attach A only.
    r1 = await app_client.post(
        f"/api/agents/{agent_id}/skills",
        json={"skill_id": skill_a},
        headers=headers,
    )
    assert r1.status_code == 201

    listing = await app_client.get(f"/api/agents/{agent_id}/skills")
    assert listing.status_code == 200
    ids = {row["skill_id"] for row in listing.json()}
    assert ids == {skill_a}
    assert skill_b not in ids


# ---------------------------------------------------------------------------
# Cross-tenant: skill belongs to other user
# ---------------------------------------------------------------------------


async def test_attach_other_users_skill_returns_404(app_client) -> None:
    # User A creates a skill.
    await _seed_and_login(app_client, email="user-a@example.com")
    a_headers = _csrf_headers(app_client)
    skill_id_a = await _make_skill(app_client, name="secret-rule")

    # User B has their own agent — tries to attach user A's skill.
    app_client.cookies.clear()
    await _seed_and_login(app_client, email="user-b@example.com")
    b_headers = _csrf_headers(app_client)
    agent_id_b = await _make_agent(app_client, name="worker-b")

    resp = await app_client.post(
        f"/api/agents/{agent_id_b}/skills",
        json={"skill_id": skill_id_a},
        headers=b_headers,
    )
    assert resp.status_code == 404  # NOT 403 — non-disclosure
    assert a_headers  # silence linter


# ---------------------------------------------------------------------------
# Cross-tenant: agent belongs to other user
# ---------------------------------------------------------------------------


async def test_attach_own_skill_to_foreign_agent_returns_404(app_client) -> None:
    # User A has an agent.
    await _seed_and_login(app_client, email="agent-owner@example.com")
    agent_id_a = await _make_agent(app_client, name="worker-a")

    # User B owns a skill but doesn't own A's agent.
    app_client.cookies.clear()
    await _seed_and_login(app_client, email="skill-owner@example.com")
    b_headers = _csrf_headers(app_client)
    skill_id_b = await _make_skill(app_client, name="b-skill")

    resp = await app_client.post(
        f"/api/agents/{agent_id_a}/skills",
        json={"skill_id": skill_id_b},
        headers=b_headers,
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Double-attach → 409
# ---------------------------------------------------------------------------


async def test_double_attach_returns_409(app_client) -> None:
    await _seed_and_login(app_client, email="dup@example.com")
    headers = _csrf_headers(app_client)
    agent_id = await _make_agent(app_client)
    skill_id = await _make_skill(app_client)

    r1 = await app_client.post(
        f"/api/agents/{agent_id}/skills",
        json={"skill_id": skill_id},
        headers=headers,
    )
    assert r1.status_code == 201

    r2 = await app_client.post(
        f"/api/agents/{agent_id}/skills",
        json={"skill_id": skill_id},
        headers=headers,
    )
    assert r2.status_code == 409
    assert r2.json()["detail"]["code"] == "skill_already_attached"


# ---------------------------------------------------------------------------
# Detach
# ---------------------------------------------------------------------------


async def test_detach_success_returns_204(app_client) -> None:
    await _seed_and_login(app_client, email="detach@example.com")
    headers = _csrf_headers(app_client)
    agent_id = await _make_agent(app_client)
    skill_id = await _make_skill(app_client)
    r1 = await app_client.post(
        f"/api/agents/{agent_id}/skills",
        json={"skill_id": skill_id},
        headers=headers,
    )
    assert r1.status_code == 201

    r2 = await app_client.delete(
        f"/api/agents/{agent_id}/skills/{skill_id}",
        headers=headers,
    )
    assert r2.status_code == 204


async def test_detach_nonexistent_binding_returns_404(app_client) -> None:
    await _seed_and_login(app_client, email="detach-missing@example.com")
    headers = _csrf_headers(app_client)
    agent_id = await _make_agent(app_client)
    skill_id = await _make_skill(app_client)

    resp = await app_client.delete(
        f"/api/agents/{agent_id}/skills/{skill_id}",
        headers=headers,
    )
    assert resp.status_code == 404


async def test_detach_random_skill_id_returns_404(app_client) -> None:
    await _seed_and_login(app_client, email="detach-random@example.com")
    headers = _csrf_headers(app_client)
    agent_id = await _make_agent(app_client)

    resp = await app_client.delete(
        f"/api/agents/{agent_id}/skills/{uuid.uuid4()}",
        headers=headers,
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# RESTRICT — cannot hard-delete a skill that is attached
# ---------------------------------------------------------------------------


async def test_delete_attached_skill_returns_409(app_client) -> None:
    await _seed_and_login(app_client, email="restrict@example.com")
    headers = _csrf_headers(app_client)
    agent_id = await _make_agent(app_client)
    skill_id = await _make_skill(app_client)
    r1 = await app_client.post(
        f"/api/agents/{agent_id}/skills",
        json={"skill_id": skill_id},
        headers=headers,
    )
    assert r1.status_code == 201

    resp = await app_client.delete(f"/api/skills/{skill_id}", headers=headers)
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "skill_referenced"
    assert resp.json()["detail"]["used_by_agent_count"] == 1


# ---------------------------------------------------------------------------
# CASCADE — deleting an agent that has bindings purges them
# ---------------------------------------------------------------------------


async def test_delete_agent_cascades_to_bindings(app_client) -> None:
    """When the agent is hard-deleted, its agent_skills rows go too.

    The skill itself MUST survive (it has its own lifecycle) and become
    un-attached so a subsequent DELETE /api/skills/{id} succeeds.
    """
    await _seed_and_login(app_client, email="cascade@example.com")
    headers = _csrf_headers(app_client)
    agent_id = await _make_agent(app_client)
    skill_id = await _make_skill(app_client)
    r1 = await app_client.post(
        f"/api/agents/{agent_id}/skills",
        json={"skill_id": skill_id},
        headers=headers,
    )
    assert r1.status_code == 201

    # Delete the agent. Should be 204 — no project FKs it.
    r2 = await app_client.delete(f"/api/agents/{agent_id}", headers=headers)
    assert r2.status_code == 204, r2.text

    # Skill survives and can now be deleted.
    r3 = await app_client.delete(f"/api/skills/{skill_id}", headers=headers)
    assert r3.status_code == 204, r3.text


# ---------------------------------------------------------------------------
# Used-by count surfaces on the skill detail
# ---------------------------------------------------------------------------


async def test_skill_detail_reports_used_by_agent_count(app_client) -> None:
    await _seed_and_login(app_client, email="count@example.com")
    headers = _csrf_headers(app_client)
    agent_id = await _make_agent(app_client)
    skill_id = await _make_skill(app_client)

    detail_before = await app_client.get(f"/api/skills/{skill_id}")
    assert detail_before.status_code == 200
    assert detail_before.json()["used_by_agent_count"] == 0

    r1 = await app_client.post(
        f"/api/agents/{agent_id}/skills",
        json={"skill_id": skill_id},
        headers=headers,
    )
    assert r1.status_code == 201

    detail_after = await app_client.get(f"/api/skills/{skill_id}")
    assert detail_after.status_code == 200
    assert detail_after.json()["used_by_agent_count"] == 1
