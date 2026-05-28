"""Symmetric encryption wrapper for at-rest secrets (TOTP, future credentials).

This module hides the concrete primitive (Fernet today; AWS KMS, GCP KMS,
HashiCorp Vault Transit later) behind a tiny two-method facade so the
swap is mechanical:

    >>> box = SecretBox()
    >>> ref = box.encrypt("JBSWY3DPEHPK3PXP")  # plaintext TOTP secret
    >>> box.decrypt(ref)
    'JBSWY3DPEHPK3PXP'

Today's backend is :class:`cryptography.fernet.Fernet`:

* AES-128-CBC + HMAC-SHA-256 with a 32-byte key (split 16/16 internally).
* Versioned tokens — Fernet packs ``version | timestamp | iv | ciphertext |
  hmac`` so a future key rotation can decrypt-old / encrypt-new without
  changing the storage shape.
* Authenticated — tampered ciphertext raises :class:`InvalidToken` rather
  than silently decrypting to garbage.

KMS / Vault swap path (no call-site changes required):

1. Replace ``_fernet`` with a thin client that calls the remote service.
2. Keep the public methods exactly the same; ``encrypt`` returns an opaque
   string (the KMS "ciphertext blob") and ``decrypt`` consumes it.
3. The settings field changes from ``MFA_SECRET_KEY`` (raw key) to
   ``KMS_KEY_ID`` (resource ARN / Vault key name); update
   :mod:`aether_api.core.settings` and the validator.

Until then, every call routes through the in-process Fernet — no network
hop, no IAM, fast tests.
"""

from __future__ import annotations

from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from aether_api.core.settings import get_settings


class SecretBoxError(RuntimeError):
    """Raised for any encrypt/decrypt failure surfaced to callers.

    Distinguishes "the key is misconfigured" / "the ciphertext is bad"
    from generic ``Exception`` so route handlers can map cleanly to a
    500 (server config) vs 401 (stored ref corrupted, fail closed and
    refuse the MFA flow).
    """


class SecretBox:
    """Fernet-backed encryption helper.

    The instance is cheap — :class:`cryptography.fernet.Fernet` keeps no
    network handles — so callers can construct one per request without
    worrying. We still cache via :func:`_fernet` so the key bytes are
    decoded exactly once per process.
    """

    def encrypt(self, plaintext: str) -> str:
        """Encrypt ``plaintext`` and return the opaque ciphertext reference.

        The returned string is what we store in ``users.mfa_secret_ref``.
        It is URL-safe base64, contains the Fernet version byte and the
        IV, and is opaque to every consumer except :meth:`decrypt` on
        an instance configured with the SAME key.
        """
        try:
            return _fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")
        except Exception as exc:  # pragma: no cover — Fernet.encrypt rarely raises
            raise SecretBoxError("encrypt failed") from exc

    def decrypt(self, token: str) -> str:
        """Reverse :meth:`encrypt`. Raises :class:`SecretBoxError` on tampering.

        Fernet's authenticated decryption raises :class:`InvalidToken` on
        any modification of the ciphertext bytes OR if the wrong key is
        used. We wrap that as :class:`SecretBoxError` so route code can
        map "secret is unreadable" → 401 without leaking the underlying
        crypto library's error names through logs.
        """
        try:
            return _fernet().decrypt(token.encode("ascii")).decode("utf-8")
        except InvalidToken as exc:
            raise SecretBoxError("decrypt failed") from exc
        except Exception as exc:  # pragma: no cover — defensive
            raise SecretBoxError("decrypt failed") from exc


@lru_cache(maxsize=1)
def _fernet() -> Fernet:
    """Construct the Fernet instance lazily from settings.

    Cached so the URL-safe-base64 decode runs exactly once per process.
    Tests that mutate :envvar:`MFA_SECRET_KEY` MUST call
    ``_fernet.cache_clear()`` (mirroring the
    :func:`aether_api.core.settings.get_settings` cache reset pattern).
    """
    settings = get_settings()
    if settings.mfa_secret_key is None:
        raise SecretBoxError(
            "MFA_SECRET_KEY is not configured. Generate one with: "
            'python -c "import secrets,base64;'
            'print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())"'
        )
    key_str = settings.mfa_secret_key.get_secret_value()
    # Fernet accepts the URL-safe-base64 string directly when expressed
    # as bytes; the constructor decodes + validates length itself.
    try:
        return Fernet(key_str.encode("ascii"))
    except (ValueError, TypeError) as exc:
        raise SecretBoxError(
            "MFA_SECRET_KEY is not a valid Fernet key (must be URL-safe "
            "base64 of exactly 32 random bytes)"
        ) from exc
