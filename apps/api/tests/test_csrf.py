"""Double-submit CSRF: refresh / logout / signup require matching header."""

from __future__ import annotations

import pytest
from aether_api.auth.cookies import CSRF_COOKIE

pytestmark = pytest.mark.integration


async def _login(client, email="carol@example.com", password="testtesttesttest"):
    from aether_api.db.session import get_session_maker

    from tests._helpers import seed_user

    maker = get_session_maker()
    async with maker() as session:
        await seed_user(session, email=email, password=password)
        await session.commit()
    resp = await client.post("/api/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200
    return resp


async def test_refresh_without_csrf_header_is_403(app_client):
    await _login(app_client)
    # Cookie present; header absent → 403.
    resp = await app_client.post("/api/auth/refresh")
    assert resp.status_code == 403


async def test_refresh_with_mismatched_csrf_header_is_403(app_client):
    await _login(app_client)
    cookie_value = app_client.cookies.get(CSRF_COOKIE)
    assert cookie_value
    resp = await app_client.post(
        "/api/auth/refresh",
        headers={"X-CSRF-Token": cookie_value + "TAMPERED"},
    )
    assert resp.status_code == 403
