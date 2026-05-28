"""``/api/me`` — self-service profile / credentials / sessions.

The smoke surface here mirrors the spec scenarios in
``sdd/settings-profile/spec`` (#1973):

* PATCH /api/me updates display_name / avatar_url and rejects unknown fields.
* CSRF mismatch returns 403.
* Email change verifies the password, normalises to lowercase, sets
  ``email_verified_at = NULL``, and rejects duplicates with 409.
* Password change is transactional — sign_out_others revokes only OTHER
  rows AND a new session is issued for the caller.
* Sessions list paginates with an opaque cursor and exposes ``is_current``
  derived server-side from the refresh cookie.
* Revoking the caller's current session returns 400 ``use_logout_instead``.
* Cross-tenant attempts at revoking another user's session return 404 (NEVER 403).
"""

from __future__ import annotations

import pytest
from aether_api.auth.cookies import (
    ACCESS_COOKIE,
    CSRF_COOKIE,
    REFRESH_COOKIE,
)
from aether_api.auth.tokens import hash_refresh_token
from aether_api.models.session import UserSession
from sqlalchemy import select

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
async def _seed_and_login(
    client,
    *,
    email: str = "me@example.com",
    password: str = "supersecret123",
    display_name: str | None = None,
):
    from aether_api.db.session import get_session_maker

    from tests._helpers import seed_user

    maker = get_session_maker()
    async with maker() as session:
        user = await seed_user(
            session,
            email=email,
            password=password,
            display_name=display_name,
        )
        await session.commit()
        user_id = user.id
    resp = await client.post(
        "/api/auth/login", json={"email": email, "password": password}
    )
    assert resp.status_code == 200, resp.text
    return user_id


def _csrf_headers(client) -> dict[str, str]:
    token = client.cookies.get(CSRF_COOKIE)
    assert token, "csrf cookie must be present after login"
    return {"X-CSRF-Token": token}


# ---------------------------------------------------------------------------
# PATCH /api/me
# ---------------------------------------------------------------------------
async def test_patch_me_updates_display_name(app_client):
    await _seed_and_login(app_client, display_name="Alice")
    resp = await app_client.patch(
        "/api/me",
        json={"display_name": "Alice Cooper"},
        headers=_csrf_headers(app_client),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["display_name"] == "Alice Cooper"


async def test_patch_me_rejects_unknown_field(app_client):
    await _seed_and_login(app_client)
    resp = await app_client.patch(
        "/api/me",
        json={"is_admin": True},
        headers=_csrf_headers(app_client),
    )
    assert resp.status_code == 422


async def test_patch_me_rejects_non_http_avatar_url(app_client):
    await _seed_and_login(app_client)
    resp = await app_client.patch(
        "/api/me",
        json={"avatar_url": "javascript:alert(1)"},
        headers=_csrf_headers(app_client),
    )
    assert resp.status_code == 422


async def test_patch_me_without_csrf_is_403(app_client):
    await _seed_and_login(app_client)
    resp = await app_client.patch("/api/me", json={"display_name": "Z"})
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# POST /api/me/email/change
# ---------------------------------------------------------------------------
async def test_change_email_happy_path(app_client):
    await _seed_and_login(app_client, email="old@example.com")
    resp = await app_client.post(
        "/api/me/email/change",
        json={"new_email": "NEW@Example.com", "current_password": "supersecret123"},
        headers=_csrf_headers(app_client),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["email"] == "new@example.com"
    assert body["email_verified_at"] is None


async def test_change_email_wrong_password_is_401(app_client):
    await _seed_and_login(app_client)
    resp = await app_client.post(
        "/api/me/email/change",
        json={"new_email": "x@example.com", "current_password": "wrongwrongwrong"},
        headers=_csrf_headers(app_client),
    )
    assert resp.status_code == 401


async def test_change_email_duplicate_returns_409(app_client):
    # Pre-seed a second user whose email we'll try to claim.
    from aether_api.db.session import get_session_maker

    from tests._helpers import seed_user

    maker = get_session_maker()
    async with maker() as session:
        await seed_user(session, email="taken@example.com", password="zzzzzzzzzzzz")
        await session.commit()

    await _seed_and_login(app_client, email="me2@example.com")
    resp = await app_client.post(
        "/api/me/email/change",
        json={"new_email": "taken@example.com", "current_password": "supersecret123"},
        headers=_csrf_headers(app_client),
    )
    assert resp.status_code == 409


# ---------------------------------------------------------------------------
# POST /api/me/password/change
# ---------------------------------------------------------------------------
async def test_change_password_rotates_session_and_revokes_others(app_client):
    """Password change with sign_out_others=True must:

    * verify current pw,
    * rehash + update,
    * revoke OTHER active sessions (not the caller's current one),
    * rotate the caller's current session (new refresh cookie + DB row).
    """
    from aether_api.db.session import get_session_maker

    user_id = await _seed_and_login(app_client)
    old_refresh = app_client.cookies.get(REFRESH_COOKIE)
    assert old_refresh

    # Pre-seed an OTHER active session for the same user. We don't drive
    # this through the login endpoint (that would clobber the test
    # client's cookie jar); we just insert a row directly.
    maker = get_session_maker()
    from datetime import datetime, timedelta

    async with maker() as session:
        other = UserSession(
            user_id=user_id,
            refresh_token_hash="0" * 64,
            expires_at=datetime.utcnow() + timedelta(days=14),
            ip_address=None,
            user_agent="other-device",
        )
        session.add(other)
        await session.commit()
        other_id = other.id

    resp = await app_client.post(
        "/api/me/password/change",
        json={
            "current_password": "supersecret123",
            "new_password": "brandnewpass456",
            "sign_out_others": True,
        },
        headers=_csrf_headers(app_client),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert body["revoked_other_sessions"] == 1

    # New cookies were minted (rotation).
    new_cookies = {c.name for c in resp.cookies.jar}
    assert ACCESS_COOKIE in new_cookies
    assert REFRESH_COOKIE in new_cookies
    assert CSRF_COOKIE in new_cookies

    # The caller's OLD session row is revoked AND the OTHER device's row
    # is revoked too — but a fresh row exists for the caller.
    async with maker() as session:
        old_row = (
            await session.execute(
                select(UserSession).where(
                    UserSession.refresh_token_hash == hash_refresh_token(old_refresh)
                )
            )
        ).scalar_one()
        assert old_row.revoked_at is not None

        other_row = await session.get(UserSession, other_id)
        assert other_row is not None
        assert other_row.revoked_at is not None

        # And the NEW row is alive.
        alive = (
            await session.execute(
                select(UserSession)
                .where(UserSession.user_id == user_id)
                .where(UserSession.revoked_at.is_(None))
            )
        ).scalars().all()
        assert len(alive) == 1
        assert alive[0].id not in {old_row.id, other_id}


async def test_change_password_wrong_current_is_401_and_no_writes(app_client):
    """A bad current_password must NOT touch users.password_hash or sessions."""
    from aether_api.db.session import get_session_maker

    user_id = await _seed_and_login(app_client)
    maker = get_session_maker()

    # Snapshot the hash + session count.
    async with maker() as session:
        from aether_api.models.user import User

        before_user = await session.get(User, user_id)
        assert before_user is not None
        before_hash = before_user.password_hash
        before_active = (
            await session.execute(
                select(UserSession)
                .where(UserSession.user_id == user_id)
                .where(UserSession.revoked_at.is_(None))
            )
        ).scalars().all()
        before_alive_count = len(before_active)

    resp = await app_client.post(
        "/api/me/password/change",
        json={
            "current_password": "WRONGWRONGWRONG",
            "new_password": "doesntmatter000",
            "sign_out_others": True,
        },
        headers=_csrf_headers(app_client),
    )
    assert resp.status_code == 401

    async with maker() as session:
        from aether_api.models.user import User

        after_user = await session.get(User, user_id)
        assert after_user is not None
        assert after_user.password_hash == before_hash
        after_active = (
            await session.execute(
                select(UserSession)
                .where(UserSession.user_id == user_id)
                .where(UserSession.revoked_at.is_(None))
            )
        ).scalars().all()
        assert len(after_active) == before_alive_count


# ---------------------------------------------------------------------------
# GET /api/me/sessions
# ---------------------------------------------------------------------------
async def test_list_sessions_marks_current(app_client):
    await _seed_and_login(app_client)
    resp = await app_client.get("/api/me/sessions")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["items"]) == 1
    assert body["items"][0]["is_current"] is True


async def test_list_sessions_invalid_cursor_is_400(app_client):
    await _seed_and_login(app_client)
    resp = await app_client.get("/api/me/sessions?cursor=not-base64!!!")
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# POST /api/me/sessions/{id}/revoke
# ---------------------------------------------------------------------------
async def test_revoke_current_session_returns_400_use_logout(app_client):
    await _seed_and_login(app_client)
    sessions = (await app_client.get("/api/me/sessions")).json()["items"]
    current = next(s for s in sessions if s["is_current"])

    resp = await app_client.post(
        f"/api/me/sessions/{current['id']}/revoke",
        headers=_csrf_headers(app_client),
    )
    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert isinstance(detail, dict)
    assert detail["code"] == "use_logout_instead"


async def test_revoke_other_users_session_returns_404(app_client):
    """Cross-tenant denial returns 404, never 403."""
    from aether_api.db.session import get_session_maker

    from tests._helpers import seed_user

    # Seed a second user with their own session row.
    maker = get_session_maker()
    from datetime import datetime, timedelta

    async with maker() as session:
        other_user = await seed_user(
            session, email="other@example.com", password="passpasspass"
        )
        other_session = UserSession(
            user_id=other_user.id,
            refresh_token_hash="1" * 64,
            expires_at=datetime.utcnow() + timedelta(days=14),
            ip_address=None,
            user_agent="other-user-device",
        )
        session.add(other_session)
        await session.commit()
        target_id = other_session.id

    await _seed_and_login(app_client, email="caller@example.com")
    resp = await app_client.post(
        f"/api/me/sessions/{target_id}/revoke",
        headers=_csrf_headers(app_client),
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/me/sessions/revoke-others
# ---------------------------------------------------------------------------
async def test_revoke_others_keeps_current_session(app_client):
    from aether_api.db.session import get_session_maker

    user_id = await _seed_and_login(app_client)
    maker = get_session_maker()
    from datetime import datetime, timedelta

    async with maker() as session:
        for i in range(3):
            session.add(
                UserSession(
                    user_id=user_id,
                    refresh_token_hash=str(i + 2) * 64,
                    expires_at=datetime.utcnow() + timedelta(days=14),
                    ip_address=None,
                    user_agent=f"device-{i}",
                )
            )
        await session.commit()

    resp = await app_client.post(
        "/api/me/sessions/revoke-others",
        headers=_csrf_headers(app_client),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["revoked"] == 3

    # Exactly one active row remains — the caller's.
    async with maker() as session:
        alive = (
            await session.execute(
                select(UserSession)
                .where(UserSession.user_id == user_id)
                .where(UserSession.revoked_at.is_(None))
            )
        ).scalars().all()
        assert len(alive) == 1
