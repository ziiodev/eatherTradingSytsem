"""End-to-end coverage for /api/exchanges CRUD + cross-tenant isolation.

Mirrors the contract enforced in :mod:`aether_api.routers.exchanges`:

* Auth gating (401 without cookie).
* CSRF on state-changing endpoints (403 without token).
* Cross-tenant denial → 404 (never 403; no existence leak).
* Per-tenant unique ``code`` → 409.
* RESTRICT delete (exchange with an account) → 409.
"""

from __future__ import annotations

import pytest

from aether_api.auth.cookies import CSRF_COOKIE

pytestmark = pytest.mark.integration


async def _seed_user_and_login(
    client,
    *,
    email: str = "ex-ops@example.com",
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


def _csrf(client) -> dict[str, str]:
    token = client.cookies.get(CSRF_COOKIE)
    assert token
    return {"X-CSRF-Token": token}


def _payload(**overrides) -> dict:
    body = {"name": "IC Markets", "code": "ICM", "kind": "broker"}
    body.update(overrides)
    return body


async def _create(client, **overrides) -> dict:
    resp = await client.post(
        "/api/exchanges", json=_payload(**overrides), headers=_csrf(client)
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# Auth + CSRF
# ---------------------------------------------------------------------------
async def test_list_requires_auth(app_client):
    resp = await app_client.get("/api/exchanges")
    assert resp.status_code == 401


async def test_create_without_csrf_is_403(app_client):
    await _seed_user_and_login(app_client)
    resp = await app_client.post("/api/exchanges", json=_payload())
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# CRUD happy paths
# ---------------------------------------------------------------------------
async def test_create_returns_defaults(app_client):
    await _seed_user_and_login(app_client)
    body = await _create(app_client)
    assert body["code"] == "ICM"
    assert body["kind"] == "broker"


async def test_create_defaults_kind_to_broker(app_client):
    await _seed_user_and_login(app_client)
    resp = await app_client.post(
        "/api/exchanges",
        json={"name": "X", "code": "XXX"},
        headers=_csrf(app_client),
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["kind"] == "broker"


async def test_create_rejects_bad_kind(app_client):
    await _seed_user_and_login(app_client)
    resp = await app_client.post(
        "/api/exchanges",
        json=_payload(kind="banana"),
        headers=_csrf(app_client),
    )
    assert resp.status_code in (400, 422)


async def test_list_and_get(app_client):
    await _seed_user_and_login(app_client)
    created = await _create(app_client)
    lst = await app_client.get("/api/exchanges")
    assert lst.status_code == 200
    assert lst.json()["total"] == 1
    one = await app_client.get(f"/api/exchanges/{created['id']}")
    assert one.status_code == 200
    assert one.json()["id"] == created["id"]


async def test_patch_updates_fields(app_client):
    await _seed_user_and_login(app_client)
    created = await _create(app_client)
    resp = await app_client.patch(
        f"/api/exchanges/{created['id']}",
        json={"name": "Renamed"},
        headers=_csrf(app_client),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["name"] == "Renamed"


async def test_duplicate_code_per_tenant_is_409(app_client):
    await _seed_user_and_login(app_client)
    await _create(app_client, code="DUP")
    resp = await app_client.post(
        "/api/exchanges", json=_payload(code="DUP"), headers=_csrf(app_client)
    )
    assert resp.status_code == 409


async def test_delete_succeeds(app_client):
    await _seed_user_and_login(app_client)
    created = await _create(app_client)
    resp = await app_client.delete(
        f"/api/exchanges/{created['id']}", headers=_csrf(app_client)
    )
    assert resp.status_code == 204
    follow = await app_client.get(f"/api/exchanges/{created['id']}")
    assert follow.status_code == 404


# ---------------------------------------------------------------------------
# Cross-tenant — always 404, never 403
# ---------------------------------------------------------------------------
async def test_get_cross_tenant_returns_404(app_client):
    await _seed_user_and_login(app_client, email="ex-a@example.com")
    a = await _create(app_client)
    app_client.cookies.clear()
    await _seed_user_and_login(app_client, email="ex-b@example.com")
    resp = await app_client.get(f"/api/exchanges/{a['id']}")
    assert resp.status_code == 404


async def test_patch_cross_tenant_returns_404(app_client):
    await _seed_user_and_login(app_client, email="ex-pa@example.com")
    a = await _create(app_client)
    app_client.cookies.clear()
    await _seed_user_and_login(app_client, email="ex-pb@example.com")
    resp = await app_client.patch(
        f"/api/exchanges/{a['id']}",
        json={"name": "x"},
        headers=_csrf(app_client),
    )
    assert resp.status_code == 404


async def test_delete_cross_tenant_returns_404(app_client):
    await _seed_user_and_login(app_client, email="ex-da@example.com")
    a = await _create(app_client)
    app_client.cookies.clear()
    await _seed_user_and_login(app_client, email="ex-db@example.com")
    resp = await app_client.delete(
        f"/api/exchanges/{a['id']}", headers=_csrf(app_client)
    )
    assert resp.status_code == 404
