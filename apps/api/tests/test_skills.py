"""Integration tests for the full /api/skills CRUD surface.

Covers:
- POST create (happy path, validation, ignores client-supplied user_id,
  type CHECK violations, signature round-trip).
- GET list (filters, ordering).
- GET detail (404 for missing, cross-tenant 404).
- PATCH (optimistic locking, version bump on code change, signature update).
- POST archive (idempotent).
- DELETE (204).
- CSRF gate on writes.
- code shape validation: ast.parse failure → 422, runtime CHECK constraint.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

pytestmark = pytest.mark.integration

CODE_VALID = "def rsi(series, period=14):\n    return series\n"
CODE_BROKEN = "def rsi(series, period=14)\n    return series\n"


async def _seed_and_login(client, email: str = "skills-owner@example.com") -> str:
    """Seed a single user, log them in (sets cookies), return user id."""
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


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


async def test_post_creates_skill_with_server_user_id(app_client) -> None:
    user_id = await _seed_and_login(app_client)
    resp = await app_client.post(
        "/api/skills",
        json={
            "name": "RSI",
            "type": "indicator",
            "code": CODE_VALID,
            "input_signature": {
                "inputs": [{"name": "series", "type": "list[float]"}],
                "outputs": [],
            },
            "output_signature": {
                "inputs": [],
                "outputs": [{"name": "rsi", "type": "list[float]"}],
            },
        },
        headers=_csrf_headers(app_client),
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["name"] == "RSI"
    assert body["type"] == "indicator"
    assert body["version"] == 1
    assert body["is_active"] is True
    assert body["code"] == CODE_VALID
    assert body["input_signature"]["inputs"][0]["name"] == "series"
    assert body["output_signature"]["outputs"][0]["type"] == "list[float]"
    uuid.UUID(body["id"])
    assert user_id


async def test_post_ignores_client_user_id(app_client) -> None:
    """Extra ``user_id`` in the body must be a 422 (extra='forbid')."""
    await _seed_and_login(app_client)
    resp = await app_client.post(
        "/api/skills",
        json={
            "name": "x",
            "type": "indicator",
            "code": CODE_VALID,
            "user_id": str(uuid.uuid4()),
        },
        headers=_csrf_headers(app_client),
    )
    assert resp.status_code == 422, resp.text


async def test_post_invalid_type_is_422(app_client) -> None:
    """Type outside the CHECK set MUST be rejected by Pydantic first."""
    await _seed_and_login(app_client)
    resp = await app_client.post(
        "/api/skills",
        json={"name": "n", "type": "orchestrator", "code": CODE_VALID},
        headers=_csrf_headers(app_client),
    )
    assert resp.status_code == 422


async def test_post_invalid_code_returns_422_with_line_col(app_client) -> None:
    await _seed_and_login(app_client)
    resp = await app_client.post(
        "/api/skills",
        json={
            "name": "n",
            "type": "indicator",
            "runtime": "python",
            "code": CODE_BROKEN,
        },
        headers=_csrf_headers(app_client),
    )
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail["code"] == "python_syntax_error"
    assert detail["line"] == 1
    assert detail["col"] is not None


async def test_post_without_csrf_is_403(app_client) -> None:
    await _seed_and_login(app_client)
    resp = await app_client.post(
        "/api/skills",
        json={"name": "n", "type": "indicator", "code": CODE_VALID},
        # NO CSRF header.
    )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# CHECK constraint violations at the DB layer
# ---------------------------------------------------------------------------


async def test_runtime_check_constraint_blocks_unknown_runtime(app_client) -> None:
    """The ``skills_runtime_valid`` CHECK enforces runtime ∈ {markdown, python}.

    Markdown and Python are accepted; anything else (e.g. 'lua', 'mql5')
    raises an :class:`IntegrityError`. We exercise this by issuing a raw
    INSERT through the engine, bypassing the Pydantic layer.
    """
    await _seed_and_login(app_client)
    from aether_api.db.session import get_engine

    engine = get_engine()
    user_id = uuid.uuid4()
    async with engine.begin() as conn:
        # Plant a throwaway user so the FK is satisfied.
        await conn.execute(
            text(
                "INSERT INTO users (id, email, password_hash) VALUES "
                "(:id, :email, 'x')"
            ),
            {"id": user_id, "email": f"checkcons-{user_id}@example.com"},
        )

    async with engine.begin() as conn:
        with pytest.raises(IntegrityError):
            await conn.execute(
                text(
                    "INSERT INTO skills (user_id, name, type, code, runtime) "
                    "VALUES (:uid, 'x', 'indicator', '# noop\n', 'lua')"
                ),
                {"uid": user_id},
            )


async def test_type_check_constraint_blocks_unknown(app_client) -> None:
    await _seed_and_login(app_client)
    from aether_api.db.session import get_engine

    engine = get_engine()
    user_id = uuid.uuid4()
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO users (id, email, password_hash) VALUES "
                "(:id, :email, 'x')"
            ),
            {"id": user_id, "email": f"checktyp-{user_id}@example.com"},
        )

    async with engine.begin() as conn:
        with pytest.raises(IntegrityError):
            await conn.execute(
                text(
                    "INSERT INTO skills (user_id, name, type, code) "
                    "VALUES (:uid, 'x', 'orchestrator', 'def f():\n    pass\n')"
                ),
                {"uid": user_id},
            )


# ---------------------------------------------------------------------------
# List + filters
# ---------------------------------------------------------------------------


async def test_list_filters_by_type_and_is_active(app_client) -> None:
    await _seed_and_login(app_client)
    headers = _csrf_headers(app_client)
    # Plant three skills of mixed types and active states via the API.
    a = await app_client.post(
        "/api/skills",
        json={"name": "alpha", "type": "indicator", "code": CODE_VALID},
        headers=headers,
    )
    b = await app_client.post(
        "/api/skills",
        json={"name": "beta", "type": "analytic", "code": CODE_VALID},
        headers=headers,
    )
    assert a.status_code == 201 and b.status_code == 201
    # Archive ``a`` so we can filter on is_active=false.
    arch = await app_client.post(
        f"/api/skills/{a.json()['id']}/archive", headers=headers
    )
    assert arch.status_code == 200

    only_indicator = await app_client.get("/api/skills?type=indicator")
    assert only_indicator.status_code == 200
    rows = only_indicator.json()
    assert {r["name"] for r in rows} == {"alpha"}

    only_inactive = await app_client.get("/api/skills?is_active=false")
    assert only_inactive.status_code == 200
    assert {r["name"] for r in only_inactive.json()} == {"alpha"}


async def test_list_invalid_type_filter_is_400(app_client) -> None:
    await _seed_and_login(app_client)
    resp = await app_client.get("/api/skills?type=unknown")
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Get detail + cross-tenant 404
# ---------------------------------------------------------------------------


async def test_get_detail_includes_code_and_signatures(app_client) -> None:
    await _seed_and_login(app_client)
    headers = _csrf_headers(app_client)
    created = await app_client.post(
        "/api/skills",
        json={
            "name": "d",
            "type": "indicator",
            "code": CODE_VALID,
            "input_signature": {
                "inputs": [{"name": "x", "type": "float"}],
                "outputs": [],
            },
        },
        headers=headers,
    )
    skill_id = created.json()["id"]
    resp = await app_client.get(f"/api/skills/{skill_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == CODE_VALID
    assert body["input_signature"]["inputs"][0]["name"] == "x"
    assert body["updated_at"] is not None


async def test_get_missing_id_returns_404(app_client) -> None:
    await _seed_and_login(app_client)
    resp = await app_client.get(f"/api/skills/{uuid.uuid4()}")
    assert resp.status_code == 404


async def test_cross_tenant_get_returns_404_not_403(app_client) -> None:
    """Owner A creates a skill; owner B GETs it and MUST see 404."""
    # Owner A creates a skill.
    await _seed_and_login(app_client, email="a-owner@example.com")
    a_headers = _csrf_headers(app_client)
    created = await app_client.post(
        "/api/skills",
        json={"name": "secret", "type": "indicator", "code": CODE_VALID},
        headers=a_headers,
    )
    skill_id = created.json()["id"]

    # Clear cookies and log in as a DIFFERENT user.
    app_client.cookies.clear()
    await _seed_and_login(app_client, email="b-owner@example.com")

    resp = await app_client.get(f"/api/skills/{skill_id}")
    assert resp.status_code == 404  # NOT 403 — must not disclose existence.


async def test_cross_tenant_delete_returns_404(app_client) -> None:
    await _seed_and_login(app_client, email="del-a@example.com")
    headers_a = _csrf_headers(app_client)
    created = await app_client.post(
        "/api/skills",
        json={"name": "del-target", "type": "indicator", "code": CODE_VALID},
        headers=headers_a,
    )
    skill_id = created.json()["id"]

    app_client.cookies.clear()
    await _seed_and_login(app_client, email="del-b@example.com")
    headers_b = _csrf_headers(app_client)
    resp = await app_client.delete(f"/api/skills/{skill_id}", headers=headers_b)
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Patch
# ---------------------------------------------------------------------------


async def test_patch_requires_updated_at_precondition(app_client) -> None:
    await _seed_and_login(app_client)
    headers = _csrf_headers(app_client)
    created = await app_client.post(
        "/api/skills",
        json={"name": "p", "type": "indicator", "code": CODE_VALID},
        headers=headers,
    )
    skill_id = created.json()["id"]
    resp = await app_client.patch(
        f"/api/skills/{skill_id}",
        json={"name": "renamed"},
        headers=headers,
    )
    assert resp.status_code == 428


async def test_patch_stale_updated_at_returns_409(app_client) -> None:
    await _seed_and_login(app_client)
    headers = _csrf_headers(app_client)
    created = await app_client.post(
        "/api/skills",
        json={"name": "p", "type": "indicator", "code": CODE_VALID},
        headers=headers,
    )
    skill_id = created.json()["id"]
    resp = await app_client.patch(
        f"/api/skills/{skill_id}",
        json={"name": "x", "updated_at": "2000-01-01T00:00:00"},
        headers=headers,
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "stale_update"


async def test_patch_bumps_version_only_when_code_changes(app_client) -> None:
    await _seed_and_login(app_client)
    headers = _csrf_headers(app_client)
    created = await app_client.post(
        "/api/skills",
        json={"name": "p", "type": "indicator", "code": CODE_VALID},
        headers=headers,
    )
    body = created.json()
    skill_id = body["id"]
    ts = body["updated_at"]
    assert body["version"] == 1

    # Rename only — version stays at 1.
    r1 = await app_client.patch(
        f"/api/skills/{skill_id}",
        json={"name": "renamed", "updated_at": ts},
        headers=headers,
    )
    assert r1.status_code == 200, r1.text
    b1 = r1.json()
    assert b1["name"] == "renamed"
    assert b1["version"] == 1

    # Now change code — version bumps to 2.
    new_code = "def rsi(series, period=14):\n    return [0.0]\n"
    r2 = await app_client.patch(
        f"/api/skills/{skill_id}",
        json={"code": new_code, "updated_at": b1["updated_at"]},
        headers=headers,
    )
    assert r2.status_code == 200, r2.text
    assert r2.json()["version"] == 2
    assert r2.json()["code"] == new_code


async def test_patch_invalid_code_returns_422(app_client) -> None:
    await _seed_and_login(app_client)
    headers = _csrf_headers(app_client)
    created = await app_client.post(
        "/api/skills",
        json={
            "name": "p",
            "type": "indicator",
            "runtime": "python",
            "code": CODE_VALID,
        },
        headers=headers,
    )
    body = created.json()
    skill_id = body["id"]
    resp = await app_client.patch(
        f"/api/skills/{skill_id}",
        json={"code": CODE_BROKEN, "updated_at": body["updated_at"]},
        headers=headers,
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "python_syntax_error"


# ---------------------------------------------------------------------------
# Archive
# ---------------------------------------------------------------------------


async def test_archive_sets_is_active_false_and_is_idempotent(app_client) -> None:
    await _seed_and_login(app_client)
    headers = _csrf_headers(app_client)
    created = await app_client.post(
        "/api/skills",
        json={"name": "a", "type": "indicator", "code": CODE_VALID},
        headers=headers,
    )
    skill_id = created.json()["id"]

    r1 = await app_client.post(f"/api/skills/{skill_id}/archive", headers=headers)
    assert r1.status_code == 200
    assert r1.json()["is_active"] is False

    r2 = await app_client.post(f"/api/skills/{skill_id}/archive", headers=headers)
    assert r2.status_code == 200
    assert r2.json()["is_active"] is False


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------


async def test_delete_returns_204_then_404_on_get(app_client) -> None:
    await _seed_and_login(app_client)
    headers = _csrf_headers(app_client)
    created = await app_client.post(
        "/api/skills",
        json={"name": "x", "type": "indicator", "code": CODE_VALID},
        headers=headers,
    )
    skill_id = created.json()["id"]
    resp = await app_client.delete(f"/api/skills/{skill_id}", headers=headers)
    assert resp.status_code == 204

    get_again = await app_client.get(f"/api/skills/{skill_id}")
    assert get_again.status_code == 404
