"""TOTP enrollment, verification, and recovery-code management.

This module is the single place where TOTP cryptographic primitives are
used. Route handlers (``routers/me_mfa.py`` and ``auth/routes.py``) call
the helpers below; they never reach for :mod:`pyotp` or
:mod:`aether_api.services.secret_box` directly. Centralising keeps the
"NUNCA secreto TOTP en claro" charter invariant auditable — grep for
``SecretBox`` and the only hits are here + the secret_box module itself.

What lives here:

* :func:`generate_totp_secret`     — 32 random base32 chars (RFC 6238).
* :func:`provisioning_uri`         — ``otpauth://`` URI for QR encoding.
* :func:`verify_totp`              — ±1 step window, replays NOT prevented
                                     here (the route layer handles
                                     "already-verified" / "session minted").
* :func:`generate_recovery_codes`  — 10 ``secrets.token_urlsafe(16)`` codes.
* :func:`hash_recovery_code`       — argon2id, charter parameters.
* :func:`consume_recovery_code`    — atomic single-use via
                                     ``UPDATE ... WHERE used_at IS NULL
                                     RETURNING id``.
"""

from __future__ import annotations

import secrets
import uuid
from datetime import UTC, datetime

import pyotp
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from aether_api.auth.passwords import hash_password, verify_password
from aether_api.models.mfa_recovery_code import MfaRecoveryCode

# Charter-fixed knobs — codified here so a future change must touch this
# file (and its tests) rather than silently weakening MFA.

#: Issuer label embedded in the otpauth:// URI; surfaces in the user's
#: authenticator app (Google Authenticator, 1Password, Authy, etc.).
TOTP_ISSUER: str = "Aether Trading System"

#: How many ±30s windows the verifier accepts on either side of the
#: server clock. ``1`` (the spec's recommended value) tolerates a phone
#: that's up to 30 s out of sync — enough for any realistic NTP drift,
#: tight enough that a stolen code expires in well under a minute.
TOTP_VALID_WINDOW: int = 1

#: Number of recovery codes minted per regenerate. ``10`` matches the
#: spec.
RECOVERY_CODE_COUNT: int = 10

#: Byte length passed to :func:`secrets.token_urlsafe`. 16 bytes → 22
#: URL-safe-base64 chars, ≥ 128 bits of entropy.
RECOVERY_CODE_BYTES: int = 16


# -----------------------------------------------------------------------------
# TOTP — secret + URI + verification
# -----------------------------------------------------------------------------
def generate_totp_secret() -> str:
    """Return a fresh base32-encoded TOTP secret (RFC 6238 §6).

    pyotp's :func:`pyotp.random_base32` draws 32 base32 chars from
    :mod:`secrets`; 32 chars × 5 bits = 160 bits of entropy, matching
    the upper end of the spec recommendation.
    """
    return pyotp.random_base32()


def provisioning_uri(secret_b32: str, account_name: str) -> str:
    """Build the ``otpauth://totp/...`` URI that Google Authenticator etc. expect.

    ``account_name`` is the user-visible label in the authenticator app —
    we use their email so a power user with several enrolments can tell
    them apart at a glance. The issuer is hard-coded above so QR codes
    are consistent across the deployment.
    """
    return pyotp.TOTP(secret_b32).provisioning_uri(
        name=account_name,
        issuer_name=TOTP_ISSUER,
    )


def verify_totp(secret_b32: str, code: str, *, now: datetime | None = None) -> bool:
    """Constant-time-ish verify against a ±1 step window.

    Returns False on any mismatch; never raises. Whitespace inside the
    code (users sometimes paste ``"123 456"``) is tolerated by stripping
    non-digits — anything else is a malformed code and rejected.

    NOTE: replay prevention ("this code was already accepted") is the
    caller's responsibility. We can't enforce it here without a state
    store, and the route layer already mints (access, refresh) cookies
    on success which closes the practical replay window.
    """
    if not code:
        return False
    cleaned = "".join(ch for ch in code if ch.isdigit())
    if len(cleaned) != 6:
        return False
    when = now or datetime.now(tz=UTC)
    return pyotp.TOTP(secret_b32).verify(
        cleaned,
        for_time=when,
        valid_window=TOTP_VALID_WINDOW,
    )


# -----------------------------------------------------------------------------
# Recovery codes — generation, hashing, single-use consumption
# -----------------------------------------------------------------------------
def generate_recovery_codes(count: int = RECOVERY_CODE_COUNT) -> list[str]:
    """Mint ``count`` plain recovery codes. Returned ONCE to the caller.

    Each code is 22 URL-safe-base64 chars from :mod:`secrets`. The
    caller is responsible for displaying them to the user; we never
    persist plaintext. The returned strings are also fed straight into
    :func:`hash_recovery_code` for DB storage.
    """
    return [secrets.token_urlsafe(RECOVERY_CODE_BYTES) for _ in range(count)]


def hash_recovery_code(code: str) -> str:
    """Argon2id-hash a recovery code with the same parameters as passwords.

    Re-uses :func:`aether_api.auth.passwords.hash_password` so the
    charter floor (``memory_cost=19_456, time_cost=2, parallelism=1``)
    is automatically enforced. Salt is per-row (argon2 default), so two
    rows with the same plaintext still hash to different strings.
    """
    return hash_password(code)


async def store_recovery_codes(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    raw_codes: list[str],
) -> None:
    """Persist hashed recovery codes after wiping the user's previous batch.

    Called from both initial verification and the regenerate endpoint.
    Replacement is atomic at the application layer (delete-all + insert-all
    inside the caller's transaction); ``UNIQUE(user_id, code_hash)`` keeps
    a parallel double-tap from inserting duplicates.
    """
    # Delete-all-then-insert is simpler than a per-row diff and matches
    # the UX ("regenerate" means "the old list is dead").
    await session.execute(
        delete(MfaRecoveryCode).where(MfaRecoveryCode.user_id == user_id)
    )
    for raw in raw_codes:
        session.add(
            MfaRecoveryCode(
                user_id=user_id,
                code_hash=hash_recovery_code(raw),
            )
        )
    await session.flush()


async def consume_recovery_code(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    raw_code: str,
    now: datetime | None = None,
) -> bool:
    """Verify and atomically mark one recovery code as used.

    Algorithm:

    1. Load every UNUSED row for the user (partial index makes this
       cheap — typically ≤ 10 rows).
    2. Argon2-verify the candidate against each row's hash. The hash
       carries its own salt so we cannot pre-compute a single hash to
       compare; per-row verify is the only correct path.
    3. The FIRST matching row is marked used via a single
       ``UPDATE ... WHERE id = :id AND used_at IS NULL RETURNING id``.
       The ``WHERE used_at IS NULL`` clause makes the update a no-op if
       a concurrent request has already consumed the row — the second
       caller sees an empty RETURNING and treats the code as invalid.

    Returns ``True`` exactly when a row was successfully marked used,
    ``False`` otherwise (no match OR a concurrent consumer beat us).
    """
    if not raw_code:
        return False

    when = (now or datetime.now(tz=UTC)).replace(tzinfo=None)

    stmt = select(MfaRecoveryCode).where(
        MfaRecoveryCode.user_id == user_id,
        MfaRecoveryCode.used_at.is_(None),
    )
    result = await session.execute(stmt)
    candidates = result.scalars().all()

    for row in candidates:
        if not verify_password(raw_code, row.code_hash):
            continue
        # Match found — claim it atomically. Two parallel verifiers can
        # both reach this point; the WHERE clause guarantees only one
        # update sticks. The loser sees ``rowcount == 0`` and falls
        # through to ``return False``.
        update_stmt = (
            update(MfaRecoveryCode)
            .where(
                MfaRecoveryCode.id == row.id,
                MfaRecoveryCode.used_at.is_(None),
            )
            .values(used_at=when)
            .returning(MfaRecoveryCode.id)
        )
        claimed = await session.execute(update_stmt)
        # If the RETURNING fetched a row, we claimed this code; if not,
        # a concurrent consumer beat us. Either way, no further rows
        # would match (argon2 salts make a second hit impossible by
        # construction), so we exit the loop right here.
        return claimed.first() is not None
    return False
