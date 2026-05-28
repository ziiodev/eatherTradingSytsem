"""JWT access tokens + opaque refresh tokens.

Rules:

* Access token: signed JWT, 15 min TTL. Default algorithm is **RS256**
  (asymmetric — private key signs, public key verifies). The legacy HS256
  path remains for verification only, gated by
  ``Settings.jwt_legacy_hs256_verify_enabled``, and is removed by the
  ``rs256-jwt-cleanup`` follow-up. The token always contains ``sub`` (user
  UUID as string), ``iat``, ``exp``. No PII beyond the user id.
* Refresh token: 32 bytes of urandom, URL-safe-b64 encoded. The raw
  string is returned to the caller exactly once (to set the cookie);
  only the SHA-256 hex is persisted in ``sessions.refresh_token_hash``.

Security notes:

* RS256 tokens carry a ``kid`` (key id) in the JOSE header so verifiers
  (the Next.js edge middleware via JWKS, future sister services) can pick
  the right public key during key rotation.
* The verifier EXPLICITLY defeats the classic ``alg=none``/``alg=HS256``
  algorithm-confusion attack by:
    1. Inspecting the unverified header BEFORE decoding.
    2. Refusing ``alg`` values outside the allow-list ``{RS256, HS256}``.
    3. Routing RS256 tokens to the public key and HS256 tokens to the
       shared secret — never letting an HS256-signed token be verified
       against the RSA public key as HMAC material.
    4. Disabling pyjwt's algorithm inference by passing an explicit single-
       element ``algorithms`` list per branch.

Nothing here logs token bodies. Ever.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta

import jwt

from aether_api.auth.keys import KeyConfigurationError, get_rsa_keypair
from aether_api.core.settings import Settings, get_settings


# -----------------------------------------------------------------------------
# Access JWT — issuance
# -----------------------------------------------------------------------------
def issue_access_token(
    user_id: uuid.UUID,
    *,
    session_id: uuid.UUID | None = None,
    now: datetime | None = None,
) -> str:
    """Issue a short-lived signed JWT carrying the user id as ``sub``.

    The signing algorithm is whatever ``Settings.jwt_algorithm`` resolves to:

    * ``RS256`` (default) — signs with the loaded RSA private key. The token
      carries a ``kid`` header so verifiers can pick the matching public key
      from the JWKS endpoint.
    * ``HS256`` — signs with ``Settings.jwt_secret``. Only used when an
      operator has explicitly pinned the algorithm; the migration path is
      RS256 and HS256 issuance is removed by the cleanup change.

    Optional ``session_id`` is embedded as the ``sid`` claim so endpoints
    that need to identify the caller's session row (e.g. ``/api/me/sessions``
    deriving ``is_current``) can do so without relying on the refresh
    cookie, which is path-scoped to ``/api/auth/refresh`` and won't be
    sent on other paths.
    """
    s = get_settings()
    issued_at = now or datetime.now(tz=UTC)
    expires_at = issued_at + timedelta(minutes=s.access_token_ttl_minutes)
    payload: dict[str, object] = {
        "sub": str(user_id),
        "iat": int(issued_at.timestamp()),
        "exp": int(expires_at.timestamp()),
    }
    if session_id is not None:
        payload["sid"] = str(session_id)

    if s.jwt_algorithm == "RS256":
        keypair = get_rsa_keypair()
        return jwt.encode(
            payload,
            keypair.private_key,
            algorithm="RS256",
            headers={"kid": keypair.kid},
        )
    # HS256 — explicit branch, never inferred. Kept for emergency rollback
    # and parity during the transitional window.
    return jwt.encode(payload, s.jwt_secret, algorithm="HS256")


# -----------------------------------------------------------------------------
# Access JWT — verification
# -----------------------------------------------------------------------------
def verify_access_token(token: str) -> uuid.UUID | None:
    """Verify signature + expiry. Returns the ``sub`` UUID, or ``None`` on failure.

    Failure modes (all return ``None``):

    * Bad signature
    * Expired
    * Malformed
    * Missing ``sub``
    * ``sub`` not a UUID
    * Algorithm not in the allow-list
    * HS256 fallback disabled but token is HS256
    """
    payload = _decode_access_token(token)
    if payload is None:
        return None
    sub = payload.get("sub")
    if not isinstance(sub, str):
        return None
    try:
        return uuid.UUID(sub)
    except ValueError:
        return None


def verify_access_token_session_id(token: str) -> uuid.UUID | None:
    """Return the ``sid`` (session UUID) embedded in the access token, or ``None``.

    Returns ``None`` when the token is invalid OR when no ``sid`` claim is
    present (older tokens minted before session tracking went in). Callers
    that need this for non-critical UX decisions (``is_current`` badge in
    /api/me/sessions) MUST be able to tolerate ``None``.
    """
    payload = _decode_access_token(token)
    if payload is None:
        return None
    sid = payload.get("sid")
    if not isinstance(sid, str):
        return None
    try:
        return uuid.UUID(sid)
    except ValueError:
        return None


# -----------------------------------------------------------------------------
# Internal — verification implementation
# -----------------------------------------------------------------------------
def _decode_access_token(token: str) -> dict[str, object] | None:
    """Decode an access token using the algorithm in its header.

    The ``alg`` header is read with :func:`jwt.get_unverified_header` BEFORE
    any cryptographic operation — purely so we can route the token to the
    correct key. The actual signature check uses an explicit single-element
    ``algorithms`` list, so an attacker swapping ``alg=HS256`` on an
    RS256-shaped header (the classic algorithm-confusion attack) can never
    cause the RSA public key bytes to be misinterpreted as an HMAC secret.
    """
    s = get_settings()
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError:
        return None

    alg = header.get("alg")
    if not isinstance(alg, str):
        return None

    if alg == "RS256":
        return _decode_rs256(token, settings=s)
    if alg == "HS256":
        if not s.jwt_legacy_hs256_verify_enabled:
            # HS256 deliberately disabled — refuse without ever touching the
            # RSA key material. This is the post-cleanup steady state.
            return None
        return _decode_hs256(token, settings=s)
    # Any other algorithm — including ``none``, ``ES256``, etc. — is refused.
    return None


def _decode_rs256(token: str, *, settings: Settings) -> dict[str, object] | None:
    """Verify an RS256 token against the configured public key."""
    try:
        keypair = get_rsa_keypair()
    except KeyConfigurationError:
        # Key material missing/invalid — fail closed.
        return None
    try:
        payload: dict[str, object] = jwt.decode(
            token,
            keypair.public_key,
            algorithms=["RS256"],
            options={"require": ["sub", "iat", "exp"]},
        )
    except jwt.PyJWTError:
        return None
    return payload


def _decode_hs256(token: str, *, settings: Settings) -> dict[str, object] | None:
    """Verify an HS256 token against the shared ``jwt_secret``.

    This branch is the transitional fallback. It is gated by the caller and
    NEVER receives any key material other than the HS256 string secret —
    that guarantee is what makes algorithm confusion impossible.
    """
    try:
        payload: dict[str, object] = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=["HS256"],
            options={"require": ["sub", "iat", "exp"]},
        )
    except jwt.PyJWTError:
        return None
    return payload


# -----------------------------------------------------------------------------
# Refresh token (opaque) — unchanged by the RS256 migration
# -----------------------------------------------------------------------------
def generate_refresh_token() -> tuple[str, str]:
    """Generate ``(raw, sha256_hex)``.

    The raw token is the value placed in the cookie. The hex hash is the
    value persisted in ``sessions.refresh_token_hash``. The raw value
    MUST NOT touch the database or any log.
    """
    raw = secrets.token_urlsafe(32)  # ≈ 43 chars of URL-safe base64
    return raw, hash_refresh_token(raw)


def hash_refresh_token(raw: str) -> str:
    """SHA-256 hex of the raw refresh token. Used for lookups."""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def refresh_token_expiry(*, now: datetime | None = None) -> datetime:
    """Compute ``now + refresh_token_ttl_days`` in UTC."""
    s = get_settings()
    base = now or datetime.now(tz=UTC)
    return base + timedelta(days=s.refresh_token_ttl_days)
