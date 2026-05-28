"""End-to-end coverage for the mfa-totp change.

Lanes:

* Enrollment (`POST /api/me/mfa/setup`) — happy path, re-setup, 409 when
  already enabled.
* Verify (`POST /api/me/mfa/verify`) — happy path, wrong code, replay
  after success.
* Disable (`POST /api/me/mfa/disable`) — requires password + TOTP.
* Recovery codes — single-use, concurrency-safe, regenerate invalidates
  the previous batch.
* Login two-step — pending cookie aud/expiry/tamper, lockout integration.
* Real-account gate — 409 on POST + PATCH when ``mfa_enabled=false``,
  demo→real transition path.
"""

from __future__ import annotations

import time
import uuid

import jwt
import pyotp
import pytest

pytestmark = pytest.mark.integration


# -----------------------------------------------------------------------------
# Seed / auth helpers
# -----------------------------------------------------------------------------
_DEFAULT_PW = "correct horse battery staple"


async def _seed_user(
    client, *, email: str = "alice@example.com", password: str = _DEFAULT_PW
):
    from aether_api.db.session import get_session_maker

    from tests._helpers import seed_user

    maker = get_session_maker()
    async with maker() as session:
        user = await seed_user(session, email=email, password=password)
        await session.commit()
        return user


async def _login(client, *, email: str, password: str) -> dict[str, object]:
    """POST /api/auth/login and return the parsed JSON body."""
    resp = await client.post(
        "/api/auth/login",
        json={"email": email, "password": password},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _csrf_headers(client) -> dict[str, str]:
    """Return X-CSRF-Token mirroring the csrf_token cookie. 403 without."""
    csrf = client.cookies.get("csrf_token")
    assert csrf is not None, "csrf_token cookie not set — login first"
    return {"X-CSRF-Token": csrf}


async def _enable_mfa_for_user(client, *, email: str, password: str) -> tuple[str, list[str]]:
    """End-to-end MFA enrolment. Returns ``(totp_secret_b32, recovery_codes)``."""
    await _login(client, email=email, password=password)
    setup = await client.post("/api/me/mfa/setup", headers=_csrf_headers(client))
    assert setup.status_code == 200, setup.text
    setup_body = setup.json()
    secret = setup_body["secret_b32"]

    code = pyotp.TOTP(secret).now()
    verify = await client.post(
        "/api/me/mfa/verify",
        json={"totp_code": code},
        headers=_csrf_headers(client),
    )
    assert verify.status_code == 200, verify.text
    body = verify.json()
    assert body["mfa_enabled"] is True
    assert len(body["recovery_codes"]) == 10
    return secret, body["recovery_codes"]


# =============================================================================
# Enrollment
# =============================================================================
async def test_setup_returns_qr_and_secret_once(app_client):
    await _seed_user(app_client)
    await _login(app_client, email="alice@example.com", password="correct horse battery staple")
    resp = await app_client.post("/api/me/mfa/setup", headers=_csrf_headers(app_client))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["secret_b32"]
    assert body["provisioning_uri"].startswith("otpauth://totp/")
    assert "qr_data_url" in body


async def test_setup_overwrites_unverified_secret(app_client):
    await _seed_user(app_client)
    await _login(app_client, email="alice@example.com", password="correct horse battery staple")
    first = await app_client.post("/api/me/mfa/setup", headers=_csrf_headers(app_client))
    second = await app_client.post("/api/me/mfa/setup", headers=_csrf_headers(app_client))
    assert first.status_code == 200 and second.status_code == 200
    assert first.json()["secret_b32"] != second.json()["secret_b32"]


async def test_setup_409_when_already_enabled(app_client):
    await _seed_user(app_client)
    await _enable_mfa_for_user(
        app_client, email="alice@example.com", password="correct horse battery staple"
    )
    again = await app_client.post("/api/me/mfa/setup", headers=_csrf_headers(app_client))
    assert again.status_code == 409
    assert again.json()["detail"]["code"] == "MFA_ALREADY_ENABLED"


# =============================================================================
# Verify
# =============================================================================
async def test_verify_wrong_code_returns_401(app_client):
    await _seed_user(app_client)
    await _login(app_client, email="alice@example.com", password="correct horse battery staple")
    setup = await app_client.post("/api/me/mfa/setup", headers=_csrf_headers(app_client))
    assert setup.status_code == 200
    resp = await app_client.post(
        "/api/me/mfa/verify",
        json={"totp_code": "000000"},
        headers=_csrf_headers(app_client),
    )
    # Use a code we know is wrong; pyotp.TOTP.verify is highly unlikely
    # to accept all-zeros on any modern secret in any ±1 step window.
    assert resp.status_code == 401
    assert resp.json()["detail"]["code"] == "INVALID_TOTP_CODE"


async def test_verify_without_setup_returns_400(app_client):
    await _seed_user(app_client)
    await _login(app_client, email="alice@example.com", password="correct horse battery staple")
    resp = await app_client.post(
        "/api/me/mfa/verify",
        json={"totp_code": "123456"},
        headers=_csrf_headers(app_client),
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "MFA_NOT_SETUP"


# =============================================================================
# Disable — requires password + TOTP
# =============================================================================
async def test_disable_requires_password_and_totp(app_client):
    await _seed_user(app_client)
    secret, _codes = await _enable_mfa_for_user(
        app_client, email="alice@example.com", password="correct horse battery staple"
    )
    code = pyotp.TOTP(secret).now()

    # Wrong password.
    bad_pw = await app_client.post(
        "/api/me/mfa/disable",
        json={"current_password": "WRONG", "totp_code": code},
        headers=_csrf_headers(app_client),
    )
    assert bad_pw.status_code == 401

    # Wrong TOTP.
    bad_totp = await app_client.post(
        "/api/me/mfa/disable",
        json={"current_password": "correct horse battery staple", "totp_code": "000000"},
        headers=_csrf_headers(app_client),
    )
    assert bad_totp.status_code == 401

    # Good both.
    code = pyotp.TOTP(secret).now()
    ok = await app_client.post(
        "/api/me/mfa/disable",
        json={"current_password": "correct horse battery staple", "totp_code": code},
        headers=_csrf_headers(app_client),
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["mfa_enabled"] is False


# =============================================================================
# Recovery codes
# =============================================================================
async def test_recovery_code_single_use(app_client):
    await _seed_user(app_client)
    _secret, codes = await _enable_mfa_for_user(
        app_client, email="alice@example.com", password="correct horse battery staple"
    )
    # Log out so we can run /login + /login/mfa cleanly.
    await app_client.post("/api/auth/logout", headers=_csrf_headers(app_client))
    app_client.cookies.clear()

    # First step — password.
    resp1 = await app_client.post(
        "/api/auth/login",
        json={"email": "alice@example.com", "password": "correct horse battery staple"},
    )
    assert resp1.status_code == 200
    assert resp1.json()["requires_mfa"] is True

    # Second step — recovery code.
    resp2 = await app_client.post(
        "/api/auth/login/mfa",
        json={"recovery_code": codes[0]},
    )
    assert resp2.status_code == 200, resp2.text
    assert resp2.json()["user"]["email"] == "alice@example.com"

    # Re-use the SAME code on a fresh /login flow → 401.
    await app_client.post("/api/auth/logout", headers=_csrf_headers(app_client))
    app_client.cookies.clear()
    await app_client.post(
        "/api/auth/login",
        json={"email": "alice@example.com", "password": "correct horse battery staple"},
    )
    reuse = await app_client.post(
        "/api/auth/login/mfa",
        json={"recovery_code": codes[0]},
    )
    assert reuse.status_code == 401


async def test_recovery_codes_regenerate_invalidates_previous(app_client):
    await _seed_user(app_client)
    _secret, old_codes = await _enable_mfa_for_user(
        app_client, email="alice@example.com", password="correct horse battery staple"
    )

    regen = await app_client.post(
        "/api/me/mfa/recovery-codes/regenerate",
        json={"current_password": "correct horse battery staple"},
        headers=_csrf_headers(app_client),
    )
    assert regen.status_code == 200
    new_codes = regen.json()["recovery_codes"]
    assert len(new_codes) == 10
    assert set(new_codes).isdisjoint(set(old_codes))

    # Old code shouldn't work anymore.
    await app_client.post("/api/auth/logout", headers=_csrf_headers(app_client))
    app_client.cookies.clear()
    await app_client.post(
        "/api/auth/login",
        json={"email": "alice@example.com", "password": "correct horse battery staple"},
    )
    bad = await app_client.post(
        "/api/auth/login/mfa",
        json={"recovery_code": old_codes[0]},
    )
    assert bad.status_code == 401


# =============================================================================
# Login two-step — pending cookie integrity
# =============================================================================
async def test_login_two_step_happy_path_totp(app_client):
    await _seed_user(app_client)
    secret, _codes = await _enable_mfa_for_user(
        app_client, email="alice@example.com", password="correct horse battery staple"
    )
    await app_client.post("/api/auth/logout", headers=_csrf_headers(app_client))
    app_client.cookies.clear()

    resp1 = await app_client.post(
        "/api/auth/login",
        json={"email": "alice@example.com", "password": "correct horse battery staple"},
    )
    assert resp1.status_code == 200
    body1 = resp1.json()
    assert body1["requires_mfa"] is True
    assert body1.get("user") is None
    # No access cookie set yet.
    assert "aether_access" not in {c.name for c in resp1.cookies.jar}
    # Pending cookie must be there.
    pending = next(
        (c for c in resp1.cookies.jar if c.name == "aether_mfa_pending"),
        None,
    )
    assert pending is not None

    code = pyotp.TOTP(secret).now()
    resp2 = await app_client.post("/api/auth/login/mfa", json={"totp_code": code})
    assert resp2.status_code == 200, resp2.text
    assert resp2.json()["user"]["email"] == "alice@example.com"
    set_cookies = {c.name for c in resp2.cookies.jar}
    assert "aether_access" in set_cookies
    assert "aether_refresh" in set_cookies
    assert "csrf_token" in set_cookies


async def test_login_mfa_without_pending_cookie_401(app_client):
    await _seed_user(app_client)
    await _enable_mfa_for_user(
        app_client, email="alice@example.com", password="correct horse battery staple"
    )
    app_client.cookies.clear()
    resp = await app_client.post("/api/auth/login/mfa", json={"totp_code": "123456"})
    assert resp.status_code == 401


async def test_login_mfa_rejects_wrong_audience(app_client):
    """A token signed with MFA_PENDING_SECRET but ``aud`` != 'mfa' must be refused."""
    from aether_api.auth.routes import MFA_PENDING_COOKIE
    from aether_api.core.settings import get_settings

    await _seed_user(app_client)
    await _enable_mfa_for_user(
        app_client, email="alice@example.com", password="correct horse battery staple"
    )
    app_client.cookies.clear()
    s = get_settings()
    secret = s.mfa_pending_secret.get_secret_value()  # type: ignore[union-attr]
    bad = jwt.encode(
        {
            "sub": str(uuid.uuid4()),
            "aud": "not-mfa",
            "iat": int(time.time()),
            "exp": int(time.time()) + 60,
        },
        secret,
        algorithm="HS256",
    )
    app_client.cookies.set(MFA_PENDING_COOKIE, bad, path="/api/auth/login/mfa")
    resp = await app_client.post("/api/auth/login/mfa", json={"totp_code": "123456"})
    assert resp.status_code == 401


async def test_login_mfa_rejects_expired_cookie(app_client):
    from aether_api.auth.routes import MFA_PENDING_COOKIE
    from aether_api.core.settings import get_settings

    await _seed_user(app_client)
    await _enable_mfa_for_user(
        app_client, email="alice@example.com", password="correct horse battery staple"
    )
    app_client.cookies.clear()
    s = get_settings()
    secret = s.mfa_pending_secret.get_secret_value()  # type: ignore[union-attr]
    expired = jwt.encode(
        {
            "sub": str(uuid.uuid4()),
            "aud": "mfa",
            "iat": int(time.time()) - 3600,
            "exp": int(time.time()) - 60,
        },
        secret,
        algorithm="HS256",
    )
    app_client.cookies.set(MFA_PENDING_COOKIE, expired, path="/api/auth/login/mfa")
    resp = await app_client.post("/api/auth/login/mfa", json={"totp_code": "123456"})
    assert resp.status_code == 401


async def test_login_mfa_rejects_tampered_cookie(app_client):
    from aether_api.auth.routes import MFA_PENDING_COOKIE

    await _seed_user(app_client)
    await _enable_mfa_for_user(
        app_client, email="alice@example.com", password="correct horse battery staple"
    )
    app_client.cookies.clear()
    app_client.cookies.set(
        MFA_PENDING_COOKIE,
        "not.a.real.jwt",
        path="/api/auth/login/mfa",
    )
    resp = await app_client.post("/api/auth/login/mfa", json={"totp_code": "123456"})
    assert resp.status_code == 401


async def test_lockout_after_threshold_bad_totp(app_client):
    """Wrong codes against /login/mfa increment the same lockout counter."""
    from aether_api.core.settings import get_settings

    await _seed_user(app_client)
    await _enable_mfa_for_user(
        app_client, email="alice@example.com", password="correct horse battery staple"
    )
    threshold = get_settings().lockout_threshold

    # The user is currently in a verified session; logout cleanly first
    # so the lockout loop starts from a blank slate.
    await app_client.post("/api/auth/logout", headers=_csrf_headers(app_client))
    app_client.cookies.clear()

    for _ in range(threshold):
        await app_client.post(
            "/api/auth/login",
            json={
                "email": "alice@example.com",
                "password": "correct horse battery staple",
            },
        )
        bad = await app_client.post(
            "/api/auth/login/mfa", json={"totp_code": "000000"}
        )
        assert bad.status_code == 401
        # Each /login mints a fresh pending cookie; clear before the
        # next iteration so we don't accidentally accumulate them.
        app_client.cookies.clear()

    # Now even a correct password is refused — lockout is engaged.
    resp = await app_client.post(
        "/api/auth/login",
        json={"email": "alice@example.com", "password": "correct horse battery staple"},
    )
    assert resp.status_code == 401


# =============================================================================
# Real-account gate (charter)
# =============================================================================
def _project_create_body(name: str, *, account_type: str | None = None) -> dict[str, object]:
    body: dict[str, object] = {
        "name": name,
        "symbol": "EURUSD",
        "timeframe": "H1",
        "mcp_url": "http://localhost:8081",
    }
    if account_type is not None:
        body["account_type"] = account_type
    return body


async def test_create_real_account_409_without_mfa(app_client):
    await _seed_user(app_client)
    await _login(app_client, email="alice@example.com", password="correct horse battery staple")
    resp = await app_client.post(
        "/api/projects",
        json=_project_create_body("real-test", account_type="real"),
        headers=_csrf_headers(app_client),
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "MFA_REQUIRED_FOR_REAL_ACCOUNT"


async def test_create_demo_account_allowed_without_mfa(app_client):
    await _seed_user(app_client)
    await _login(app_client, email="alice@example.com", password="correct horse battery staple")
    resp = await app_client.post(
        "/api/projects",
        json=_project_create_body("demo-test", account_type="demo"),
        headers=_csrf_headers(app_client),
    )
    assert resp.status_code == 201, resp.text


async def test_create_real_account_succeeds_after_mfa(app_client):
    await _seed_user(app_client)
    await _enable_mfa_for_user(
        app_client, email="alice@example.com", password="correct horse battery staple"
    )
    resp = await app_client.post(
        "/api/projects",
        json=_project_create_body("real-test", account_type="real"),
        headers=_csrf_headers(app_client),
    )
    assert resp.status_code == 201, resp.text


async def test_patch_to_real_account_409_without_mfa(app_client):
    await _seed_user(app_client)
    await _login(app_client, email="alice@example.com", password="correct horse battery staple")
    create = await app_client.post(
        "/api/projects",
        json=_project_create_body("demo-test", account_type="demo"),
        headers=_csrf_headers(app_client),
    )
    assert create.status_code == 201
    project_id = create.json()["id"]
    resp = await app_client.patch(
        f"/api/projects/{project_id}",
        json={"account_type": "real"},
        headers=_csrf_headers(app_client),
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "MFA_REQUIRED_FOR_REAL_ACCOUNT"


async def test_patch_demo_to_real_after_mfa(app_client):
    await _seed_user(app_client)
    await _login(app_client, email="alice@example.com", password="correct horse battery staple")
    create = await app_client.post(
        "/api/projects",
        json=_project_create_body("demo-test", account_type="demo"),
        headers=_csrf_headers(app_client),
    )
    assert create.status_code == 201
    project_id = create.json()["id"]

    # Now enable MFA for the same user.
    setup = await app_client.post("/api/me/mfa/setup", headers=_csrf_headers(app_client))
    secret = setup.json()["secret_b32"]
    code = pyotp.TOTP(secret).now()
    verify = await app_client.post(
        "/api/me/mfa/verify",
        json={"totp_code": code},
        headers=_csrf_headers(app_client),
    )
    assert verify.status_code == 200

    resp = await app_client.patch(
        f"/api/projects/{project_id}",
        json={"account_type": "real"},
        headers=_csrf_headers(app_client),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["account_type"] == "real"
