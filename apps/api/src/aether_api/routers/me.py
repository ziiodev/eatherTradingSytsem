"""``/api/me`` — self-service profile, credentials, and session management.

Why this module exists separately from :mod:`aether_api.auth.routes`:

* ``auth/routes.py`` owns the cookie / session lifecycle for **anonymous**
  callers (login mints cookies; refresh / logout / signup). Self-service
  endpoints below are strictly for an **authenticated** caller acting on
  their own row — every handler runs through ``Depends(current_user)``
  and never mints a session cold.
* Splitting also keeps `auth/routes.py` focused so the CI "no raw token
  logged" greps stay tight, and lets a future ``mfa-totp`` change rebase
  on top by only extending the same imports + router-include lines.

Endpoint surface (all mounted at ``/api/me``):

* ``PATCH /``                          — update display_name / avatar_url.
* ``POST  /email/change``              — change email (password-confirmed).
* ``POST  /password/change``           — change password (transactional;
                                          optional bulk revoke + current
                                          session rotation).
* ``GET   /sessions``                  — list caller's sessions (cursor pagination).
* ``POST  /sessions/{id}/revoke``      — revoke a specific session.
* ``POST  /sessions/revoke-others``    — revoke every session except current.

All mutating endpoints require the standard double-submit CSRF header
(``X-CSRF-Token``). The CSRF cookie + access cookie are already minted
by ``/api/auth/login``; no endpoint here issues cookies from scratch —
the password-change path *rotates* them, which is a different code path
than minting.
"""

from __future__ import annotations

import base64
import binascii
import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, EmailStr, Field, HttpUrl, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from aether_api.auth.cookies import (
    ACCESS_COOKIE,
    REFRESH_COOKIE,
    clear_auth_cookies,
    set_auth_cookies,
    set_csrf_cookie,
)
from aether_api.auth.passwords import hash_password, verify_password
from aether_api.auth.tokens import (
    generate_refresh_token,
    hash_refresh_token,
    issue_access_token,
    refresh_token_expiry,
    verify_access_token_session_id,
)
from aether_api.db.session import get_session
from aether_api.models.session import UserSession
from aether_api.models.user import User
from aether_api.repositories.session_repository import SessionRepository
from aether_api.repositories.user_repository import (
    EmailAlreadyTakenError,
    UserRepository,
)
from aether_api.tenancy.middleware import csrf_dependency, current_user

router = APIRouter(prefix="/api/me", tags=["me"])

# Page size cap on GET /sessions. The default matches the design doc;
# the upper bound is conservative so the dashboard can't hammer the DB
# by paginating with limit=1000.
_SESSIONS_DEFAULT_LIMIT = 20
_SESSIONS_MAX_LIMIT = 100


# -----------------------------------------------------------------------------
# DTOs
# -----------------------------------------------------------------------------
class ProfileResponse(BaseModel):
    """Shape returned by ``PATCH /api/me`` and the various session-derived helpers."""

    id: uuid.UUID
    email: str
    display_name: str | None
    avatar_url: str | None
    email_verified_at: datetime | None
    is_admin: bool
    created_at: datetime | None


class ProfileUpdateRequest(BaseModel):
    """Partial profile update.

    Pydantic v2 ``model_config = {"extra": "forbid"}`` rejects unknown
    fields with HTTP 422 — that's the spec contract.
    """

    model_config = {"extra": "forbid"}

    display_name: str | None = Field(default=None, min_length=1, max_length=100)
    # We accept ``str`` rather than ``HttpUrl`` directly so the caller can
    # also pass ``null`` to clear the field. The validator below enforces
    # http(s) + length when the value is non-null.
    avatar_url: str | None = Field(default=None, max_length=2048)

    @field_validator("avatar_url")
    @classmethod
    def _avatar_url_must_be_http(cls, v: str | None) -> str | None:
        if v is None or v == "":
            return None
        # Run the value through Pydantic's HttpUrl to get scheme validation
        # for free; we still return the original string so the DB stores
        # what the user typed.
        parsed = HttpUrl(v)
        if parsed.scheme not in {"http", "https"}:
            raise ValueError("avatar_url must use http or https")
        return v


class EmailChangeRequest(BaseModel):
    model_config = {"extra": "forbid"}
    new_email: EmailStr
    current_password: str = Field(min_length=1, max_length=512)


class PasswordChangeRequest(BaseModel):
    model_config = {"extra": "forbid"}
    current_password: str = Field(min_length=1, max_length=512)
    new_password: str = Field(min_length=8, max_length=512)
    sign_out_others: bool = False


class SessionItem(BaseModel):
    id: uuid.UUID
    ip_address: str | None
    user_agent: str | None
    issued_at: datetime
    last_used_at: datetime
    expires_at: datetime
    revoked_at: datetime | None
    is_current: bool


class SessionsPage(BaseModel):
    items: list[SessionItem]
    next_cursor: str | None


class RevokeOthersResponse(BaseModel):
    revoked: int


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------
def _utcnow() -> datetime:
    return datetime.now(tz=UTC)


def _client_ip(request: Request) -> str | None:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",", 1)[0].strip()
    if request.client is None:
        return None
    return request.client.host


def _to_profile(user: User) -> ProfileResponse:
    return ProfileResponse(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        avatar_url=user.avatar_url,
        email_verified_at=user.email_verified_at,
        is_admin=user.is_admin,
        created_at=user.created_at,
    )


def _current_session_id_from_access(request: Request) -> uuid.UUID | None:
    """Return the caller's session id taken from the access JWT's ``sid`` claim.

    The refresh cookie is path-scoped to ``/api/auth/refresh``, so it is
    NOT sent on ``/api/me/*`` requests. Instead the access JWT carries
    the session id as a ``sid`` claim (added at login + refresh). When
    the claim is absent (e.g. a legacy token issued before this change)
    we fall back to ``None`` — the caller treats that as "current session
    unknown".
    """
    token = request.cookies.get(ACCESS_COOKIE)
    if not token:
        return None
    return verify_access_token_session_id(token)


async def _resolve_current_session(
    request: Request, session_repo: SessionRepository
) -> UserSession | None:
    """Look up the caller's active session row.

    Tries two signals in priority order:

    1. The ``sid`` claim embedded in the access JWT (works on any path).
    2. The path-scoped refresh cookie — only useful when the caller's
       request actually carried the cookie (e.g. server-side proxy, or
       a path the cookie is scoped to). Kept as a fallback so test
       harnesses that inject refresh cookies into ``/api/me/*``
       requests behave like a real browser would for the JWT path.

    Returns ``None`` when neither signal yields a live, owned session row.
    """
    sid = _current_session_id_from_access(request)
    if sid is not None:
        row = await session_repo.get_for_user_active(sid)
        if row is not None:
            return row

    raw = request.cookies.get(REFRESH_COOKIE)
    if raw is None:
        return None
    return await session_repo.get_active_by_token_hash(hash_refresh_token(raw))


def _encode_cursor(issued_at: datetime, session_id: uuid.UUID) -> str:
    """Encode a session-list cursor as opaque URL-safe base64.

    Format: ``<isoformat>|<uuid>`` then base64. Opaque on the wire so the
    client doesn't grow a dependency on the internal ordering key.
    """
    raw = f"{issued_at.isoformat()}|{session_id}".encode()
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    """Decode the opaque cursor. Raises ``ValueError`` on any malformed input."""
    padding = "=" * (-len(cursor) % 4)
    try:
        raw = base64.urlsafe_b64decode(cursor + padding).decode("ascii")
    except (binascii.Error, UnicodeDecodeError) as exc:
        raise ValueError("invalid cursor") from exc
    if "|" not in raw:
        raise ValueError("invalid cursor")
    issued_str, id_str = raw.split("|", 1)
    try:
        issued_at = datetime.fromisoformat(issued_str)
    except ValueError as exc:
        raise ValueError("invalid cursor") from exc
    try:
        session_id = uuid.UUID(id_str)
    except ValueError as exc:
        raise ValueError("invalid cursor") from exc
    return issued_at, session_id


# -----------------------------------------------------------------------------
# PATCH /api/me  — profile (display_name / avatar_url)
# -----------------------------------------------------------------------------
@router.patch("", response_model=ProfileResponse, status_code=status.HTTP_200_OK)
async def update_profile(
    body: ProfileUpdateRequest,
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    _csrf: Annotated[None, Depends(csrf_dependency)],
) -> ProfileResponse:
    """Update display_name and/or avatar_url for the caller.

    The Pydantic model gives us:

    * length caps (1..100 / 0..2048)
    * scheme-only http(s) validation on ``avatar_url``
    * 422 on unknown fields via ``extra = "forbid"``

    Pydantic's "field unset" tracking distinguishes ``{"display_name": null}``
    (clear it) from ``{}`` (leave it alone) — that's what
    ``fields_set`` does for us.
    """
    user_repo = UserRepository(session)
    fields = body.model_fields_set
    if not fields:
        # Nothing to do — still return the current shape. 200 instead of
        # 304 because the response carries the canonical profile.
        return _to_profile(user)

    updated = await user_repo.update_profile(
        user.id,
        display_name=body.display_name,
        avatar_url=body.avatar_url,
        update_display_name="display_name" in fields,
        update_avatar_url="avatar_url" in fields,
        now=_utcnow().replace(tzinfo=None),
    )
    await session.commit()
    # ``updated`` cannot be None — ``current_user`` already proved the row
    # exists. We narrow the type for mypy with a defensive check.
    if updated is None:  # pragma: no cover — defensive
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="user not found")
    return _to_profile(updated)


# -----------------------------------------------------------------------------
# POST /api/me/email/change
# -----------------------------------------------------------------------------
@router.post(
    "/email/change",
    response_model=ProfileResponse,
    status_code=status.HTTP_200_OK,
)
async def change_email(
    body: EmailChangeRequest,
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    _csrf: Annotated[None, Depends(csrf_dependency)],
) -> ProfileResponse:
    """Change the caller's email after verifying the current password.

    Hard rules:

    * Password verify uses :func:`verify_password` (argon2id, constant
      wall-clock cost). 401 on mismatch — no enumeration risk because
      we're authenticated already.
    * New email is normalised to lowercase before the unique-index check
      (matches the ``users_email_lower`` DB constraint).
    * On a collision with an existing row → 409 with a stable detail.
    * ``email_verified_at`` is set to NULL — the new address is unverified
      by definition. v1 has no SMTP, so re-verification lives in a future
      change.
    """
    if not user.password_hash or not verify_password(body.current_password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid credentials",
        )

    new_email = body.new_email.lower()
    if new_email == user.email:
        # Idempotent no-op. Return current profile rather than 409.
        return _to_profile(user)

    user_repo = UserRepository(session)
    try:
        await user_repo.update_email(
            user.id, new_email, now=_utcnow().replace(tzinfo=None)
        )
    except EmailAlreadyTakenError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="email already in use",
        ) from exc

    await session.commit()
    refreshed = await user_repo.get_by_id(user.id)
    assert refreshed is not None  # invariant — we just updated this row
    return _to_profile(refreshed)


# -----------------------------------------------------------------------------
# POST /api/me/password/change
# -----------------------------------------------------------------------------
@router.post("/password/change", status_code=status.HTTP_200_OK)
async def change_password(
    body: PasswordChangeRequest,
    request: Request,
    response: Response,
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    _csrf: Annotated[None, Depends(csrf_dependency)],
) -> dict[str, object]:
    """Change the caller's password, optionally bulk-revoke other sessions, rotate current.

    Order of operations (single transaction — all-or-nothing):

    1. Verify ``current_password`` against ``users.password_hash``.
    2. Rehash ``new_password`` with argon2id (charter floor parameters).
    3. ``UPDATE users SET password_hash, updated_at``.
    4. If ``sign_out_others`` is true: revoke every active session
       belonging to the user *except* the caller's current one.
    5. Always rotate the caller's current session — revoke the old row
       and insert a new (refresh_hash, expires_at) pair, then refresh
       cookies. Mirrors ``POST /api/auth/refresh`` so a successful
       password change leaves the caller authenticated.

    Any failure rolls everything back — the user keeps their old password
    AND their old refresh token, so a half-applied change cannot lock them
    out.
    """
    if not user.password_hash or not verify_password(body.current_password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid credentials",
        )

    user_repo = UserRepository(session)
    session_repo = SessionRepository(session)

    new_hash = hash_password(body.new_password)
    now = _utcnow()
    now_naive = now.replace(tzinfo=None)

    current_session = await _resolve_current_session(request, session_repo)
    if current_session is None:
        # Caller authenticated via the access cookie but the refresh row
        # is gone (revoked, expired, or never present). We can still update
        # the password — but we cannot rotate. Skip the rotate-and-reissue
        # branch; the access cookie keeps working for its remaining TTL.
        await user_repo.update_password_hash(user.id, new_hash, now=now_naive)
        revoked_others = 0
        if body.sign_out_others:
            # No current session to spare — revoke ALL.
            revoked_others = await session_repo.revoke_all_for_user(user.id, now=now_naive)
        await session.commit()
        return {"ok": True, "revoked_other_sessions": revoked_others}

    # Standard path: we have a live session row to rotate around.
    await user_repo.update_password_hash(user.id, new_hash, now=now_naive)

    revoked_others = 0
    if body.sign_out_others:
        revoked_others = await session_repo.revoke_others_for_user(
            user.id,
            except_session_id=current_session.id,
            now=now_naive,
        )

    # Rotate the caller's current session (revoke + new row + new cookies).
    await session_repo.revoke(current_session.id, now=now_naive)

    refresh_raw, refresh_hash = generate_refresh_token()
    expires_at = refresh_token_expiry(now=now).replace(tzinfo=None)
    new_session = await session_repo.create(
        user_id=user.id,
        refresh_token_hash=refresh_hash,
        expires_at=expires_at,
        ip_address=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    access_token = issue_access_token(user.id, session_id=new_session.id, now=now)

    set_auth_cookies(response, access_token=access_token, refresh_token=refresh_raw)
    set_csrf_cookie(response)

    await session.commit()
    return {"ok": True, "revoked_other_sessions": revoked_others}


# -----------------------------------------------------------------------------
# GET /api/me/sessions
# -----------------------------------------------------------------------------
@router.get("/sessions", response_model=SessionsPage, status_code=status.HTTP_200_OK)
async def list_sessions(
    request: Request,
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: Annotated[
        int, Query(ge=1, le=_SESSIONS_MAX_LIMIT)
    ] = _SESSIONS_DEFAULT_LIMIT,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
) -> SessionsPage:
    """List the caller's sessions ordered ``issued_at DESC, id DESC``.

    Pagination is keyset / seek — the cursor encodes the
    ``(issued_at, id)`` of the last row returned. The ``is_current`` flag
    is derived server-side by hashing the request's refresh cookie and
    matching it against ``refresh_token_hash``. Never trust the client.
    """
    session_repo = SessionRepository(session)

    cursor_tuple: tuple[datetime, uuid.UUID] | None = None
    if cursor:
        try:
            cursor_tuple = _decode_cursor(cursor)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="invalid cursor",
            ) from exc

    rows, next_cursor_tuple = await session_repo.list_for_user(
        user.id, limit=limit, cursor=cursor_tuple
    )

    # is_current is derived from the access JWT's `sid` claim. We also
    # accept a matching path-scoped refresh cookie as a fallback for
    # server-side callers that happen to carry it.
    current_sid = _current_session_id_from_access(request)
    raw_refresh = request.cookies.get(REFRESH_COOKIE)
    current_hash = hash_refresh_token(raw_refresh) if raw_refresh else None

    items: list[SessionItem] = []
    for row in rows:
        is_current = row.revoked_at is None and (
            (current_sid is not None and row.id == current_sid)
            or (current_hash is not None and row.refresh_token_hash == current_hash)
        )
        items.append(
            SessionItem(
                id=row.id,
                ip_address=str(row.ip_address) if row.ip_address is not None else None,
                user_agent=row.user_agent,
                issued_at=row.issued_at,
                last_used_at=row.last_used_at,
                expires_at=row.expires_at,
                revoked_at=row.revoked_at,
                is_current=is_current,
            )
        )
    next_cursor = (
        _encode_cursor(*next_cursor_tuple) if next_cursor_tuple is not None else None
    )
    return SessionsPage(items=items, next_cursor=next_cursor)


# -----------------------------------------------------------------------------
# POST /api/me/sessions/{id}/revoke
# -----------------------------------------------------------------------------
@router.post(
    "/sessions/{session_id}/revoke",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def revoke_session(
    session_id: uuid.UUID,
    request: Request,
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    _csrf: Annotated[None, Depends(csrf_dependency)],
) -> Response:
    """Revoke one of the caller's own sessions.

    Outcomes:

    * 204 — row revoked (or already revoked — idempotent).
    * 400 (``use_logout_instead``) — the target id matches the caller's
      current session. We refuse here so the UI doesn't silently log
      the user out of the tab they're staring at; the dedicated
      ``POST /api/auth/logout`` path is the documented way to end the
      current session.
    * 404 — no such session, or it belongs to another user. Cross-tenant
      denial returns 404, never 403, per the project's auth rules.
    """
    session_repo = SessionRepository(session)

    target = await session_repo.get_for_user(user.id, session_id)
    if target is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="session not found",
        )

    current = await _resolve_current_session(request, session_repo)
    if current is not None and current.id == session_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "use_logout_instead",
                "message": "cannot revoke current session — call POST /api/auth/logout",
            },
        )

    await session_repo.revoke(session_id, now=_utcnow().replace(tzinfo=None))
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# -----------------------------------------------------------------------------
# POST /api/me/sessions/revoke-others
# -----------------------------------------------------------------------------
@router.post(
    "/sessions/revoke-others",
    response_model=RevokeOthersResponse,
    status_code=status.HTTP_200_OK,
)
async def revoke_other_sessions(
    request: Request,
    response: Response,
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    _csrf: Annotated[None, Depends(csrf_dependency)],
) -> RevokeOthersResponse:
    """Revoke every active session for the caller except the current one.

    If there's no resolvable current session (refresh cookie missing or
    already dead) we still proceed by revoking ALL active rows and
    clearing the now-orphaned cookies — the caller is effectively logged
    out, which matches the user's intent ("kill anything that isn't me").
    """
    session_repo = SessionRepository(session)
    now_naive = _utcnow().replace(tzinfo=None)

    current = await _resolve_current_session(request, session_repo)
    if current is None:
        revoked = await session_repo.revoke_all_for_user(user.id, now=now_naive)
        await session.commit()
        clear_auth_cookies(response)
        return RevokeOthersResponse(revoked=revoked)

    revoked = await session_repo.revoke_others_for_user(
        user.id,
        except_session_id=current.id,
        now=now_naive,
    )
    await session.commit()
    return RevokeOthersResponse(revoked=revoked)
