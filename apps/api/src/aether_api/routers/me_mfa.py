"""``/api/me/mfa`` — TOTP enrollment, verification, disable, regenerate.

Why this is a separate router (not folded into :mod:`routers.me`):

* The MFA surface is conceptually a sub-resource ("MFA on the caller's
  account"), but every handler talks to ``services/mfa.py`` /
  ``services/secret_box.py`` — modules the settings tabs do not.
  Splitting keeps :mod:`routers.me` from growing a dependency on the
  crypto primitives.
* Future docs grouping in OpenAPI ("MFA" tag) is also cleaner this way.

Endpoint surface (all mounted at ``/api/me/mfa``):

* ``POST /setup``                 — mint TOTP secret + QR. Returns ONCE.
* ``POST /verify``                — confirm enrollment + mint recovery codes ONCE.
* ``POST /disable``               — turn MFA off (password + TOTP required).
* ``POST /recovery-codes/regenerate`` — fresh batch of 10 codes (password gate).

CSRF (double-submit) is mandatory on every endpoint here — the caller
already has a valid session, so the cookie is present. We layer
``Depends(csrf_dependency)`` explicitly per route rather than relying on
a router-level dependency so each endpoint's contract is reviewable in
isolation.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from aether_api.auth.passwords import verify_password
from aether_api.db.session import get_session
from aether_api.models.user import User
from aether_api.repositories.mfa_recovery_repository import MfaRecoveryRepository
from aether_api.repositories.user_repository import UserRepository
from aether_api.services.mfa import (
    generate_recovery_codes,
    generate_totp_secret,
    provisioning_uri,
    store_recovery_codes,
    verify_totp,
)
from aether_api.services.secret_box import SecretBox, SecretBoxError
from aether_api.tenancy.middleware import csrf_dependency, current_user

router = APIRouter(prefix="/api/me/mfa", tags=["mfa"])


# -----------------------------------------------------------------------------
# DTOs
# -----------------------------------------------------------------------------
class SetupResponse(BaseModel):
    """Returned exactly once at the end of POST /api/me/mfa/setup.

    The frontend MUST display ``qr_data_url`` (rendered via
    ``qrcode.react`` or similar) AND ``secret_b32`` (manual-entry
    fallback) — once the dialog closes, the secret is unreadable.
    ``mfa_enabled`` stays ``false`` until POST /verify succeeds.
    """

    provisioning_uri: str
    secret_b32: str
    qr_data_url: str


class VerifyRequest(BaseModel):
    model_config = {"extra": "forbid"}
    totp_code: str = Field(min_length=1, max_length=12)


class VerifyResponse(BaseModel):
    """Returned exactly once at the end of POST /api/me/mfa/verify.

    ``recovery_codes`` is the ONLY moment the plaintext codes are
    visible to the user; downstream calls only ever see the argon2id
    hashes. The frontend must force the user through an "I've saved
    them" gate before allowing the dialog to close.
    """

    mfa_enabled: bool
    recovery_codes: list[str]


class DisableRequest(BaseModel):
    model_config = {"extra": "forbid"}
    current_password: str = Field(min_length=1, max_length=512)
    totp_code: str = Field(min_length=1, max_length=12)


class RegenerateRequest(BaseModel):
    model_config = {"extra": "forbid"}
    current_password: str = Field(min_length=1, max_length=512)


class RegenerateResponse(BaseModel):
    recovery_codes: list[str]


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------
def _utcnow_naive() -> datetime:
    """Return a tz-naive UTC datetime — matches the column type (``TIMESTAMP``)."""
    return datetime.now(tz=UTC).replace(tzinfo=None)


def _qr_data_url(provisioning: str) -> str:
    """Return an ``otpauth://`` URI as a data URL backed by the same string.

    The frontend (qrcode.react) renders the QR client-side from the raw
    ``provisioning_uri`` field; we still expose a ``qr_data_url`` for
    callers that prefer a server-rendered image (CLI tooling, screen
    readers). Today we simply prefix the URI — ``qrcode.react`` does the
    work browser-side. If a server-rendered PNG/SVG is ever needed we
    can swap the body here without touching call sites.
    """
    # ``data:text/plain`` is honest about today's content; the frontend
    # never reads this field directly when qrcode.react is available.
    return f"data:text/plain;charset=utf-8,{provisioning}"


# -----------------------------------------------------------------------------
# POST /api/me/mfa/setup
# -----------------------------------------------------------------------------
@router.post(
    "/setup",
    response_model=SetupResponse,
    status_code=status.HTTP_200_OK,
)
async def setup_mfa(
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    _csrf: Annotated[None, Depends(csrf_dependency)],
) -> SetupResponse:
    """Generate a TOTP secret, encrypt it, store the ciphertext, return the QR.

    Rules:

    * Forbidden when MFA is already enabled → 409 (caller must disable
      first to re-enrol).
    * Allowed when ``mfa_secret_ref`` is already populated but
      ``mfa_enabled=false`` (abandoned setup) — a fresh secret is
      generated and overwrites the previous one.
    * The plaintext secret is returned in ``secret_b32`` so the user can
      paste it into an authenticator that doesn't support QR scanning.
      It is NEVER stored in plaintext.
    """
    if user.mfa_enabled:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "MFA_ALREADY_ENABLED"},
        )

    secret_b32 = generate_totp_secret()
    uri = provisioning_uri(secret_b32, account_name=user.email)

    try:
        encrypted = SecretBox().encrypt(secret_b32)
    except SecretBoxError as exc:
        # Misconfigured MFA_SECRET_KEY — fail closed with 500. The
        # error body is generic; the cause is in structlog.
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="mfa not available",
        ) from exc

    user_repo = UserRepository(session)
    await user_repo.set_mfa_secret_ref(
        user.id, encrypted, now=_utcnow_naive()
    )
    await session.commit()

    return SetupResponse(
        provisioning_uri=uri,
        secret_b32=secret_b32,
        qr_data_url=_qr_data_url(uri),
    )


# -----------------------------------------------------------------------------
# POST /api/me/mfa/verify
# -----------------------------------------------------------------------------
@router.post(
    "/verify",
    response_model=VerifyResponse,
    status_code=status.HTTP_200_OK,
)
async def verify_mfa_setup(
    body: VerifyRequest,
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    _csrf: Annotated[None, Depends(csrf_dependency)],
) -> VerifyResponse:
    """Confirm enrollment with a fresh TOTP code, then mint recovery codes.

    Outcomes:

    * 400 (``MFA_NOT_SETUP``) — no ``mfa_secret_ref`` yet; caller must
      hit /setup first.
    * 409 (``MFA_ALREADY_ENABLED``) — re-verifying once enabled is a
      no-op caller error.
    * 401 (``INVALID_TOTP_CODE``) — code did not validate against the
      stored secret (±1 step window).
    * 200 — ``mfa_enabled`` flipped to True, 10 recovery codes returned
      ONCE. The same response body is also the only moment they are
      visible in plaintext.
    """
    if user.mfa_enabled:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "MFA_ALREADY_ENABLED"},
        )
    if not user.mfa_secret_ref:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "MFA_NOT_SETUP"},
        )

    try:
        secret_b32 = SecretBox().decrypt(user.mfa_secret_ref)
    except SecretBoxError as exc:
        # Stored ciphertext is unreadable — most likely the key was
        # rotated without re-enrolling users. Refuse rather than mint
        # cookies against an unverifiable secret.
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="mfa not available",
        ) from exc

    if not verify_totp(secret_b32, body.totp_code):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "INVALID_TOTP_CODE"},
        )

    user_repo = UserRepository(session)
    raw_codes = generate_recovery_codes()
    await store_recovery_codes(session, user_id=user.id, raw_codes=raw_codes)
    await user_repo.enable_mfa(user.id, now=_utcnow_naive())
    await session.commit()

    return VerifyResponse(mfa_enabled=True, recovery_codes=raw_codes)


# -----------------------------------------------------------------------------
# POST /api/me/mfa/disable
# -----------------------------------------------------------------------------
@router.post("/disable", status_code=status.HTTP_200_OK)
async def disable_mfa(
    body: DisableRequest,
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    _csrf: Annotated[None, Depends(csrf_dependency)],
) -> dict[str, object]:
    """Turn MFA off. Requires BOTH the password and a current TOTP code.

    Two factors are mandatory:

    * Password — argon2id verify; defeats a session-cookie thief.
    * TOTP    — defeats a password-only thief.

    On success: ``mfa_enabled=false``, ``mfa_secret_ref=null``, all
    recovery code rows deleted. The endpoint is idempotent on a user
    who already has MFA off only insofar as the password+TOTP both
    validate — we still 409 because the caller's mental model is wrong
    ("disable" on an already-off account is a UI bug).
    """
    if not user.mfa_enabled:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "MFA_NOT_ENABLED"},
        )
    if not user.password_hash or not verify_password(
        body.current_password, user.password_hash
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "INVALID_CREDENTIALS"},
        )
    if not user.mfa_secret_ref:
        # Shouldn't happen — mfa_enabled implies a secret ref — but
        # fail closed.
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="mfa not available",
        )
    try:
        secret_b32 = SecretBox().decrypt(user.mfa_secret_ref)
    except SecretBoxError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="mfa not available",
        ) from exc

    if not verify_totp(secret_b32, body.totp_code):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "INVALID_TOTP_CODE"},
        )

    user_repo = UserRepository(session)
    recovery_repo = MfaRecoveryRepository(session)
    deleted = await recovery_repo.delete_all_for_user(user.id)
    await user_repo.disable_mfa(user.id, now=_utcnow_naive())
    await session.commit()
    return {"mfa_enabled": False, "recovery_codes_deleted": deleted}


# -----------------------------------------------------------------------------
# POST /api/me/mfa/recovery-codes/regenerate
# -----------------------------------------------------------------------------
@router.post(
    "/recovery-codes/regenerate",
    response_model=RegenerateResponse,
    status_code=status.HTTP_200_OK,
)
async def regenerate_recovery_codes(
    body: RegenerateRequest,
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    _csrf: Annotated[None, Depends(csrf_dependency)],
) -> RegenerateResponse:
    """Mint a fresh batch of 10 recovery codes; invalidate any previous batch.

    Requires the current password as a high-friction gate — regenerating
    is a credential rotation and we don't want a stolen access cookie to
    silently swap codes a real user is relying on.

    Returns the plaintext list exactly once; downstream calls only see
    argon2id hashes.
    """
    if not user.mfa_enabled:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "MFA_NOT_ENABLED"},
        )
    if not user.password_hash or not verify_password(
        body.current_password, user.password_hash
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "INVALID_CREDENTIALS"},
        )

    raw_codes = generate_recovery_codes()
    await store_recovery_codes(session, user_id=user.id, raw_codes=raw_codes)
    await session.commit()
    return RegenerateResponse(recovery_codes=raw_codes)
