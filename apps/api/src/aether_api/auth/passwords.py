"""Password hashing — argon2id only.

The :class:`PasswordHasher` instance is constructed lazily from
:func:`get_settings` so test setups can override the cost parameters
without monkey-patching this module.

Side note on timing oracles: :func:`verify_password` ALWAYS runs argon2
verification, even against a known-bad hash, so the wall-clock cost of
``user-missing`` and ``user-exists-bad-password`` are indistinguishable.
The caller (login handler) MUST always reach :func:`verify_password`,
even when it had to fabricate a dummy hash because the user did not
exist — see :func:`dummy_hash`.
"""

from __future__ import annotations

from functools import lru_cache

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, VerifyMismatchError

from aether_api.core.settings import get_settings

# A pre-computed argon2id hash of an arbitrary string; constant-cost
# placeholder for the "user does not exist" code path. Computed lazily
# so settings changes still take effect.


@lru_cache(maxsize=1)
def _hasher() -> PasswordHasher:
    s = get_settings()
    return PasswordHasher(
        memory_cost=s.argon2_memory_cost,
        time_cost=s.argon2_time_cost,
        parallelism=s.argon2_parallelism,
    )


@lru_cache(maxsize=1)
def dummy_hash() -> str:
    """Return a hash to verify against when the user does not exist.

    Verifying a real password against this ALWAYS fails, but takes the
    same wall-clock time as verifying against a real user's hash — that
    is what defeats the username-enumeration timing oracle.
    """
    return _hasher().hash("\x00\x00not-a-real-password\x00\x00")


def hash_password(plain: str) -> str:
    """Argon2id-encode a plaintext password. Output includes parameters + salt."""
    return _hasher().hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    """Constant-failure-time verify. Returns False on mismatch; never raises."""
    try:
        return _hasher().verify(hashed, plain)
    except (VerifyMismatchError, VerificationError):
        return False
    except Exception:
        # Any other failure (corrupt hash, unsupported variant) — treat
        # as a verification failure rather than 500ing the request.
        return False
