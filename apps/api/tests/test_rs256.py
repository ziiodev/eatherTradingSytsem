"""RS256 access-token migration tests.

Covers:

* RS256 issuance + verification round-trip.
* HS256 verify-only fallback (transitional).
* JWKS endpoint shape and ``Cache-Control``.
* Tampered tokens are rejected.
* Expired tokens are rejected.
* **Algorithm confusion**: a token forged with ``alg=HS256`` whose HMAC
  secret is the RSA public key MUST be rejected.

These tests are NOT marked ``integration`` — they exercise the in-process
signer/verifier and the JWKS endpoint without ever touching the database.
"""

from __future__ import annotations

import base64
import json
import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from aether_api.auth import tokens as tokens_mod
from aether_api.auth.keys import compute_kid, get_rsa_keypair
from aether_api.auth.tokens import (
    issue_access_token,
    verify_access_token,
    verify_access_token_session_id,
)
from aether_api.core.settings import get_settings
from fastapi.testclient import TestClient


# The shared conftest autouse fixture ``_truncate_mutable_tables`` pulls in a
# Postgres connection on every test — but the RS256 tests are pure-Python
# (in-process signer/verifier + TestClient against the JWKS endpoint). Shadow
# the autouse fixture with a function-scoped no-op so the suite can run
# without ``DATABASE_URL`` / testcontainers / Docker.
@pytest.fixture(autouse=True)
def _truncate_mutable_tables() -> None:  # type: ignore[override]
    return None


# -----------------------------------------------------------------------------
# Issuance
# -----------------------------------------------------------------------------
def test_issued_token_is_rs256_with_kid_header() -> None:
    user_id = uuid.uuid4()
    token = issue_access_token(user_id)

    header = jwt.get_unverified_header(token)
    assert header["alg"] == "RS256"
    assert header["typ"] == "JWT"
    assert header["kid"] == get_rsa_keypair().kid


def test_issued_token_round_trips() -> None:
    user_id = uuid.uuid4()
    session_id = uuid.uuid4()
    token = issue_access_token(user_id, session_id=session_id)

    assert verify_access_token(token) == user_id
    assert verify_access_token_session_id(token) == session_id


def test_issued_token_has_sub_iat_exp() -> None:
    user_id = uuid.uuid4()
    token = issue_access_token(user_id)
    keypair = get_rsa_keypair()
    payload = jwt.decode(token, keypair.public_key, algorithms=["RS256"])

    assert payload["sub"] == str(user_id)
    assert isinstance(payload["iat"], int)
    assert isinstance(payload["exp"], int)
    # exp is iat + access_token_ttl_minutes
    s = get_settings()
    assert payload["exp"] - payload["iat"] == s.access_token_ttl_minutes * 60


# -----------------------------------------------------------------------------
# kid derivation is stable
# -----------------------------------------------------------------------------
def test_kid_is_stable_and_truncated_to_16_chars() -> None:
    kp = get_rsa_keypair()
    again = compute_kid(kp.public_key)
    assert kp.kid == again
    assert len(kp.kid) == 16


# -----------------------------------------------------------------------------
# JWKS endpoint
# -----------------------------------------------------------------------------
def test_jwks_endpoint_shape() -> None:
    # Lazy import so the conftest's session-scoped env fixture has run by the
    # time aether_api.main does its module-level ``create_app()`` call.
    from aether_api.main import create_app

    app = create_app()
    client = TestClient(app)

    resp = client.get("/.well-known/jwks.json")
    assert resp.status_code == 200
    assert resp.headers["cache-control"] == "public, max-age=3600"
    assert resp.headers["content-type"].startswith("application/json")

    body = resp.json()
    assert "keys" in body
    assert len(body["keys"]) == 1

    jwk = body["keys"][0]
    assert jwk["kty"] == "RSA"
    assert jwk["use"] == "sig"
    assert jwk["alg"] == "RS256"
    assert jwk["kid"] == get_rsa_keypair().kid
    # n and e must be base64url-no-pad strings (no '=' / '+' / '/').
    for field in ("n", "e"):
        assert isinstance(jwk[field], str)
        assert "=" not in jwk[field]
        assert "+" not in jwk[field]
        assert "/" not in jwk[field]


# -----------------------------------------------------------------------------
# Tampering / expiry
# -----------------------------------------------------------------------------
def test_tampered_token_is_rejected() -> None:
    user_id = uuid.uuid4()
    token = issue_access_token(user_id)
    # Flip the last char of the signature segment.
    head, payload, sig = token.split(".")
    bad_sig = sig[:-1] + ("A" if sig[-1] != "A" else "B")
    tampered = ".".join([head, payload, bad_sig])

    assert verify_access_token(tampered) is None


def test_expired_token_is_rejected() -> None:
    user_id = uuid.uuid4()
    past = datetime.now(tz=UTC) - timedelta(hours=2)
    token = issue_access_token(user_id, now=past)
    # TTL is 15 min, token issued 2 h ago → exp is firmly in the past.
    assert verify_access_token(token) is None


def test_token_missing_required_claim_is_rejected() -> None:
    keypair = get_rsa_keypair()
    now = datetime.now(tz=UTC)
    # Payload deliberately omits ``sub``.
    bad = jwt.encode(
        {
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(minutes=15)).timestamp()),
        },
        keypair.private_key,
        algorithm="RS256",
        headers={"kid": keypair.kid},
    )
    assert verify_access_token(bad) is None


# -----------------------------------------------------------------------------
# Algorithm-confusion defense
# -----------------------------------------------------------------------------
def test_alg_confusion_hs256_with_public_key_as_hmac_secret_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The classic ``alg=HS256`` confusion attack MUST fail.

    An attacker who can read the JWKS endpoint forges a token claiming
    ``alg=HS256`` and HMAC-signs it with the RSA public key bytes. A naive
    verifier that infers the algorithm from the header would verify the
    forgery against its own public key.

    Our verifier (``_decode_access_token``) explicitly routes RS256 → public
    key + ``algorithms=["RS256"]`` and HS256 → ``jwt_secret`` +
    ``algorithms=["HS256"]``. The HS256 branch NEVER sees the RSA key
    material, so the forgery has no path to success — it either gets
    rejected as a bad HMAC against ``jwt_secret`` or is refused outright
    when HS256 fallback is disabled.

    pyjwt refuses (correctly) to ENCODE with a PEM key as HMAC secret, so we
    forge the token byte-by-byte and feed it to the verifier — the same
    shape an attacker would produce by hand or with a non-pyjwt library.
    """
    import hashlib
    import hmac

    from cryptography.hazmat.primitives import serialization

    keypair = get_rsa_keypair()
    pub_pem = keypair.public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    user_id = uuid.uuid4()
    now = int(datetime.now(tz=UTC).timestamp())
    payload = {"sub": str(user_id), "iat": now, "exp": now + 600}

    def _b64url(raw: bytes) -> str:
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")

    header_b64 = _b64url(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    payload_b64 = _b64url(json.dumps(payload).encode())
    signing_input = f"{header_b64}.{payload_b64}".encode()
    sig = hmac.new(pub_pem, signing_input, hashlib.sha256).digest()
    forged = f"{header_b64}.{payload_b64}.{_b64url(sig)}"

    # With HS256 fallback enabled, the verifier still rejects because
    # ``jwt_secret`` is NOT the public key bytes — the HMAC over jwt_secret
    # produces a totally different signature.
    assert verify_access_token(forged) is None

    # And with HS256 fallback explicitly disabled, the verifier rejects
    # immediately at the header-routing step — no key material consulted.
    monkeypatch.setattr(
        get_settings(), "jwt_legacy_hs256_verify_enabled", False
    )
    assert verify_access_token(forged) is None


def test_none_algorithm_is_rejected() -> None:
    """``alg=none`` MUST be rejected. pyjwt won't sign one for us so we
    hand-craft the token."""
    user_id = uuid.uuid4()
    now = int(datetime.now(tz=UTC).timestamp())
    header = {"alg": "none", "typ": "JWT"}
    payload = {"sub": str(user_id), "iat": now, "exp": now + 600}

    def _b64url(obj: dict) -> str:
        raw = json.dumps(obj, separators=(",", ":")).encode("utf-8")
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")

    forged = ".".join([_b64url(header), _b64url(payload), ""])
    assert verify_access_token(forged) is None


# -----------------------------------------------------------------------------
# HS256 verify-only fallback
# -----------------------------------------------------------------------------
def test_hs256_fallback_accepts_legacy_token_when_enabled() -> None:
    s = get_settings()
    assert s.jwt_legacy_hs256_verify_enabled is True

    user_id = uuid.uuid4()
    now = int(datetime.now(tz=UTC).timestamp())
    payload = {"sub": str(user_id), "iat": now, "exp": now + 600}
    legacy_token = jwt.encode(payload, s.jwt_secret, algorithm="HS256")

    assert verify_access_token(legacy_token) == user_id


def test_hs256_fallback_refused_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    s = get_settings()
    user_id = uuid.uuid4()
    now = int(datetime.now(tz=UTC).timestamp())
    legacy_token = jwt.encode(
        {"sub": str(user_id), "iat": now, "exp": now + 600},
        s.jwt_secret,
        algorithm="HS256",
    )
    # Disable fallback.
    monkeypatch.setattr(s, "jwt_legacy_hs256_verify_enabled", False)
    assert verify_access_token(legacy_token) is None


# -----------------------------------------------------------------------------
# Sanity: tokens_mod re-exported symbols still resolve (regression guard).
# -----------------------------------------------------------------------------
def test_module_exports() -> None:
    assert callable(tokens_mod.issue_access_token)
    assert callable(tokens_mod.verify_access_token)
    assert callable(tokens_mod.verify_access_token_session_id)
    assert callable(tokens_mod.generate_refresh_token)
    assert callable(tokens_mod.hash_refresh_token)
    assert callable(tokens_mod.refresh_token_expiry)
