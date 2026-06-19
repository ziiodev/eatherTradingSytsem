"""Integration tests for the /api/eas CRUD + codegen surface (ea-management P3).

Covers:
- Feature-flag gating: default app (AETHER_EAS_ENABLED unset → False) → /api/eas 404.
- Auth gate: 401 without a session cookie.
- CRUD happy path: create → list → get → patch (version bump on graph change) → delete (soft-archive).
- Cross-tenant denial: 404 (NEVER 403) for get/patch/delete/codegen.
- CSRF gate on writes (POST/PATCH/DELETE/codegen).
- Codegen mql5 + python success from the stored graph.
- Codegen preview (no-persist) over a posted graph.
- Codegen over an empty graph succeeds; over an invalid graph → 422 (not 5xx).
- Malformed body → 422 (Pydantic), not 5xx.

The EA router is feature-flagged OFF by default, so the bulk of the suite runs
against a dedicated ``eas_client`` fixture that boots a fresh app with
``AETHER_EAS_ENABLED=true``.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def eas_client(
    migrated_db: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator:
    """Boot a fresh FastAPI app with the EA feature flag ON.

    Mirrors the shared ``app_client`` fixture but flips ``AETHER_EAS_ENABLED``
    before ``create_app()`` so the ``/api/eas`` router is mounted. The settings
    lru_cache is cleared so the new env value takes effect.
    """
    try:
        import httpx
        from asgi_lifespan import LifespanManager
    except ImportError:  # pragma: no cover
        pytest.skip("asgi-lifespan / httpx not installed")
        return

    monkeypatch.setenv("AETHER_EAS_ENABLED", "true")

    from aether_api.core.settings import get_settings
    from aether_api.main import create_app

    get_settings.cache_clear()
    app = create_app()

    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            yield client

    get_settings.cache_clear()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _seed_and_login(client, email: str, password: str = "testtesttesttest") -> str:
    from aether_api.db.session import get_session_maker

    from tests._helpers import seed_user

    maker = get_session_maker()
    async with maker() as session:
        user = await seed_user(session, email=email, password=password)
        await session.commit()
        user_id = str(user.id)

    resp = await client.post("/api/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.text
    return user_id


def _csrf_headers(client) -> dict[str, str]:
    from aether_api.auth.cookies import CSRF_COOKIE

    token = client.cookies.get(CSRF_COOKIE)
    assert token, "csrf cookie missing — login was not run first"
    return {"X-CSRF-Token": token}


# A small but real graph: Start → RSI. The exact node set is not the point —
# we only assert that codegen emits *something* for both targets.
GRAPH = {
    "nodes": [
        {"id": "n1", "type": "Start", "data": {}},
        {"id": "n2", "type": "RSI", "data": {"period": 14}},
    ],
    "edges": [{"id": "e1", "source": "n1", "target": "n2"}],
}
EMPTY_GRAPH = {"nodes": [], "edges": []}


# ---------------------------------------------------------------------------
# Feature flag
# ---------------------------------------------------------------------------


async def test_flag_off_router_not_mounted(app_client) -> None:
    """Default app (flag unset → False): /api/eas is not mounted → 404."""
    await _seed_and_login(app_client, email="flagoff@example.com")
    resp = await app_client.get("/api/eas")
    assert resp.status_code == 404, resp.text


async def test_health_reports_eas_flag(app_client) -> None:
    resp = await app_client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json()["features"]["eas_enabled"] is False


async def test_health_reports_eas_flag_on(eas_client) -> None:
    resp = await eas_client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json()["features"]["eas_enabled"] is True


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


async def test_list_requires_auth(eas_client) -> None:
    resp = await eas_client.get("/api/eas")
    assert resp.status_code == 401, resp.text


# ---------------------------------------------------------------------------
# CRUD happy path
# ---------------------------------------------------------------------------


async def test_crud_happy_path(eas_client) -> None:
    await _seed_and_login(eas_client, email="owner@example.com")
    headers = _csrf_headers(eas_client)

    # Create (graph omitted → defaults to empty-but-valid envelope).
    resp = await eas_client.post(
        "/api/eas", json={"name": "Scalper", "description": "x"}, headers=headers
    )
    assert resp.status_code == 201, resp.text
    created = resp.json()
    ea_id = created["id"]
    uuid.UUID(ea_id)
    assert created["name"] == "Scalper"
    assert created["version"] == 1
    assert created["is_active"] is True
    assert created["graph"] == EMPTY_GRAPH

    # List.
    resp = await eas_client.get("/api/eas")
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 1
    assert rows[0]["id"] == ea_id
    assert "graph" not in rows[0]  # summary excludes the heavy body

    # Get detail.
    resp = await eas_client.get(f"/api/eas/{ea_id}")
    assert resp.status_code == 200
    detail = resp.json()
    assert detail["graph"] == EMPTY_GRAPH
    updated_at = detail["updated_at"]

    # Patch graph → version bump.
    resp = await eas_client.patch(
        f"/api/eas/{ea_id}",
        json={"graph": GRAPH, "updated_at": updated_at},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    patched = resp.json()
    assert patched["version"] == 2
    assert patched["graph"] == GRAPH

    # Patch name-only → NO version bump.
    resp = await eas_client.get(f"/api/eas/{ea_id}")
    updated_at = resp.json()["updated_at"]
    resp = await eas_client.patch(
        f"/api/eas/{ea_id}",
        json={"name": "Scalper v2", "updated_at": updated_at},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["version"] == 2
    assert resp.json()["name"] == "Scalper v2"

    # Delete → soft archive (is_active False), row still fetchable.
    resp = await eas_client.delete(f"/api/eas/{ea_id}", headers=headers)
    assert resp.status_code == 204, resp.text
    resp = await eas_client.get(f"/api/eas/{ea_id}")
    assert resp.status_code == 200
    assert resp.json()["is_active"] is False
    # Default list filters nothing; is_active=true filter hides it.
    resp = await eas_client.get("/api/eas", params={"is_active": "true"})
    assert resp.json() == []


async def test_create_rejects_client_user_id(eas_client) -> None:
    await _seed_and_login(eas_client, email="strict@example.com")
    resp = await eas_client.post(
        "/api/eas",
        json={"name": "x", "user_id": str(uuid.uuid4())},
        headers=_csrf_headers(eas_client),
    )
    assert resp.status_code == 422, resp.text


async def test_patch_without_updated_at_is_428(eas_client) -> None:
    await _seed_and_login(eas_client, email="lock@example.com")
    headers = _csrf_headers(eas_client)
    ea_id = (
        await eas_client.post("/api/eas", json={"name": "n"}, headers=headers)
    ).json()["id"]
    resp = await eas_client.patch(
        f"/api/eas/{ea_id}", json={"name": "n2"}, headers=headers
    )
    assert resp.status_code == 428, resp.text


# ---------------------------------------------------------------------------
# CSRF
# ---------------------------------------------------------------------------


async def test_create_without_csrf_is_403(eas_client) -> None:
    await _seed_and_login(eas_client, email="csrf@example.com")
    resp = await eas_client.post("/api/eas", json={"name": "n"})  # no CSRF header
    assert resp.status_code == 403, resp.text


async def test_codegen_without_csrf_is_403(eas_client) -> None:
    await _seed_and_login(eas_client, email="csrf2@example.com")
    resp = await eas_client.post(
        "/api/eas/codegen/mql5", json={"graph": GRAPH}
    )  # no CSRF header
    assert resp.status_code == 403, resp.text


# ---------------------------------------------------------------------------
# Cross-tenant
# ---------------------------------------------------------------------------


async def test_cross_tenant_get_is_404(eas_client) -> None:
    await _seed_and_login(eas_client, email="a@example.com", password="apasspasspass")
    ea_id = (
        await eas_client.post(
            "/api/eas", json={"name": "secret"}, headers=_csrf_headers(eas_client)
        )
    ).json()["id"]

    await eas_client.post("/api/auth/logout", headers=_csrf_headers(eas_client))
    eas_client.cookies.clear()
    await _seed_and_login(eas_client, email="b@example.com", password="bpasspasspass")

    # Get / patch / delete / codegen all 404 across the tenant boundary.
    assert (await eas_client.get(f"/api/eas/{ea_id}")).status_code == 404
    headers = _csrf_headers(eas_client)
    r = await eas_client.patch(
        f"/api/eas/{ea_id}",
        json={"name": "z", "updated_at": "2020-01-01T00:00:00"},
        headers=headers,
    )
    assert r.status_code == 404
    assert (await eas_client.delete(f"/api/eas/{ea_id}", headers=headers)).status_code == 404
    r = await eas_client.post(f"/api/eas/{ea_id}/codegen/mql5", headers=headers)
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Codegen — stored graph
# ---------------------------------------------------------------------------


async def test_codegen_stored_mql5_and_python(eas_client) -> None:
    await _seed_and_login(eas_client, email="gen@example.com")
    headers = _csrf_headers(eas_client)
    ea_id = (
        await eas_client.post(
            "/api/eas", json={"name": "GenEA", "graph": GRAPH}, headers=headers
        )
    ).json()["id"]

    r = await eas_client.post(f"/api/eas/{ea_id}/codegen/mql5", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["target"] == "mql5"
    assert body["ea_name"] == "GenEA"
    assert "OnTick" in body["source"]

    r = await eas_client.post(f"/api/eas/{ea_id}/codegen/python", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["target"] == "python"
    assert len(r.json()["source"]) > 0


async def test_codegen_empty_graph_succeeds(eas_client) -> None:
    """An empty (but valid) graph renders the empty-strategy skeleton, not 5xx."""
    await _seed_and_login(eas_client, email="empty@example.com")
    headers = _csrf_headers(eas_client)
    ea_id = (
        await eas_client.post("/api/eas", json={"name": "Empty"}, headers=headers)
    ).json()["id"]
    r = await eas_client.post(f"/api/eas/{ea_id}/codegen/mql5", headers=headers)
    assert r.status_code == 200, r.text
    assert "OnTick" in r.json()["source"]


# ---------------------------------------------------------------------------
# Codegen — preview (no persist)
# ---------------------------------------------------------------------------


async def test_codegen_preview_mql5(eas_client) -> None:
    await _seed_and_login(eas_client, email="preview@example.com")
    headers = _csrf_headers(eas_client)
    r = await eas_client.post(
        "/api/eas/codegen/mql5",
        json={"graph": GRAPH, "ea_name": "MyPreview"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["ea_name"] == "MyPreview"
    assert "OnTick" in r.json()["source"]
    # Preview must NOT persist anything.
    assert (await eas_client.get("/api/eas")).json() == []


async def test_codegen_preview_python(eas_client) -> None:
    await _seed_and_login(eas_client, email="preview2@example.com")
    headers = _csrf_headers(eas_client)
    r = await eas_client.post(
        "/api/eas/codegen/python", json={"graph": EMPTY_GRAPH}, headers=headers
    )
    assert r.status_code == 200, r.text
    assert r.json()["target"] == "python"


async def test_codegen_invalid_graph_is_422(eas_client) -> None:
    """A graph the generator cannot render → 422, never 5xx.

    ``nodes`` as a non-iterable-of-dicts shape blows up inside the pure
    generator; the router must trap it and return a structured 422.
    """
    await _seed_and_login(eas_client, email="bad@example.com")
    headers = _csrf_headers(eas_client)
    r = await eas_client.post(
        "/api/eas/codegen/mql5",
        json={"graph": {"nodes": [123, "not-a-node"], "edges": []}},
        headers=headers,
    )
    assert r.status_code == 422, r.text
    assert r.json()["detail"]["code"] == "codegen_failed"


async def test_codegen_preview_malformed_body_is_422(eas_client) -> None:
    """Missing required ``graph`` key → Pydantic 422, not a 5xx."""
    await _seed_and_login(eas_client, email="malformed@example.com")
    headers = _csrf_headers(eas_client)
    r = await eas_client.post("/api/eas/codegen/mql5", json={}, headers=headers)
    assert r.status_code == 422, r.text
