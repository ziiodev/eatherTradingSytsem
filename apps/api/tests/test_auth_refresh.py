"""Refresh-token rotation: REVOKE-OLD + INSERT-NEW.

Asserts both the wire behaviour (new cookies issued) and the DB state
(old session row marked revoked).
"""

from __future__ import annotations

import pytest
from aether_api.auth.cookies import ACCESS_COOKIE, CSRF_COOKIE, REFRESH_COOKIE
from aether_api.auth.tokens import hash_refresh_token
from aether_api.models.session import UserSession
from sqlalchemy import select

pytestmark = pytest.mark.integration


async def _login(client, email="bob@example.com", password="hunter22hunter22"):
    from aether_api.db.session import get_session_maker

    from tests._helpers import seed_user

    maker = get_session_maker()
    async with maker() as session:
        await seed_user(session, email=email, password=password)
        await session.commit()

    resp = await client.post("/api/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.text
    return resp


def _csrf_header(client):
    csrf_value = client.cookies.get(CSRF_COOKIE)
    return {"X-CSRF-Token": csrf_value} if csrf_value else {}


async def test_refresh_rotates_and_revokes(app_client):
    login_resp = await _login(app_client)
    old_refresh = login_resp.cookies.get(REFRESH_COOKIE)
    assert old_refresh

    headers = _csrf_header(app_client)
    refresh_resp = await app_client.post("/api/auth/refresh", headers=headers)
    assert refresh_resp.status_code == 200, refresh_resp.text

    # New access + refresh + csrf cookies were minted.
    new_cookies = {c.name for c in refresh_resp.cookies.jar}
    assert ACCESS_COOKIE in new_cookies
    assert REFRESH_COOKIE in new_cookies
    assert CSRF_COOKIE in new_cookies

    # Old session row was revoked.
    from aether_api.db.session import get_session_maker

    maker = get_session_maker()
    async with maker() as session:
        stmt = select(UserSession).where(
            UserSession.refresh_token_hash == hash_refresh_token(old_refresh)
        )
        row = (await session.execute(stmt)).scalar_one()
        assert row.revoked_at is not None, "old session row should be revoked"


async def test_refresh_of_revoked_token_clears_cookies(app_client):
    login_resp = await _login(app_client)
    # Capture the FIRST refresh value before any rotation. We'll replay it
    # below to prove the server treats a revoked token as 401.
    old_refresh = login_resp.cookies.get(REFRESH_COOKIE)
    assert old_refresh
    headers = _csrf_header(app_client)

    first = await app_client.post("/api/auth/refresh", headers=headers)
    assert first.status_code == 200

    # The OLD refresh cookie is now revoked server-side. The client jar
    # has already moved on to the new (post-rotation) cookie. Force the
    # OLD value back in and replay — the server MUST respond 401 (revoked)
    # rather than 200 (would let stolen tokens keep refreshing).
    csrf_value = app_client.cookies.get(CSRF_COOKIE)
    assert csrf_value, "CSRF cookie must still be set after successful refresh"
    app_client.cookies.set(REFRESH_COOKIE, old_refresh)
    resp = await app_client.post(
        "/api/auth/refresh", headers={"X-CSRF-Token": csrf_value}
    )
    assert resp.status_code == 401, resp.text
