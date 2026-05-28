"""RSA key material loading + key-id (``kid``) derivation for RS256 JWT signing.

The auth issuer (this service) is the *only* holder of the private key. The
public key is exposed via the JWKS endpoint (see :mod:`aether_api.auth.jwks`)
for sister services (edge middleware, future Worker/Investigator backends) to
verify signatures without ever seeing a shared secret.

Key material comes from settings — either as an inline PEM string
(``JWT_PRIVATE_KEY_PEM`` / ``JWT_PUBLIC_KEY_PEM``) or as a filesystem path
(``JWT_PRIVATE_KEY_PATH`` / ``JWT_PUBLIC_KEY_PATH``). Inline PEM wins when
both are set — convenient for KMS-driven deployments that materialise the key
straight into the process environment.

Nothing here logs key bytes. Ever.
"""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.asymmetric.types import (
    PrivateKeyTypes,
    PublicKeyTypes,
)

from aether_api.core.settings import Settings, get_settings


# -----------------------------------------------------------------------------
# Errors
# -----------------------------------------------------------------------------
class KeyConfigurationError(RuntimeError):
    """Raised at startup when RS256 keys are misconfigured.

    The intent is to FAIL FAST so a misconfigured deployment doesn't silently
    fall back to HS256 (or, worse, issue unsigned tokens).
    """


# -----------------------------------------------------------------------------
# Public dataclass
# -----------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class RSAKeyPair:
    """Loaded RSA keypair + derived ``kid``.

    All consumers (the token signer, the JWKS publisher) read from this single
    object so the ``kid`` stamped into a header always matches the key that
    signed the body.
    """

    private_key: rsa.RSAPrivateKey
    public_key: rsa.RSAPublicKey
    kid: str


# -----------------------------------------------------------------------------
# Loaders
# -----------------------------------------------------------------------------
def _load_private_pem(pem_bytes: bytes) -> rsa.RSAPrivateKey:
    try:
        key: PrivateKeyTypes = serialization.load_pem_private_key(
            pem_bytes, password=None
        )
    except (ValueError, TypeError) as exc:
        raise KeyConfigurationError(f"failed to parse RSA private key PEM: {exc}") from exc
    if not isinstance(key, rsa.RSAPrivateKey):
        raise KeyConfigurationError(
            f"expected RSA private key, got {type(key).__name__}"
        )
    if key.key_size < 2048:
        raise KeyConfigurationError(
            f"RSA private key is {key.key_size} bits — minimum is 2048"
        )
    return key


def _load_public_pem(pem_bytes: bytes) -> rsa.RSAPublicKey:
    try:
        key: PublicKeyTypes = serialization.load_pem_public_key(pem_bytes)
    except (ValueError, TypeError) as exc:
        raise KeyConfigurationError(f"failed to parse RSA public key PEM: {exc}") from exc
    if not isinstance(key, rsa.RSAPublicKey):
        raise KeyConfigurationError(
            f"expected RSA public key, got {type(key).__name__}"
        )
    if key.key_size < 2048:
        raise KeyConfigurationError(
            f"RSA public key is {key.key_size} bits — minimum is 2048"
        )
    return key


def _read_pem(
    *,
    inline: str | None,
    path: Path | None,
    field_label: str,
) -> bytes:
    """Resolve a PEM payload from inline string OR file path.

    Inline wins when both are set — that matches the KMS-injected-env path
    we expect in production. Missing both raises :class:`KeyConfigurationError`.
    """
    if inline is not None:
        # SecretStr → str happens at the call-site; we only see plain str/None here.
        return inline.encode("utf-8")
    if path is not None:
        if not path.exists():
            raise KeyConfigurationError(
                f"{field_label}: file not found at {path}"
            )
        try:
            return path.read_bytes()
        except OSError as exc:
            raise KeyConfigurationError(
                f"{field_label}: cannot read {path}: {exc}"
            ) from exc
    raise KeyConfigurationError(
        f"{field_label}: neither PEM string nor file path is configured"
    )


# -----------------------------------------------------------------------------
# kid derivation
# -----------------------------------------------------------------------------
def _b64url_no_pad(data: bytes) -> str:
    """RFC 7515 base64url WITHOUT padding."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _int_to_b64url(n: int) -> str:
    """Encode an unsigned int as RFC 7518 §6.3.1 ``n``/``e`` field."""
    # Round up to a multiple of 8 bits then base64url-no-pad.
    byte_len = (n.bit_length() + 7) // 8 or 1
    return _b64url_no_pad(n.to_bytes(byte_len, "big"))


def public_jwk(public_key: rsa.RSAPublicKey, *, kid: str) -> dict[str, str]:
    """Serialise an RSA public key as a JWK (RFC 7517 §4)."""
    numbers = public_key.public_numbers()
    return {
        "kty": "RSA",
        "use": "sig",
        "alg": "RS256",
        "kid": kid,
        "n": _int_to_b64url(numbers.n),
        "e": _int_to_b64url(numbers.e),
    }


def compute_kid(public_key: rsa.RSAPublicKey) -> str:
    """Derive a stable ``kid`` from the public key.

    Algorithm: build the canonical JWK members (``kty``, ``n``, ``e`` — the
    fields RFC 7638 mandates for an RSA thumbprint), serialise as JSON with
    sorted keys and no whitespace, SHA-256, base64url-no-pad, then truncate
    to 16 chars. 16 chars of base64url ≈ 96 bits of namespace — far more
    than enough to keep two keys from colliding within one issuer.
    """
    numbers = public_key.public_numbers()
    # RFC 7638 canonical members for RSA: e, kty, n — sorted alphabetically
    # by json.dumps(sort_keys=True).
    canonical = json.dumps(
        {
            "e": _int_to_b64url(numbers.e),
            "kty": "RSA",
            "n": _int_to_b64url(numbers.n),
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    digest = hashlib.sha256(canonical).digest()
    return _b64url_no_pad(digest)[:16]


# -----------------------------------------------------------------------------
# Public API: load + cache the configured keypair
# -----------------------------------------------------------------------------
def _settings_inline(value: Any) -> str | None:
    """Unwrap a ``SecretStr`` (or plain ``str``) into a raw PEM string.

    Returns ``None`` when the field is unset. Anything else gets ``str()``'d
    via ``.get_secret_value()`` when it's a ``SecretStr``.
    """
    if value is None:
        return None
    get_secret = getattr(value, "get_secret_value", None)
    if callable(get_secret):
        return str(get_secret())
    return str(value)


def load_rsa_keypair(settings: Settings | None = None) -> RSAKeyPair:
    """Load the configured RS256 keypair from settings.

    Raises :class:`KeyConfigurationError` if the algorithm is RS256 but the
    keys are absent / malformed / mismatched in size.
    """
    s = settings or get_settings()

    private_pem = _read_pem(
        inline=_settings_inline(s.jwt_private_key_pem),
        path=s.jwt_private_key_path,
        field_label="JWT_PRIVATE_KEY",
    )
    public_pem = _read_pem(
        inline=_settings_inline(s.jwt_public_key_pem),
        path=s.jwt_public_key_path,
        field_label="JWT_PUBLIC_KEY",
    )

    private_key = _load_private_pem(private_pem)
    public_key = _load_public_pem(public_pem)

    # Sanity check: the public key derived from the private key must match the
    # configured public key. A mismatch means JWKS would publish a verifier
    # that can't verify our signatures — the worst kind of silent breakage.
    derived_pub_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    configured_pub_pem = public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    if derived_pub_pem != configured_pub_pem:
        raise KeyConfigurationError(
            "configured public key does not match the private key"
        )

    kid = compute_kid(public_key)
    return RSAKeyPair(private_key=private_key, public_key=public_key, kid=kid)


@lru_cache(maxsize=1)
def get_rsa_keypair() -> RSAKeyPair:
    """Return the process-wide RSA keypair, loaded lazily on first access.

    Mirrors :func:`get_settings` — tests that swap key material in/out should
    call ``get_rsa_keypair.cache_clear()`` after rewriting the env.
    """
    return load_rsa_keypair()
