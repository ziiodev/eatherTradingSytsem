"""APIRouter mounted at ``/api/auth``.

Endpoints:

* ``POST /api/auth/login``      — exchange credentials for cookies, OR
                                   mint the MFA pending cookie when the
                                   user has TOTP enabled.
* ``POST /api/auth/login/mfa``  — second step of the MFA two-step:
                                   consumes the pending cookie + a TOTP
                                   or recovery code and mints the real
                                   session cookies.
* ``POST /api/auth/refresh``    — rotate the refresh token + reissue access.
* ``POST /api/auth/logout``     — revoke the current session + clear cookies.
* ``POST /api/auth/signup``     — admin-only by default (``signup_open=False``).
* ``GET  /api/auth/me``         — return the authenticated user.

Cookie strategy (matches apps/web):

* ``aether_access``       — JWT, httpOnly, Path=/, TTL 15 min.
* ``aether_refresh``      — opaque, httpOnly, Path=/api/auth/refresh, TTL 14d.
* ``csrf_token``          — non-httpOnly, mirrored in ``X-CSRF-Token`` header
                            for double-submit verification on state-changing
                            endpoints.
* ``aether_mfa_pending``  — HS256-signed JWT, httpOnly, Path=/api/auth/login/mfa,
                            TTL 5 min. Carries ``{user_id, nonce, aud: "mfa"}``
                            and is the ONLY signal the second-step endpoint
                            accepts to identify a password-validated user.

Anti-patterns checklist (CI greps for these):

* No raw token ever logged.
* Refresh token is hashed (SHA-256) before persistence — see
  :mod:`aether_api.auth.tokens.generate_refresh_token`.
* Cross-tenant denial would never come from here — these endpoints
  only touch the authenticated user's own rows.
* The MFA pending cookie is signed with a SEPARATE secret
  (``MFA_PENDING_SECRET``) so a leak of the access-token signing
  material does not auto-promote pending cookies to sessions.
"""

from __future__ import annotations

import secrets as _secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, EmailStr, Field, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from aether_api.auth.cookies import (
    REFRESH_COOKIE,
    clear_auth_cookies,
    set_auth_cookies,
    set_csrf_cookie,
)
from aether_api.auth.passwords import (
    dummy_hash,
    hash_password,
    verify_password,
)
from aether_api.auth.tokens import (
    generate_refresh_token,
    hash_refresh_token,
    issue_access_token,
    refresh_token_expiry,
)
from aether_api.core.settings import get_settings
from aether_api.db.session import get_session
from aether_api.models.user import User
from aether_api.repositories.session_repository import SessionRepository
from aether_api.repositories.user_repository import UserRepository
from aether_api.services.mfa import consume_recovery_code, verify_totp
from aether_api.services.secret_box import SecretBox, SecretBoxError
from aether_api.tenancy.middleware import (
    csrf_dependency,
    current_user,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])

# -----------------------------------------------------------------------------
# MFA pending cookie — minted by /login when user.mfa_enabled, consumed by
# /login/mfa. Path-scoped so it never travels with any other request, and
# signed with a SEPARATE secret so a compromise of the access-token signing
# material cannot promote a pending cookie to a session.
# -----------------------------------------------------------------------------
MFA_PENDING_COOKIE: str = "aether_mfa_pending"
MFA_PENDING_COOKIE_PATH: str = "/api/auth/login/mfa"
#: Audience claim we require on the pending JWT — defeats any future
#: re-use of MFA_PENDING_SECRET for a different token shape (the verifier
#: refuses tokens with a missing or wrong ``aud``).
MFA_PENDING_AUDIENCE: str = "mfa"


# -----------------------------------------------------------------------------
# DTOs
# -----------------------------------------------------------------------------
class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=512)


class SignupRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=512)
    display_name: str | None = Field(default=None, max_length=100)


class UserSummary(BaseModel):
    """Slim representation returned by login + me."""

    id: uuid.UUID
    email: str
    display_name: str | None
    is_admin: bool


class UserMe(UserSummary):
    avatar_url: str | None
    # mfa_enabled lives on the user row (charter pre-wired column) and is
    # surfaced here so the dashboard can render the "Activate MFA" CTA
    # without an extra /api/me round-trip.
    mfa_enabled: bool


class LoginResponse(BaseModel):
    """Result of ``POST /api/auth/login``.

    Two shapes share this model:

    * Single-factor success — ``user`` is populated, ``requires_mfa`` is
      ``False`` (default). Cookies (access + refresh + csrf) have been
      set on the response.
    * Two-factor handoff — ``user`` is ``None``, ``requires_mfa`` is
      ``True``. The ``aether_mfa_pending`` cookie is the only material
      set on the response; the caller MUST POST the second step to
      ``/api/auth/login/mfa`` to obtain the real session cookies.
    """

    user: UserSummary | None = None
    requires_mfa: bool = False


class LoginMfaRequest(BaseModel):
    """Body of ``POST /api/auth/login/mfa``.

    Exactly one of ``totp_code`` / ``recovery_code`` must be present —
    the validator below rejects "both" / "neither" with HTTP 422.
    """

    model_config = {"extra": "forbid"}

    totp_code: str | None = Field(default=None, min_length=1, max_length=12)
    recovery_code: str | None = Field(default=None, min_length=1, max_length=64)

    @model_validator(mode="after")
    def _exactly_one(self) -> LoginMfaRequest:
        has_totp = self.totp_code is not None
        has_recovery = self.recovery_code is not None
        if has_totp == has_recovery:
            raise ValueError(
                "exactly one of totp_code or recovery_code must be provided"
            )
        return self


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------
def _client_ip(request: Request) -> str | None:
    # Trust X-Forwarded-For only when behind a reverse proxy; default to
    # the direct peer address.
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",", 1)[0].strip()
    if request.client is None:
        return None
    return request.client.host


def _summary(user: User) -> UserSummary:
    return UserSummary(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        is_admin=user.is_admin,
    )


def _utcnow() -> datetime:
    return datetime.now(tz=UTC)


def _mfa_pending_secret() -> str:
    """Return the configured MFA_PENDING_SECRET or raise 500.

    Fail-closed: any /login that needs to mint a pending cookie against
    a misconfigured deployment becomes a generic 500 — the user can't
    log in until the operator fixes the env. That's the correct posture
    for an auth primitive.
    """
    s = get_settings()
    if s.mfa_pending_secret is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="mfa not available",
        )
    return s.mfa_pending_secret.get_secret_value()


def _issue_mfa_pending_cookie(response: Response, *, user_id: uuid.UUID) -> None:
    """Sign and set the short-lived ``aether_mfa_pending`` JWT cookie.

    Claims:

    * ``sub``   — user id (string).
    * ``aud``   — ``"mfa"`` (verifier requires this exact audience).
    * ``nonce`` — 16 bytes urandom (URL-safe-b64). Defeats trivial
                  replay of a captured pending cookie across browser
                  sessions, since each /login mints a fresh nonce.
    * ``iat`` / ``exp`` — 5 min TTL from settings.
    """
    s = get_settings()
    secret = _mfa_pending_secret()
    now = datetime.now(tz=UTC)
    exp = now + timedelta(seconds=s.mfa_pending_ttl_seconds)
    payload: dict[str, object] = {
        "sub": str(user_id),
        "aud": MFA_PENDING_AUDIENCE,
        "nonce": _secrets.token_urlsafe(16),
        "iat": int(now.timestamp()),
        "exp": int(exp.timestamp()),
    }
    token = jwt.encode(payload, secret, algorithm="HS256")
    common: dict[str, object] = {
        "secure": s.cookie_secure,
        "samesite": "lax",
    }
    if s.cookie_domain:
        common["domain"] = s.cookie_domain
    response.set_cookie(
        key=MFA_PENDING_COOKIE,
        value=token,
        httponly=True,
        max_age=s.mfa_pending_ttl_seconds,
        path=MFA_PENDING_COOKIE_PATH,
        **common,  # type: ignore[arg-type]
    )


def _clear_mfa_pending_cookie(response: Response) -> None:
    """Delete the pending cookie. Path MUST match the original set call."""
    s = get_settings()
    common: dict[str, object] = {
        "secure": s.cookie_secure,
        "samesite": "lax",
    }
    if s.cookie_domain:
        common["domain"] = s.cookie_domain
    response.delete_cookie(
        MFA_PENDING_COOKIE, path=MFA_PENDING_COOKIE_PATH, **common  # type: ignore[arg-type]
    )


def _verify_mfa_pending_cookie(raw: str) -> uuid.UUID | None:
    """Validate signature + aud + exp; return the embedded user_id.

    Returns ``None`` on any failure (bad signature, missing aud, expired,
    tampered claims) — the caller maps that to a generic 401. We don't
    differentiate the failure modes to avoid leaking timing / claim
    structure to a probing attacker.
    """
    secret = _mfa_pending_secret()
    try:
        payload: dict[str, object] = jwt.decode(
            raw,
            secret,
            algorithms=["HS256"],
            audience=MFA_PENDING_AUDIENCE,
            options={"require": ["sub", "iat", "exp", "aud"]},
        )
    except jwt.PyJWTError:
        return None
    sub = payload.get("sub")
    if not isinstance(sub, str):
        return None
    try:
        return uuid.UUID(sub)
    except ValueError:
        return None


async def _issue_session_and_cookies(
    *,
    response: Response,
    request: Request,
    user: User,
    session_repo: SessionRepository,
) -> None:
    """Mint a new (access, refresh) pair, persist the refresh hash, set cookies.

    The session row is inserted FIRST so its id can be embedded as the
    ``sid`` claim in the access JWT. The refresh cookie is path-scoped to
    ``/api/auth/refresh`` and so cannot identify the session on any other
    path (e.g. ``/api/me/sessions``) — the JWT-side ``sid`` claim fills
    that gap. Settings-profile change introduces this; mfa-totp will
    layer additively on top.
    """
    refresh_raw, refresh_hash = generate_refresh_token()
    expires_at = refresh_token_expiry().replace(tzinfo=None)

    session_row = await session_repo.create(
        user_id=user.id,
        refresh_token_hash=refresh_hash,
        expires_at=expires_at,
        ip_address=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    access_token = issue_access_token(user.id, session_id=session_row.id)

    set_auth_cookies(response, access_token=access_token, refresh_token=refresh_raw)
    set_csrf_cookie(response)


# -----------------------------------------------------------------------------
# POST /login
# -----------------------------------------------------------------------------
@router.post("/login", response_model=LoginResponse, status_code=status.HTTP_200_OK)
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> LoginResponse:
    """Authenticate and issue cookies. NO CSRF gate here (login mints the token).

    Failure cases all return HTTP 401 with the SAME generic message and
    SAME wall-clock cost — see :func:`aether_api.auth.passwords.verify_password`
    and :func:`aether_api.auth.passwords.dummy_hash`.
    """
    s = get_settings()
    user_repo = UserRepository(session)
    session_repo = SessionRepository(session)

    email = body.email.lower()
    user = await user_repo.get_by_email(email)
    now = _utcnow()

    # Lockout window applies even before password check — but we still run
    # a dummy hash so the wall-clock cost matches a real verify.
    locked_until = user.locked_until if user else None
    is_locked = bool(
        locked_until
        and locked_until.replace(tzinfo=locked_until.tzinfo or UTC) > now
    )

    target_hash = user.password_hash if (user and user.password_hash) else dummy_hash()
    password_ok = verify_password(body.password, target_hash)

    if (
        user is None
        or not user.is_active
        or user.password_hash is None
        or is_locked
        or not password_ok
    ):
        # Bump the failed counter only when we know who the user is —
        # otherwise we'd let an attacker enumerate emails by triggering
        # a row update.
        if user is not None and user.is_active and not is_locked:
            new_count = await user_repo.increment_failed_login(user.id)
            if new_count >= s.lockout_threshold:
                await user_repo.lock_until(user.id, minutes=s.lockout_window_minutes)
            await session.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid credentials or account locked",
        )

    # Success path — password verified. If the user has TOTP enabled,
    # short-circuit BEFORE minting session cookies AND BEFORE resetting
    # the failed-login counter: the counter is shared with the MFA
    # second step (see /login/mfa below), so resetting it here would
    # let an attacker who has the password but not the TOTP code spin
    # bad codes indefinitely without ever tripping the lockout.
    if user.mfa_enabled:
        _issue_mfa_pending_cookie(response, user_id=user.id)
        await session.commit()
        # No `user` summary — the caller hasn't completed auth yet, so
        # leaking the email/admin flag would be a small but real
        # information disclosure beyond "this account exists".
        return LoginResponse(requires_mfa=True)

    # Single-factor success — safe to reset the counter and mint cookies.
    await user_repo.reset_failed_login(user.id)
    await user_repo.update_last_login(user.id, now=now.replace(tzinfo=None))
    await _issue_session_and_cookies(
        response=response, request=request, user=user, session_repo=session_repo
    )
    await session.commit()
    return LoginResponse(user=_summary(user))


# -----------------------------------------------------------------------------
# POST /login/mfa — second step of the two-factor login
# -----------------------------------------------------------------------------
@router.post(
    "/login/mfa",
    response_model=LoginResponse,
    status_code=status.HTTP_200_OK,
)
async def login_mfa(
    body: LoginMfaRequest,
    request: Request,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> LoginResponse:
    """Complete the MFA two-step. Mint real cookies on success.

    Contract:

    * The caller MUST present the ``aether_mfa_pending`` cookie that
      ``/api/auth/login`` set when it saw ``user.mfa_enabled``. The
      cookie is path-scoped to this endpoint, so no other endpoint
      sees it.
    * Body MUST include exactly one of ``totp_code`` / ``recovery_code``.
    * Lockout: a wrong code increments ``failed_login_count`` and can
      trigger the same lockout window as a wrong password — that's how
      we keep this endpoint from becoming a 6-digit brute-force oracle.

    Outcomes:

    * 200 — pending cookie cleared, real session cookies set.
    * 401 — pending cookie missing/expired/tampered, account locked,
            user disabled, OR the supplied code didn't verify.

    NO CSRF dependency here — same reasoning as ``/api/auth/login``:
    the caller has no CSRF cookie yet (this endpoint mints them).
    """
    s = get_settings()
    raw_pending = request.cookies.get(MFA_PENDING_COOKIE)
    if not raw_pending:
        _clear_mfa_pending_cookie(response)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid mfa challenge",
        )

    pending_user_id = _verify_mfa_pending_cookie(raw_pending)
    if pending_user_id is None:
        _clear_mfa_pending_cookie(response)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid mfa challenge",
        )

    user_repo = UserRepository(session)
    session_repo = SessionRepository(session)
    user = await user_repo.get_by_id(pending_user_id)
    now = _utcnow()

    # Same lockout guard as the password step — a stolen pending cookie
    # otherwise lets the attacker spin codes uncapped.
    locked_until = user.locked_until if user else None
    is_locked = bool(
        locked_until
        and locked_until.replace(tzinfo=locked_until.tzinfo or UTC) > now
    )

    if (
        user is None
        or not user.is_active
        or not user.mfa_enabled
        or not user.mfa_secret_ref
        or is_locked
    ):
        _clear_mfa_pending_cookie(response)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid mfa challenge",
        )

    # Decrypt the stored TOTP secret. A SecretBoxError here means the
    # MFA_SECRET_KEY was rotated without re-enrolling users — refuse
    # rather than silently fail.
    try:
        secret_b32 = SecretBox().decrypt(user.mfa_secret_ref)
    except SecretBoxError:
        _clear_mfa_pending_cookie(response)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="mfa not available",
        ) from None

    code_ok = False
    if body.totp_code is not None:
        code_ok = verify_totp(secret_b32, body.totp_code, now=now)
    elif body.recovery_code is not None:
        code_ok = await consume_recovery_code(
            session, user_id=user.id, raw_code=body.recovery_code, now=now
        )

    if not code_ok:
        # Share the password-lockout counter so a mix of bad-password /
        # bad-totp attempts still bumps the same threshold.
        new_count = await user_repo.increment_failed_login(user.id)
        if new_count >= s.lockout_threshold:
            await user_repo.lock_until(
                user.id, minutes=s.lockout_window_minutes
            )
        await session.commit()
        _clear_mfa_pending_cookie(response)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid mfa challenge",
        )

    # Success — mint the real cookies, clear the pending one.
    await user_repo.reset_failed_login(user.id)
    await user_repo.update_last_login(user.id, now=now.replace(tzinfo=None))
    await _issue_session_and_cookies(
        response=response, request=request, user=user, session_repo=session_repo
    )
    _clear_mfa_pending_cookie(response)
    await session.commit()
    return LoginResponse(user=_summary(user))


# -----------------------------------------------------------------------------
# POST /refresh
# -----------------------------------------------------------------------------
@router.post("/refresh", status_code=status.HTTP_200_OK)
async def refresh(
    request: Request,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_session)],
    _csrf: Annotated[None, Depends(csrf_dependency)],
) -> dict[str, bool]:
    """Rotate the refresh token: REVOKE-OLD + INSERT-NEW.

    Failures (missing cookie, unknown hash, already revoked, expired)
    return 401 AND clear all cookies — a stuck client can't accidentally
    keep hammering the endpoint with a dead token.
    """
    raw_refresh = request.cookies.get(REFRESH_COOKIE)
    if not raw_refresh:
        clear_auth_cookies(response)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="no refresh cookie")

    session_repo = SessionRepository(session)
    incoming_hash = hash_refresh_token(raw_refresh)
    row = await session_repo.get_active_by_token_hash(incoming_hash)

    now = _utcnow()
    if row is None or row.expires_at.replace(tzinfo=row.expires_at.tzinfo or UTC) <= now:
        clear_auth_cookies(response)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="refresh invalid")

    # Revoke OLD.
    await session_repo.revoke(row.id, now=now.replace(tzinfo=None))

    # Load the user; revoke + clear if they were deactivated since issuing.
    user_repo = UserRepository(session)
    user = await user_repo.get_by_id(row.user_id)
    if user is None or not user.is_active:
        await session.commit()
        clear_auth_cookies(response)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="user disabled")

    # Insert NEW (cookies + DB row).
    await _issue_session_and_cookies(
        response=response, request=request, user=user, session_repo=session_repo
    )
    await session.commit()
    return {"ok": True}


# -----------------------------------------------------------------------------
# POST /logout
# -----------------------------------------------------------------------------
@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    request: Request,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_session)],
    _csrf: Annotated[None, Depends(csrf_dependency)],
) -> Response:
    """Revoke THIS session (by refresh-cookie hash) and clear all cookies.

    Logout is always 204, even when the refresh cookie is missing or
    invalid — telling a logging-out client "your session was already
    dead" leaks nothing and avoids 4xx noise in monitoring.
    """
    raw_refresh = request.cookies.get(REFRESH_COOKIE)
    if raw_refresh:
        session_repo = SessionRepository(session)
        row = await session_repo.get_active_by_token_hash(hash_refresh_token(raw_refresh))
        if row is not None:
            await session_repo.revoke(row.id)
            await session.commit()

    clear_auth_cookies(response)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


# -----------------------------------------------------------------------------
# POST /signup
# -----------------------------------------------------------------------------
@router.post("/signup", response_model=UserSummary, status_code=status.HTTP_201_CREATED)
async def signup(
    body: SignupRequest,
    request: Request,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_session)],
    _csrf: Annotated[None, Depends(csrf_dependency)],
) -> UserSummary:
    """Create a user.

    Two modes:

    * ``signup_open=True`` — anyone may sign up. CSRF still required.
    * ``signup_open=False`` (v1 default) — caller MUST be an authenticated
      admin (403 otherwise).
    """
    s = get_settings()

    if not s.signup_open:
        # Enforce admin manually rather than via ``Depends(admin_required)``
        # so the public-signup branch above doesn't trigger 401 for
        # unauthenticated callers.
        try:
            user = await current_user(request, session)
        except HTTPException:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="signup disabled",
            ) from None
        if not user.is_admin:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="signup disabled",
            )

    user_repo = UserRepository(session)
    email = body.email.lower()

    existing = await user_repo.get_by_email(email)
    if existing is not None:
        # Same generic message regardless of which user was duplicated.
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="email already in use")

    created = await user_repo.create(
        email=email,
        password_hash=hash_password(body.password),
        display_name=body.display_name,
    )
    await session.commit()
    response.status_code = status.HTTP_201_CREATED
    return _summary(created)


# -----------------------------------------------------------------------------
# GET /me
# -----------------------------------------------------------------------------
@router.get("/me", response_model=UserMe, status_code=status.HTTP_200_OK)
async def me(user: Annotated[User, Depends(current_user)]) -> UserMe:
    return UserMe(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        avatar_url=user.avatar_url,
        is_admin=user.is_admin,
        mfa_enabled=user.mfa_enabled,
    )
