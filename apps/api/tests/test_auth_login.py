"""Login flow: success, wrong password, lockout, case-insensitive email."""

from __future__ import annotations

import pytest
from aether_api.auth.cookies import ACCESS_COOKIE, CSRF_COOKIE, REFRESH_COOKIE

pytestmark = pytest.mark.integration


async def _seed(client):
    from aether_api.db.session import get_session_maker

    from tests._helpers import seed_user

    maker = get_session_maker()
    async with maker() as session:
        user = await seed_user(
            session, email="alice@example.com", password="correct horse battery staple"
        )
        await session.commit()
        return user


async def test_login_success_sets_three_cookies(app_client):
    await _seed(app_client)
    resp = await app_client.post(
        "/api/auth/login",
        json={"email": "alice@example.com", "password": "correct horse battery staple"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["user"]["email"] == "alice@example.com"

    cookies = {c.name for c in resp.cookies.jar}
    assert ACCESS_COOKIE in cookies
    assert REFRESH_COOKIE in cookies
    assert CSRF_COOKIE in cookies


async def test_login_wrong_password_no_cookies(app_client):
    await _seed(app_client)
    resp = await app_client.post(
        "/api/auth/login",
        json={"email": "alice@example.com", "password": "WRONG"},
    )
    assert resp.status_code == 401
    cookies = {c.name for c in resp.cookies.jar}
    assert ACCESS_COOKIE not in cookies
    assert REFRESH_COOKIE not in cookies


async def test_lockout_after_threshold(app_client):
    """Five wrong attempts → sixth login (even with the right pw) returns 401."""
    from aether_api.core.settings import get_settings

    await _seed(app_client)
    threshold = get_settings().lockout_threshold

    for _ in range(threshold):
        bad = await app_client.post(
            "/api/auth/login",
            json={"email": "alice@example.com", "password": "WRONG"},
        )
        assert bad.status_code == 401

    # Now even the right password should be refused — lockout window in play.
    resp = await app_client.post(
        "/api/auth/login",
        json={"email": "alice@example.com", "password": "correct horse battery staple"},
    )
    assert resp.status_code == 401


async def test_email_case_insensitive(app_client):
    await _seed(app_client)
    resp = await app_client.post(
        "/api/auth/login",
        json={"email": "ALICE@Example.com", "password": "correct horse battery staple"},
    )
    assert resp.status_code == 200, resp.text
